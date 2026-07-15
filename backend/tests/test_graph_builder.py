import asyncio
from unittest.mock import MagicMock, patch

import pytest

from private_rag_apps.core.config import settings
from private_rag_apps.graph.builder import build_graph


async def _run_graph(graph: object, initial_state: dict[str, object]) -> list[dict[str, object]]:
    events = []
    async for event in graph.astream(initial_state, stream_mode="custom"):  # type: ignore[attr-defined]
        events.append(event)
    return events


@patch("private_rag_apps.graph.nodes.generate.generate_answer_stream")
@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
def test_build_graph_wires_retrieve_grade_generate_grounded_route(
    mock_retrieve_context: MagicMock, mock_generate_stream: MagicMock
) -> None:
    """retrieve → grade → generate のグラフが正しい順序で実行され、
    generate ノード内で get_stream_writer() に渡したイベントが
    graph.astream(stream_mode="custom") でそのまま観測できること（スペック §5.1）。
    rerank_score が無いchunk(既存retrieve_contextモックの形)はgrade側で
    kept扱いになるため grounded 経路になる(graph/nodes/grade.pyの安全側デフォルト)"""
    mock_retrieve_context.return_value = [{"chunk_id": "c1", "title": "T1", "path": "p.md"}]
    mock_generate_stream.return_value = iter(
        [
            {"event": "citations", "data": [{"n": 1, "title": "T1"}]},
            {"event": "token", "data": "Hello"},
            {"event": "token", "data": " World"},
        ]
    )

    db = MagicMock()
    graph = build_graph(db)

    events = asyncio.run(
        _run_graph(graph, {"conversation_id": "c", "user_query": "raw question", "history": []})
    )

    # retrieve ノードが user_query をそのまま検索クエリとして使ったこと
    mock_retrieve_context.assert_called_once_with(db, query="raw question")
    # generate ノードは grade を通過した kept(この場合はretrieved全件)を
    # context_chunks として受け取ること
    mock_generate_stream.assert_called_once_with(
        "raw question", [{"chunk_id": "c1", "title": "T1", "path": "p.md"}]
    )

    assert events == [
        {"event": "citations", "data": [{"n": 1, "title": "T1"}]},
        {"event": "token", "data": "Hello"},
        {"event": "token", "data": " World"},
    ]


@patch("private_rag_apps.graph.nodes.generate.generate_answer_stream")
@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
def test_build_graph_grade_drops_low_score_chunks_before_generate(
    mock_retrieve_context: MagicMock, mock_generate_stream: MagicMock
) -> None:
    """THETA未満のchunkはgradeでkeptから除外され、generateにはkeptのみが渡ること"""
    mock_retrieve_context.return_value = [
        {"chunk_id": "high", "rerank_score": 0.9},
        {"chunk_id": "low", "rerank_score": 0.1},
    ]
    mock_generate_stream.return_value = iter([{"event": "citations", "data": []}])

    db = MagicMock()
    graph = build_graph(db)
    asyncio.run(_run_graph(graph, {"conversation_id": "c", "user_query": "q", "history": []}))

    mock_generate_stream.assert_called_once_with("q", [{"chunk_id": "high", "rerank_score": 0.9}])


@patch("private_rag_apps.graph.nodes.generate.generate_direct_answer_stream")
@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
def test_build_graph_routes_to_direct_when_no_chunk_meets_theta(
    mock_retrieve_context: MagicMock, mock_generate_direct_stream: MagicMock
) -> None:
    """全chunkがTHETA未満(またはretrievedが空)の場合、conditional edgeでdirect経路に入り、
    generate_direct_answer_stream が query のみで呼ばれること(contextは渡さない)"""
    mock_retrieve_context.return_value = [{"chunk_id": "c1", "rerank_score": 0.1}]
    mock_generate_direct_stream.return_value = iter(
        [{"event": "citations", "data": []}, {"event": "token", "data": "general answer"}]
    )

    db = MagicMock()
    graph = build_graph(db)
    events = asyncio.run(
        _run_graph(graph, {"conversation_id": "c", "user_query": "general question", "history": []})
    )

    mock_generate_direct_stream.assert_called_once_with("general question")
    assert events == [
        {"event": "citations", "data": []},
        {"event": "token", "data": "general answer"},
    ]


@patch("private_rag_apps.graph.nodes.generate.generate_direct_answer_stream")
@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
def test_build_graph_routes_to_direct_when_retrieved_is_empty(
    mock_retrieve_context: MagicMock,
    mock_generate_direct_stream: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """検索結果が0件の場合もdirect経路になること(スペック §2 direct: kept==0の場合)"""
    monkeypatch.setattr(settings, "routing_theta", 0.56)
    mock_retrieve_context.return_value = []
    mock_generate_direct_stream.return_value = iter([{"event": "citations", "data": []}])

    db = MagicMock()
    graph = build_graph(db)
    asyncio.run(_run_graph(graph, {"conversation_id": "c", "user_query": "q", "history": []}))

    mock_generate_direct_stream.assert_called_once_with("q")
