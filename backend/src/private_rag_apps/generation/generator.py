from typing import List, Dict, Any
from langfuse import observe, get_client
from private_rag_apps.core.config import settings
from private_rag_apps.generation.llm_client import get_llm_client
from private_rag_apps.prompts.rag import RAG_SYSTEM_PROMPT, build_context_text
from private_rag_apps.prompts.condense import CONDENSE_SYSTEM_PROMPT, build_condense_prompt

@observe(as_type="generation")
def condense(query: str, history_messages: List[Dict[str, str]]) -> str:
    """会話履歴を踏まえ、ユーザーの最新の質問を自己完結したクエリに書き換える"""
    if not history_messages:
        return query

    history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history_messages[-settings.condense_history_turns*2:]])

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
            **extra_kwargs
        )

        if response.usage is not None:
            get_client().update_current_generation(
                usage_details={
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens
                },
                model=settings.condense_model
            )
        return response.output_text.strip() or query
    except Exception as e:
        print(f"Condense error: {e}")
        return query # Fallback

@observe(as_type="generation")
def generate_answer_stream(query: str, context_chunks: List[Dict[str, Any]]):
    """取得したコンテキスト情報に基づき、回答をストリーミング形式で生成するジェネレータ"""
    if not context_chunks:
        yield {"event": "token", "data": "該当する情報が見つかりませんでした。"}
        yield {"event": "citations", "data": []}
        return

    citations = []
    for i, chunk in enumerate(context_chunks, 1):
        citations.append({
            "n": i,
            "title": chunk["title"],
            "path": chunk["path"],
            "heading": chunk.get("metadata", {}).get("heading", ""),
            "chunk_id": chunk["chunk_id"]
        })
    
    yield {"event": "citations", "data": citations}

    client = get_llm_client()
    context_text = build_context_text(context_chunks)
    user_prompt = f"コンテキスト情報:\n{context_text}\n\n質問: {query}"

    try:
        extra_kwargs: Dict[str, Any] = (
            {"reasoning": {"effort": "none"}} if settings.llm_provider == "ollama" else {}
        )
        stream = client.responses.create(
            model=settings.llm_model,
            max_output_tokens=1024,
            instructions=RAG_SYSTEM_PROMPT,
            input=user_prompt,
            stream=True,
            **extra_kwargs
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
                    "output": final_response.usage.output_tokens
                },
                model=settings.llm_model
            )
    except Exception as e:
        yield {"event": "error", "data": str(e)}
