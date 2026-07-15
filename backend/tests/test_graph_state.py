import json

from private_rag_apps.graph.state import GraphState


def test_graph_state_full_is_json_serializable() -> None:
    """State は JSON シリアライズ可能な TypedDict に限定する（スペック §3.4）。
    checkpointer 導入（M8）に備えた制約の検証。全フィールドを埋めても
    json.dumps が通ることを確認する（Pydantic モデル・コネクション類が混入していないこと）"""
    state: GraphState = {
        "conversation_id": "11111111-1111-1111-1111-111111111111",
        "user_query": "RRFを採用した理由は？",
        "history": [{"role": "user", "content": "こんにちは"}],
        "search_query": "RRFを採用した理由は？",
        "rewrite_applied": False,
        "retrieved": [
            {
                "chunk_id": "c1",
                "content": "text",
                "metadata": {"heading": "h1"},
                "title": "T1",
                "path": "p1.md",
                "rerank_score": 0.8,
            }
        ],
        "kept": [],
        "route": "grounded",
        "citations": [
            {"n": 1, "title": "T1", "path": "p1.md", "heading": "h1", "chunk_id": "c1"}
        ],
    }

    serialized = json.dumps(state, ensure_ascii=False)
    assert json.loads(serialized) == state


def test_graph_state_partial_construction_is_json_serializable() -> None:
    """T3 時点では rewrite/grade ノードが存在しないため、retrieve/generate が扱う
    キーのみを持つ部分的な State でも構築・シリアライズできること
    （LangGraph のノードは部分的な dict を返してマージされるため total=False とした）"""
    state: GraphState = {
        "conversation_id": "conv-1",
        "user_query": "query",
        "history": [],
    }
    assert json.loads(json.dumps(state)) == state
