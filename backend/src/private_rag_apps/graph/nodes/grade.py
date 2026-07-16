"""grade ノード: rerank score による関連度判定(THETA足切り)
(docs/specs/m7_adaptive_routing.md rev.3 §4.3 grade)。

LLM を使わない純関数。state["retrieved"](既存 retrieval パイプラインが返す、
rerank score 降順の全チャンク)から rerank_score >= THETA のチャンクのみを
kept に残し、kept が1件以上あれば route="grounded"、0件なら route="direct" とする。

    kept = [c for c in retrieved if c.rerank_score >= THETA]
    route = "direct" if len(kept) == 0 else "grounded"

**設計上の注意(後任への申し送り。重要):** grade はこの閾値による足切り判定のみを行う。
「質問がcontextでどこまでカバーできるか」というカバレッジ判定は、grade ではなく
generate 時のLLM(grounded プロンプトの指示)の責務である
(スペック §2 grounded、§4.3 generate。rev.2での設計変更: 数値閾値では
質問へのカバレッジは測定できないため)。**grade にカバレッジ判定ロジックを
追加してはならない。** grade は最後まで「rerank_scoreとTHETAの比較のみを行う純関数」
であり続けるべきである。

T6（スペック §5.2）でノード開始時の `node_start` と、grade 完了時の `route_decided`
を get_stream_writer() 経由で送出するようになった。`route_decided` の `top_score` は
`evals/routing.py` の定義（retrieved 先頭chunkのrerank_score。retrieved が空ならNone）
と同一の意味で計算する。
"""

from typing import Any, Literal

from langgraph.config import get_stream_writer

from private_rag_apps.core.config import settings
from private_rag_apps.graph.state import GraphState


def grade(state: GraphState) -> dict[str, Any]:
    writer = get_stream_writer()
    writer({"event": "node_start", "data": {"node": "grade"}})

    theta = settings.routing_theta
    retrieved = state.get("retrieved", [])

    # rerank_score を持たないチャンク(Voyageリランク失敗時のRRFフォールバック。
    # retrieval/searcher.py::_rerank() 参照)は、閾値未満とはみなさず kept として扱う
    # (`.get("rerank_score", theta)` により欠落時は theta 自身を使うため必ず通過する)。
    # これは誤判定コストの非対称性(スペック §3.1「迷ったらgroundedに倒す」)に基づく
    # 安全側のデフォルトであり、コンテキストカバレッジの判定ではない。
    kept = [c for c in retrieved if c.get("rerank_score", theta) >= theta]
    route: Literal["grounded", "direct"] = "direct" if not kept else "grounded"

    # top_score: retrieved(rerank_score降順)の先頭chunkのrerank_score。
    # retrieved が空ならNone(evals/routing.pyのtop_score定義と同一。T6ブリーフ補足3)
    top_score = retrieved[0].get("rerank_score") if retrieved else None

    writer(
        {
            "event": "route_decided",
            "data": {
                "route": route,
                "kept": len(kept),
                "dropped": len(retrieved) - len(kept),
                "top_score": top_score,
            },
        }
    )

    return {"kept": kept, "route": route}
