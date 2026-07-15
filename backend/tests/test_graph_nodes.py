from unittest.mock import MagicMock, patch

from private_rag_apps.graph.nodes.generate import generate
from private_rag_apps.graph.nodes.retrieve import make_retrieve_node
from private_rag_apps.graph.state import GraphState


@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
def test_retrieve_node_uses_user_query_as_search_query(mock_retrieve_context: MagicMock) -> None:
    """T3 時点では rewrite ノードが無いため、search_query には user_query をそのまま使う
    （タスク T3 補足コンテキスト#1、スペック rev.3 §4.3 retrieve）"""
    mock_retrieve_context.return_value = [{"chunk_id": "c1"}]
    mock_db = MagicMock()

    node = make_retrieve_node(mock_db)
    result = node({"user_query": "raw question", "conversation_id": "c", "history": []})

    mock_retrieve_context.assert_called_once_with(mock_db, query="raw question")
    assert result == {"search_query": "raw question", "retrieved": [{"chunk_id": "c1"}]}


@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
def test_retrieve_node_binds_distinct_db_sessions_per_call(mock_retrieve_context: MagicMock) -> None:
    """db セッションはノード関数のクロージャで保持し、State には含めない（スペック §3.4）。
    リクエストごとに異なる db を束縛できることを確認する"""
    mock_retrieve_context.return_value = []
    db_a = MagicMock(name="db_a")
    db_b = MagicMock(name="db_b")

    make_retrieve_node(db_a)({"user_query": "q"})
    make_retrieve_node(db_b)({"user_query": "q"})

    assert mock_retrieve_context.call_args_list[0].args[0] is db_a
    assert mock_retrieve_context.call_args_list[1].args[0] is db_b


@patch("private_rag_apps.graph.nodes.generate.get_stream_writer")
@patch("private_rag_apps.graph.nodes.generate.generate_answer_stream")
def test_generate_node_streams_events_and_returns_citations(
    mock_generate_stream: MagicMock, mock_get_writer: MagicMock
) -> None:
    """既存の generate_answer_stream をそのまま呼び出し、各イベントを
    get_stream_writer() 経由でそのまま流す（プロンプト・SDK ラッパーは変更しない）"""
    written: list[dict[str, object]] = []
    mock_get_writer.return_value = written.append

    mock_generate_stream.return_value = iter(
        [
            {"event": "citations", "data": [{"n": 1, "title": "T1"}]},
            {"event": "token", "data": "Hello"},
            {"event": "token", "data": " World"},
        ]
    )

    state: GraphState = {"search_query": "q", "retrieved": [{"chunk_id": "c1"}]}
    result = generate(state)

    mock_generate_stream.assert_called_once_with("q", [{"chunk_id": "c1"}])
    assert written == [
        {"event": "citations", "data": [{"n": 1, "title": "T1"}]},
        {"event": "token", "data": "Hello"},
        {"event": "token", "data": " World"},
    ]
    assert result == {"citations": [{"n": 1, "title": "T1"}]}


@patch("private_rag_apps.graph.nodes.generate.get_stream_writer")
@patch("private_rag_apps.graph.nodes.generate.generate_answer_stream")
def test_generate_node_no_citations_event_keeps_citations_empty(
    mock_generate_stream: MagicMock, mock_get_writer: MagicMock
) -> None:
    """citations イベントが来ない場合（例: error のみ）でも citations は空リストのまま
    返す（KeyError を起こさないことの確認）"""
    written: list[dict[str, object]] = []
    mock_get_writer.return_value = written.append
    mock_generate_stream.return_value = iter([{"event": "error", "data": "boom"}])

    state: GraphState = {"search_query": "q", "retrieved": []}
    result = generate(state)

    assert result == {"citations": []}
