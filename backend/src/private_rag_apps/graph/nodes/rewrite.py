"""rewrite ノード: 会話履歴を考慮した検索クエリ生成
(docs/specs/m7_adaptive_routing.md rev.3 §4.3 rewrite)。

新規実装ではなく、既存の generation.condense() を呼ぶだけの薄いラッパー（新規ロジックを
持たない。スペック §3.3「LangGraph は薄く使う」）。LLM呼び出し・履歴が空の場合の
pass-through・LLM失敗時のフォールバック（search_query = user_query で継続、警告ログ +
Langfuse記録）は condense() 側に実装済みであり、ここでは繰り返さない。

M7 T5 でグラフに配線されたことにより、api/main.py がグラフの外から condense() を
直接呼んでいた暫定例外（AGENTS.md §3）は解消され、rewrite は他ノード同様
graph 経由の呼び出しに一本化される。

T6（スペック §5.2）でノード開始時の `node_start` と、rewrite 完了時の `rewrite_result`
（UI でのデバッグ表示用）を get_stream_writer() 経由で送出するようになった。

T7（スペック §6）で `rewrite_applied` を trace レベルの Langfuse metadata として記録する
ようになった。span 計装自体は不要（`condense()` が既に `@observe(as_type="generation")`
済みで、このノードが呼ぶ時点で自動的に trace 配下の子 span になるため。grade.py の
コメント参照）。
"""

from typing import Any, Dict, List, cast

from langfuse import propagate_attributes
from langgraph.config import get_stream_writer

from private_rag_apps.generation.generator import condense
from private_rag_apps.graph.state import GraphState


def rewrite(state: GraphState) -> dict[str, Any]:
    writer = get_stream_writer()
    writer({"event": "node_start", "data": {"node": "rewrite"}})

    # Message(TypedDict) は plain dict と実行時には同一だが、List は不変(invariant)なため
    # 既存の condense(query, List[Dict[str, str]]) とは型上one castが必要
    # (graph/nodes/generate.pyのcast(List[Dict[str, Any]], ...)と対称の変換)
    history = cast(List[Dict[str, str]], state.get("history", []))
    search_query, rewrite_applied = condense(state["user_query"], history)

    # trace レベルの metadata(スペック §6)。route/theta/kept_count/top_score は grade 側で記録
    with propagate_attributes(metadata={"rewrite_applied": rewrite_applied}):
        pass

    writer(
        {
            "event": "rewrite_result",
            "data": {"applied": rewrite_applied, "query": search_query},
        }
    )
    return {"search_query": search_query, "rewrite_applied": rewrite_applied}
