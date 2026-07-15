"""LangGraph の状態定義（docs/specs/m7_adaptive_routing.md rev.3 §4.2）。

State は JSON シリアライズ可能な TypedDict に限定する。DB コネクション・HTTP クライアント・
Langfuse オブジェクト等は含めない（ノード関数がクロージャ/DI で保持する。スペック §3.4。
将来の checkpointer（PostgresSaver、M8）導入時に局所変更で差し込めるようにするための制約）。

T3時点では retrieve → generate の2ノードのみが State を更新していた。T4で grade
（kept/route）・generate の route 分岐が加わった（rewrite は T5 で追加予定。それまでは
retrieve ノードが user_query をそのまま検索クエリとして使う pass-through）。LangGraph の
ノードは自身が担当するキーのみを部分的な dict として返し、フレームワーク側で既存 State
にマージする方式のため、全フィールドを total=False として部分的な State 構築・更新を
型として許容する。
"""

from typing import Any, Literal, TypedDict


class Message(TypedDict):
    """会話履歴の1メッセージ（DB からロード済みの plain dict 表現）"""

    role: str
    content: str


class ScoredChunk(TypedDict, total=False):
    """retrieval が返すチャンク（既存 retrieve_context の出力形と同じ plain dict）。
    rerank_score は `retrieval/searcher.py::_rerank()` が付与する Voyage rerank-2.5 の
    relevance_score（0〜1、降順。スペック §4.3 grade 前提。T4で実装）。リランクAPI呼び出し
    失敗時のRRFフォールバック経路では付与されない場合がある（grade側で欠落時はkept扱いにする
    安全側デフォルトを持つ。graph/nodes/grade.py参照）"""

    chunk_id: str
    content: str
    metadata: dict[str, Any]
    title: str
    path: str
    rerank_score: float


class Citation(TypedDict):
    """回答に付与する出典情報（既存 generate_answer_stream の citations イベント payload 形式）"""

    n: int
    title: str
    path: str
    heading: str
    chunk_id: str


class GraphState(TypedDict, total=False):
    # input（ハンドラ層が組み立てる）
    conversation_id: str
    user_query: str
    history: list[Message]

    # rewrite の出力（T5 で実装。それまでは retrieve ノードが user_query をそのまま透過する）
    search_query: str
    rewrite_applied: bool

    # retrieve / grade の出力
    retrieved: list[ScoredChunk]
    kept: list[ScoredChunk]
    route: Literal["grounded", "direct"]

    # generate の出力
    citations: list[Citation]
