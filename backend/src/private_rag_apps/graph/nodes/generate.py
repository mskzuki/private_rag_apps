"""generate ノード: 既存の生成ロジック（generate_answer_stream）をそのまま呼び出す
（docs/specs/m7_adaptive_routing.md rev.3 §4.3 generate、§5.1）。

T3 時点では grade/経路分岐が未実装（T4）のため、route による分岐は行わず、現行と
同一の呼び出し（query, context_chunks のみ。履歴は渡さない）を行う。既存 SDK ラッパー・
prompt cache 制御・Langfuse 計装は generate_answer_stream 側の実装をそのまま維持する
（LangGraph は状態機械・イベント伝搬のみを担う。スペック §3.3）。

トークン等のイベントは get_stream_writer() で stream_mode="custom" に流す
（`astream_events` は使わない。スペック §5.1）。
"""

from typing import Any, Dict, List, cast

from langgraph.config import get_stream_writer

from private_rag_apps.generation.generator import generate_answer_stream
from private_rag_apps.graph.state import GraphState


def generate(state: GraphState) -> dict[str, Any]:
    writer = get_stream_writer()
    query = state["search_query"]
    # ScoredChunk(TypedDict) は plain dict と実行時には同一だが、List は不変(invariant)なため
    # 既存の generate_answer_stream(List[Dict[str, Any]]) とは型上one castが必要
    context_chunks = cast(List[Dict[str, Any]], state.get("retrieved", []))

    citations: list[dict[str, Any]] = []
    for event in generate_answer_stream(query, context_chunks):
        writer(event)
        if event["event"] == "citations":
            citations = event["data"]

    return {"citations": citations}
