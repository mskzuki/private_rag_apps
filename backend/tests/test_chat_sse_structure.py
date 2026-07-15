"""T3 構造検証（スペック rev.3 §5.3、タスクT3ブリーフ作業項目5/7）。

LLM生成は非決定的なため実LLMでのペイロードdiff比較はしない。代わりに、生成をstub
（固定文字列を返すmockクライアント）に差し替えた統合テストで、/api/chat のSSEイベント型の
系列・各イベントのJSONスキーマ・順序が、グラフ導入前の現行実装と同一であることを検証する
（現行実装の期待値は test_api.py::test_chat_bulk_save_and_history 等の既存テストと
generation/generator.py の実装から確定しているものをここに明示的にキャプチャする）。

get_llm_client をstubに差し替えて generate_answer_stream を実行させることで、
retrieveノード → generateノード → get_stream_writer → graph.astream(stream_mode="custom")
→ FastAPIハンドラのSSE変換、という実際の配線を丸ごと通す。
"""

import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from private_rag_apps.api.main import app
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.models.rag import Conversation

client = TestClient(app)


def _parse_sse(content: str) -> list[tuple[str, str]]:
    """SSEボディを (event, data) のペアの列にパースする（順序を保持）"""
    events: list[tuple[str, str]] = []
    current_event = None
    for line in content.split("\n"):
        if line.startswith("event: "):
            current_event = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            assert current_event is not None
            events.append((current_event, line[len("data: ") :].strip()))
    return events


def _make_stub_llm_client() -> MagicMock:
    """generate_answer_stream が呼ぶ OpenAI Responses API 互換のstubクライアント。
    固定文字列 "Hello" " " "World" をdeltaとして流す（実LLMは使わない）"""
    mock_client = MagicMock()

    delta_events = []
    for text in ("Hello", " ", "World"):
        event = MagicMock()
        event.type = "response.output_text.delta"
        event.delta = text
        delta_events.append(event)

    final_response = MagicMock()
    final_response.usage.input_tokens = 10
    final_response.usage.output_tokens = 5

    completed_event = MagicMock()
    completed_event.type = "response.completed"
    completed_event.response = final_response

    mock_client.responses.create.return_value = delta_events + [completed_event]
    return mock_client


@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
@patch("private_rag_apps.generation.generator.get_llm_client")
def test_chat_sse_event_sequence_and_schema_match_pre_graph_capture(
    mock_get_llm_client: MagicMock, mock_retrieve_context: MagicMock
) -> None:
    """generate_answer_stream / retrieve_context 自体はmockしない（stubはLLM境界の
    get_llm_client のみ）。グラフ経由のプラミングを実際に通した上で、
    現行実装のキャプチャ（イベント型の系列・schema・順序）と比較する"""
    mock_retrieve_context.return_value = [
        {
            "chunk_id": "c1",
            "content": "text",
            "metadata": {"heading": "h1"},
            "title": "Doc1",
            "path": "p1.md",
        }
    ]
    mock_get_llm_client.return_value = _make_stub_llm_client()

    db = SessionLocal()
    conv = Conversation()
    db.add(conv)
    db.commit()
    db.refresh(conv)
    conv_id = str(conv.id)
    db.close()

    try:
        response = client.post(
            "/api/chat", json={"message": "test question", "conversation_id": conv_id}
        )
        assert response.status_code == 200

        events = _parse_sse(response.content.decode("utf-8"))
        event_types = [e for e, _ in events]

        # 現行実装のキャプチャ: citations(1) -> token(3) -> done(1)。
        # T3では新規SSEイベント(node_start/route_decided/rewrite_result)は一切追加しない
        assert event_types == ["citations", "token", "token", "token", "done"], event_types

        # citations の JSON スキーマ: リスト内の各要素が n/title/path/heading/chunk_id を持つ
        citations_payload = json.loads(events[0][1])
        assert isinstance(citations_payload, list)
        assert citations_payload[0].keys() == {"n", "title", "path", "heading", "chunk_id"}
        assert citations_payload[0]["title"] == "Doc1"

        # token の JSON スキーマ: 素の文字列がそのままJSONエンコードされている
        token_payloads = [json.loads(d) for e, d in events if e == "token"]
        assert token_payloads == ["Hello", " ", "World"]

        # done の JSON スキーマ: message_id/conversation_id を持つ
        done_payload = json.loads(events[-1][1])
        assert done_payload.keys() == {"message_id", "conversation_id"}
        assert done_payload["conversation_id"] == conv_id
    finally:
        db = SessionLocal()
        db.query(Conversation).filter(Conversation.id == conv_id).delete()
        db.commit()
        db.close()


@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
@patch("private_rag_apps.graph.nodes.generate.generate_answer_stream")
def test_chat_sse_ignores_unknown_event_type_from_node(
    mock_generate_stream: MagicMock, mock_retrieve_context: MagicMock
) -> None:
    """タスクT3作業項目7: 未知イベント型が(将来的に)ノードから流れても、
    FastAPIハンドラは既存の if/elif 分岐に無いイベントを黙殺し、
    クラッシュせず200で完走することを確認する（default-ignore。
    フロント側(chat-adapter.ts)も同型のif/elif構造でdefault-ignoreになっていることは
    コードレビューで確認済み。タスクノート参照）"""
    mock_retrieve_context.return_value = [
        {"chunk_id": "c1", "content": "text", "title": "Doc1", "path": "p1.md"}
    ]

    def mock_stream(*args: object, **kwargs: object):
        yield {"event": "citations", "data": [{"n": 1, "title": "Doc1"}]}
        # T6で追加予定の未知イベント型を先取りでシミュレート(T3スコープでは実際には発火しない)
        yield {"event": "node_start", "data": {"node": "generate"}}
        yield {"event": "token", "data": "Hello"}

    mock_generate_stream.side_effect = mock_stream

    db = SessionLocal()
    conv = Conversation()
    db.add(conv)
    db.commit()
    db.refresh(conv)
    conv_id = str(conv.id)
    db.close()

    try:
        response = client.post(
            "/api/chat", json={"message": "test question", "conversation_id": conv_id}
        )
        assert response.status_code == 200

        events = _parse_sse(response.content.decode("utf-8"))
        event_types = [e for e, _ in events]

        # 未知イベント(node_start)はSSEフレームとして送出されない(サーバ側で黙殺)
        assert "node_start" not in event_types
        assert event_types == ["citations", "token", "done"]
    finally:
        db = SessionLocal()
        db.query(Conversation).filter(Conversation.id == conv_id).delete()
        db.commit()
        db.close()
