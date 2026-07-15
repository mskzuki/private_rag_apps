"""グラフ組み立て（docs/specs/m7_adaptive_routing.md rev.3 §4.1）。

T3 時点では retrieve → generate のみの pass-through グラフとする
（grade / rewrite / 経路分岐は T4/T5 で追加）。

グラフは 1 リクエスト = 1 実行のステートレスな関数として扱い、checkpointer は使わない
（スペック §3.2）。db セッションは retrieve ノードのクロージャで保持し State には
含めないため（§3.4）、リクエストごとに `build_graph(db)` を呼んでグラフを都度構築する。
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.orm import Session

from private_rag_apps.graph.nodes.generate import generate
from private_rag_apps.graph.nodes.retrieve import make_retrieve_node
from private_rag_apps.graph.state import GraphState


def build_graph(db: Session) -> CompiledStateGraph:
    """retrieve → generate の 2 ノードグラフをコンパイルして返す"""
    graph = StateGraph(GraphState)
    graph.add_node("retrieve", make_retrieve_node(db))
    graph.add_node("generate", generate)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
