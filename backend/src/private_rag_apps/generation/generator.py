import anthropic
from typing import List, Dict, Any
from langfuse.decorators import observe, langfuse_context
from private_rag_apps.core.config import settings
from private_rag_apps.prompts.rag import RAG_SYSTEM_PROMPT, build_context_text

@observe(as_type="generation")
def generate_answer(query: str, context_chunks: List[Dict[str, Any]]):
    if not context_chunks:
        return {
            "content": "該当する情報が見つかりませんでした。",
            "citations": []
        }

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    context_text = build_context_text(context_chunks)
    user_prompt = f"コンテキスト情報:\n{context_text}\n\n質問: {query}"

    response = client.messages.create(
        model=settings.llm_model,
        max_tokens=1024,
        system=RAG_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}]
    )
    
    answer_text = response.content[0].text
    
    # Langfuse metrics
    langfuse_context.update_current_observation(
        usage={
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens
        },
        model=settings.llm_model
    )

    if "見つかりませんでした" in answer_text and len(answer_text) < 50:
         return {"content": answer_text, "citations": []}

    citations = []
    for i, chunk in enumerate(context_chunks, 1):
        citations.append({
            "n": i,
            "title": chunk["title"],
            "path": chunk["path"],
            "heading": chunk.get("metadata", {}).get("heading", ""),
            "chunk_id": chunk["chunk_id"]
        })

    return {"content": answer_text, "citations": citations}
