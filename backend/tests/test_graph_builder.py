import asyncio
from unittest.mock import MagicMock, patch

from private_rag_apps.graph.builder import build_graph


@patch("private_rag_apps.graph.nodes.generate.generate_answer_stream")
@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
def test_build_graph_wires_retrieve_then_generate_and_streams_custom_events(
    mock_retrieve_context: MagicMock, mock_generate_stream: MagicMock
) -> None:
    """retrieve → generate の pass-through グラフが正しい順序で実行され、
    generate ノード内で get_stream_writer() に渡したイベントが
    graph.astream(stream_mode="custom") でそのまま観測できること（スペック §5.1）"""
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

    async def _run() -> list[dict[str, object]]:
        events = []
        async for event in graph.astream(
            {"conversation_id": "c", "user_query": "raw question", "history": []},
            stream_mode="custom",
        ):
            events.append(event)
        return events

    events = asyncio.run(_run())

    # retrieve ノードが user_query をそのまま検索クエリとして使ったこと
    mock_retrieve_context.assert_called_once_with(db, query="raw question")
    # generate ノードは retrieve の出力(retrieved)を context_chunks として受け取ること
    mock_generate_stream.assert_called_once_with(
        "raw question", [{"chunk_id": "c1", "title": "T1", "path": "p.md"}]
    )

    assert events == [
        {"event": "citations", "data": [{"n": 1, "title": "T1"}]},
        {"event": "token", "data": "Hello"},
        {"event": "token", "data": " World"},
    ]
