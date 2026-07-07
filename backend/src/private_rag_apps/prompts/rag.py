RAG_SYSTEM_PROMPT = """あなたはアシスタントです。ユーザーの質問に対して、提供されたコンテキスト情報のみに基づいて回答してください。
回答の各主張には、必ずコンテキスト情報の出典番号を `[n]` の形式で付与してください。
提供されたコンテキスト情報の中に質問の答えが見つからない場合は、推測せず「該当する情報が見つかりませんでした」と回答してください。
回答は日本語で行ってください。"""

def build_context_text(chunks: list[dict]) -> str:
    context_lines = []
    for i, chunk in enumerate(chunks, 1):
        context_lines.append(f"[{i}] {chunk['title']}\n{chunk['content']}")
    return "\n\n".join(context_lines)
