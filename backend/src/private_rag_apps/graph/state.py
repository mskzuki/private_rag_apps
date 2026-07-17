"""LangGraph の状態定義（docs/specs/m7_adaptive_routing.md rev.3 §4.2）。

State は JSON シリアライズ可能な TypedDict に限定する。DB コネクション・HTTP クライアント・
Langfuse オブジェクト等は含めない（ノード関数がクロージャ/DI で保持する。スペック §3.4。
将来の checkpointer（PostgresSaver、M8）導入時に局所変更で差し込めるようにするための制約）。

T3時点では retrieve → generate の2ノードのみが State を更新していた。T4で grade
（kept/route）・generate の route 分岐が加わり、T5で rewrite ノードが追加され
`START → rewrite → retrieve → grade → generate` の配線になった（graph/builder.py 参照）。
LangGraph の
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
    source_type: str
    source_id: str | None
    source_url: str | None


class Citation(TypedDict):
    """回答に付与する出典情報（既存 generate_answer_stream の citations イベント payload 形式）。
    M9 T6で source_type/source_id/source_url を追加（スペック §4.7）。
    source_url はスペック §4.7 のフロントエンド分岐説明（`c.source_url` を使う）から
    citation payload 自体が保持する必要があると判断し、当初のタスクブリーフの省略された
    フィールド列挙（source_type/source_idのみ）に対して追加した（フロントは
    GET /api/sources を別途叩かず、citation payload 自体が必要な情報を全て持つ、という
    スペック§4.7の明示チェーンの制約に従うため）。既存5フィールドと同様、generator は
    常に8フィールド全てを一度に構築するため total=False にはしない（既存フィールドの
    「常にセットで揃っている」という契約を新フィールドにも適用する）"""

    n: int
    title: str
    path: str
    heading: str
    chunk_id: str
    source_type: str
    source_id: str | None
    source_url: str | None


class GraphState(TypedDict, total=False):
    # input（ハンドラ層が組み立てる）
    conversation_id: str
    user_query: str
    history: list[Message]

    # rewrite の出力（graph/nodes/rewrite.py。history が空なら search_query = user_query）
    search_query: str
    rewrite_applied: bool

    # retrieve / grade の出力
    retrieved: list[ScoredChunk]
    kept: list[ScoredChunk]
    route: Literal["grounded", "direct"]

    # generate の出力
    citations: list[Citation]
