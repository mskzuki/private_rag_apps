from typing import List, Dict, Any
from langfuse import observe, get_client
from private_rag_apps.core.config import settings
from private_rag_apps.generation.llm_client import get_llm_client
from private_rag_apps.prompts.rag import build_context_text
from private_rag_apps.prompts.routing import GROUNDED_SYSTEM_PROMPT, DIRECT_SYSTEM_PROMPT
from private_rag_apps.prompts.condense import CONDENSE_SYSTEM_PROMPT, build_condense_prompt


@observe(as_type="generation")
def condense(query: str, history_messages: List[Dict[str, str]]) -> str:
    """会話履歴を踏まえ、ユーザーの最新の質問を自己完結したクエリに書き換える"""
    if not history_messages:
        return query

    history_text = "\n".join(
        [
            f"{msg['role']}: {msg['content']}"
            for msg in history_messages[-settings.condense_history_turns * 2 :]
        ]
    )

    client = get_llm_client()
    prompt = build_condense_prompt(history_text, query)

    try:
        # Ollama(Qwen3.5等の推論モデル)は既定でthinkingを行い、条件次第でmax_output_tokensを
        # 思考だけで使い切り回答が空になることがあるため、reasoningを無効化する(実機検証で確認)
        extra_kwargs: Dict[str, Any] = (
            {"reasoning": {"effort": "none"}} if settings.llm_provider == "ollama" else {}
        )
        response = client.responses.create(
            model=settings.condense_model,
            max_output_tokens=256,
            instructions=CONDENSE_SYSTEM_PROMPT,
            input=prompt,
            **extra_kwargs,
        )

        if response.usage is not None:
            get_client().update_current_generation(
                usage_details={
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens,
                },
                model=settings.condense_model,
            )
        return response.output_text.strip() or query
    except Exception as e:
        print(f"Condense error: {e}")
        return query  # Fallback


def _stream_llm_tokens(*, model: str, instructions: str, input_text: str, max_output_tokens: int):
    """OpenAI互換Responses APIのstreamingレスポンスからtokenイベントを逐次yieldし、
    完了時にusageをLangfuseへ記録する共通ロジック（grounded/direct 両経路で共有。
    generate_answer_stream / generate_direct_answer_stream から呼ばれる）。
    LLM呼び出し失敗時はerrorイベントを1件yieldして終了する（既存のフォールバック方針を踏襲）"""
    client = get_llm_client()
    try:
        extra_kwargs: Dict[str, Any] = (
            {"reasoning": {"effort": "none"}} if settings.llm_provider == "ollama" else {}
        )
        stream = client.responses.create(
            model=model,
            max_output_tokens=max_output_tokens,
            instructions=instructions,
            input=input_text,
            stream=True,
            **extra_kwargs,
        )

        final_response = None
        for event in stream:
            if event.type == "response.output_text.delta":
                yield {"event": "token", "data": event.delta}
            elif event.type == "response.completed":
                final_response = event.response

        if final_response is not None and final_response.usage is not None:
            get_client().update_current_generation(
                usage_details={
                    "input": final_response.usage.input_tokens,
                    "output": final_response.usage.output_tokens,
                },
                model=model,
            )
    except Exception as e:
        yield {"event": "error", "data": str(e)}


@observe(as_type="generation")
def generate_answer_stream(query: str, context_chunks: List[Dict[str, Any]]):
    """grounded経路: 取得したコンテキスト情報に基づき、引用付きで回答をストリーミング形式で
    生成するジェネレータ（コンテキストのカバー外の内容は GROUNDED_SYSTEM_PROMPT の指示により
    「一般知識に基づく補足」セクションに分離される。スペック rev.3 §4.3 generate grounded）"""
    if not context_chunks:
        yield {"event": "token", "data": "該当する情報が見つかりませんでした。"}
        yield {"event": "citations", "data": []}
        return

    citations = []
    for i, chunk in enumerate(context_chunks, 1):
        citations.append(
            {
                "n": i,
                "title": chunk["title"],
                "path": chunk["path"],
                "heading": chunk.get("metadata", {}).get("heading", ""),
                "chunk_id": chunk["chunk_id"],
            }
        )

    yield {"event": "citations", "data": citations}

    context_text = build_context_text(context_chunks)
    user_prompt = f"コンテキスト情報:\n{context_text}\n\n質問: {query}"

    yield from _stream_llm_tokens(
        model=settings.llm_model,
        instructions=GROUNDED_SYSTEM_PROMPT,
        input_text=user_prompt,
        max_output_tokens=1024,
    )


@observe(as_type="generation")
def generate_direct_answer_stream(query: str):
    """direct経路: コンテキスト注入なしで、一般知識のみに基づき回答をストリーミング形式で
    生成するジェネレータ（DIRECT_SYSTEM_PROMPTによりコーパスへの言及を避ける。
    スペック rev.3 §4.3 generate direct）。
    citationsは常に空（contextを使わないため生成しようがない）だが、SSEイベント契約を
    grounded経路と揃えるため、常に最初にcitations(空リスト)をyieldする"""
    yield {"event": "citations", "data": []}
    yield from _stream_llm_tokens(
        model=settings.llm_model,
        instructions=DIRECT_SYSTEM_PROMPT,
        input_text=query,
        max_output_tokens=1024,
    )
