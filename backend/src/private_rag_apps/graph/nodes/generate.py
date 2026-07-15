"""generate ノード: state["route"] に応じて grounded/direct のプロンプト・生成関数を切り替える
（docs/specs/m7_adaptive_routing.md rev.3 §4.3 generate、§5.1）。

generate は1ノードとし、2ノードに分割しない（プロンプト以外のロジックが共通のため。スペック §4.1）。
- grounded: state["kept"](THETA足切り後のchunk。retrievedの全件ではない)をcontextとして
  generate_answer_stream に渡す
- direct: contextを渡さず、query のみで generate_direct_answer_stream を呼ぶ

いずれも既存の generation/generator.py の関数をそのまま呼び出す。既存 SDK ラッパー・
prompt cache 制御・Langfuse 計装は generate_answer_stream/generate_direct_answer_stream
側の実装をそのまま維持する（LangGraph は状態機械・イベント伝搬のみを担う。スペック §3.3）。

トークン等のイベントは get_stream_writer() で stream_mode="custom" に流す
（`astream_events` は使わない。スペック §5.1）。T4ではSSE新規イベント（node_start等。T6スコープ）
は追加しない。
"""

from typing import Any, Dict, List, cast

from langgraph.config import get_stream_writer

from private_rag_apps.generation.generator import (
    generate_answer_stream,
    generate_direct_answer_stream,
)
from private_rag_apps.graph.state import GraphState


def generate(state: GraphState) -> dict[str, Any]:
    writer = get_stream_writer()
    query = state["search_query"]
    # route未設定時はgrounded扱い(誤判定コストの非対称性。スペック §3.1「迷ったらgroundedに倒す」)。
    # 実際のグラフではgradeが必ず先行するため通常は発生しない防御的デフォルト
    route = state.get("route", "grounded")

    if route == "direct":
        stream = generate_direct_answer_stream(query)
    else:
        # ScoredChunk(TypedDict) は plain dict と実行時には同一だが、List は不変(invariant)なため
        # 既存の generate_answer_stream(List[Dict[str, Any]]) とは型上one castが必要
        kept_chunks = cast(List[Dict[str, Any]], state.get("kept", []))
        stream = generate_answer_stream(query, kept_chunks)

    citations: list[dict[str, Any]] = []
    for event in stream:
        writer(event)
        if event["event"] == "citations":
            citations = event["data"]

    return {"citations": citations}
