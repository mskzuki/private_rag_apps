"""グラフ組み立て（docs/specs/m7_adaptive_routing.md rev.3 §4.1）。

rewrite → retrieve → grade → (conditional edge) → generate のグラフとする
（T5 で rewrite ノードを追加。会話履歴を踏まえた検索クエリ書き換えを retrieve より
先に実行する。rewrite は既存 generation.condense() を呼ぶだけの薄いラッパーで、
history が空の場合は LLM を呼ばず user_query をそのまま search_query として通す）。

grade の後段は conditional edge で分岐するが、grounded/direct いずれも同じ
"generate" ノードに合流する（generate は1ノードでprompt切り替えのみ行う。
プロンプト以外のロジックが共通のため2ノードに分割しない。スペック §4.1）。
ここで conditional edge を使うのは、スペック §4.1 のグラフ図が明示的に
grade からの分岐を conditional edge として描いているためであり、経路ごとの
将来的な拡張（例: 別ノードへの分割）に備えた設計意図を示す目的もある。

グラフは 1 リクエスト = 1 実行のステートレスな関数として扱い、checkpointer は使わない
（スペック §3.2）。db セッションは retrieve ノードのクロージャで保持し State には
含めないため（§3.4）、リクエストごとに `build_graph(db)` を呼んでグラフを都度構築する。
"""

from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.orm import Session

from private_rag_apps.graph.nodes.generate import generate
from private_rag_apps.graph.nodes.grade import grade
from private_rag_apps.graph.nodes.retrieve import make_retrieve_node
from private_rag_apps.graph.nodes.rewrite import rewrite
from private_rag_apps.graph.state import GraphState


def _route_after_grade(state: GraphState) -> Literal["grounded", "direct"]:
    """grade が決定した state["route"] を読むだけの conditional edge 用セレクタ
    （route未設定時はgroundedにフォールバック。スペック §3.1「迷ったらgroundedに倒す」）"""
    return state.get("route", "grounded")


def build_graph(db: Session) -> CompiledStateGraph:
    """rewrite → retrieve → grade → (conditional) → generate のグラフをコンパイルして返す"""
    graph = StateGraph(GraphState)
    graph.add_node("rewrite", rewrite)
    graph.add_node("retrieve", make_retrieve_node(db))
    graph.add_node("grade", grade)
    graph.add_node("generate", generate)
    graph.add_edge(START, "rewrite")
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        _route_after_grade,
        {"grounded": "generate", "direct": "generate"},
    )
    graph.add_edge("generate", END)
    return graph.compile()
