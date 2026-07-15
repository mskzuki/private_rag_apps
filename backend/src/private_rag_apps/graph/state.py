"""LangGraph の状態定義（docs/specs/m7_adaptive_routing.md rev.3 §4.2）。

State は JSON シリアライズ可能な TypedDict に限定する。DB コネクション・HTTP クライアント・
Langfuse オブジェクト等は含めない（ノード関数がクロージャ/DI で保持する。スペック §3.4。
将来の checkpointer（PostgresSaver、M8）導入時に局所変更で差し込めるようにするための制約）。

T3 時点では retrieve → generate の 2 ノードのみが State を更新する（rewrite/grade は未実装。
T5/T4 でそれぞれ追加予定）。LangGraph のノードは自身が担当するキーのみを部分的な dict として
返し、フレームワーク側で既存 State にマージする方式のため、全フィールドを total=False として
部分的な State 構築・更新を型として許容する。
"""

from typing import Any, Literal, TypedDict


class Message(TypedDict):
    """会話履歴の1メッセージ（DB からロード済みの plain dict 表現）"""

    role: str
    content: str


class ScoredChunk(TypedDict, total=False):
    """retrieval が返すチャンク（既存 retrieve_context の出力形と同じ plain dict）。
    rerank_score は T4 で `retrieval/searcher.py::_rerank()` に追加される予定のフィールド
    （スペック §4.3 grade 前提）。T3 時点では未設定"""

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

    # rewrite の出力（T5 で実装。T3 では retrieve ノードが user_query をそのまま透過する）
    search_query: str
    rewrite_applied: bool

    # retrieve / grade の出力（grade は T4 で実装。T3 では kept / route は未使用）
    retrieved: list[ScoredChunk]
    kept: list[ScoredChunk]
    route: Literal["grounded", "direct"]

    # generate の出力
    citations: list[Citation]
