"""retrieve ノード: 既存 retrieval サービス（retrieve_context）の薄いラッパー
（docs/specs/m7_adaptive_routing.md rev.3 §4.3 retrieve）。

M7 では retrieval 内部（hybrid search → RRF → rerank）に一切変更を加えない。
T3 時点では rewrite ノードが未実装（T5）のため、検索クエリには State の user_query を
そのまま使う（rewrite が実装されるまでは search_query == user_query）。

db セッションはノード関数のクロージャで保持し、State には含めない
（DB コネクションを State に入れない。スペック §3.4）。
"""

from typing import Any, Protocol

from sqlalchemy.orm import Session

from private_rag_apps.graph.state import GraphState
from private_rag_apps.retrieval.searcher import retrieve_context


class RetrieveNode(Protocol):
    """retrieve ノード関数の型。`Callable[[GraphState], dict[str, Any]]` と等価だが、
    LangGraph の `StateGraph.add_node` の overload 解決が Callable エイリアスの
    factory 戻り値をうまく単一化できない（mypy/langgraph-stubs 側の既知の制約）ため、
    Protocol で明示的に型を与えて overload を解決させる"""

    def __call__(self, state: GraphState) -> dict[str, Any]: ...


def make_retrieve_node(db: Session) -> RetrieveNode:
    """db セッションを束縛した retrieve ノード関数を生成する（1 リクエストごとに呼ぶ）"""

    def retrieve(state: GraphState) -> dict[str, Any]:
        search_query = state["user_query"]
        retrieved = retrieve_context(db, query=search_query)
        return {"search_query": search_query, "retrieved": retrieved}

    return retrieve
