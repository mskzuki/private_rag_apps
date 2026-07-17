from unittest.mock import MagicMock, patch
from private_rag_apps.core.config import settings
from private_rag_apps.generation.generator import generate_answer_stream, condense


def test_generate_answer_stream_no_chunks():
    generator = generate_answer_stream("query", [])
    events = list(generator)

    assert len(events) == 2
    assert events[0]["event"] == "token"
    assert "見つかりませんでした" in events[0]["data"]

    assert events[1]["event"] == "citations"
    assert events[1]["data"] == []


@patch("private_rag_apps.generation.generator.get_llm_client")
def test_generate_answer_stream_with_chunks(mock_get_llm_client):
    # Mock OpenAI Responses API streaming response
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
    mock_get_llm_client.return_value = mock_client

    chunks = [
        {"title": "T1", "path": "p1.md", "chunk_id": "c1", "content": "mock text"},
    ]

    generator = generate_answer_stream("query", chunks)
    events = list(generator)

    # 1 citation, 3 tokens
    assert len(events) == 4
    assert events[0]["event"] == "citations"
    assert len(events[0]["data"]) == 1
    assert events[0]["data"][0]["title"] == "T1"

    assert events[1]["event"] == "token"
    assert events[1]["data"] == "Hello"

    assert events[2]["event"] == "token"
    assert events[2]["data"] == " "

    assert events[3]["event"] == "token"
    assert events[3]["data"] == "World"


@patch("private_rag_apps.generation.generator.get_llm_client")
def test_generate_answer_stream_citation_source_type_defaults_to_local_fs_when_missing(
    mock_get_llm_client,
):
    """M9 T6: _format_chunks を経由していないchunk dict（source_type等のキーを
    持たない旧形式）でも citations 組み立てがKeyErrorせず、Source/Documentモデル
    自身の既定値と同じ 'local_fs' にフォールバックすることを確認する（スペック §4.7）"""
    mock_get_llm_client.return_value = _stub_llm_client()

    chunks = [
        {"title": "T1", "path": "p1.md", "chunk_id": "c1", "content": "mock text"},
    ]

    events = list(generate_answer_stream("query", chunks))

    citation = events[0]["data"][0]
    assert citation["source_type"] == "local_fs"
    assert citation["source_id"] is None
    assert citation["source_url"] is None
    # 既存5フィールドは不変
    assert citation["n"] == 1
    assert citation["title"] == "T1"
    assert citation["path"] == "p1.md"
    assert citation["chunk_id"] == "c1"


@patch("private_rag_apps.generation.generator.get_llm_client")
def test_generate_answer_stream_citation_carries_drive_source_fields(mock_get_llm_client):
    """M9 T6: _format_chunks が付与した source_type='google_drive'/external_id/source_url
    が citations にそのまま反映されることを確認する（external_idはsource_idという
    キー名にリネームされて渡る。スペック §4.7）"""
    mock_get_llm_client.return_value = _stub_llm_client()

    chunks = [
        {
            "title": "Drive Doc",
            "path": "Notes/drive-doc.md",
            "chunk_id": "c1",
            "content": "mock text",
            "source_type": "google_drive",
            "external_id": "drv-abc123",
            "source_url": "https://drive.google.com/file/d/drv-abc123/view",
        },
    ]

    events = list(generate_answer_stream("query", chunks))

    citation = events[0]["data"][0]
    assert citation["source_type"] == "google_drive"
    assert citation["source_id"] == "drv-abc123"
    assert citation["source_url"] == "https://drive.google.com/file/d/drv-abc123/view"
    # 既存5フィールドは不変
    assert citation["title"] == "Drive Doc"
    assert citation["path"] == "Notes/drive-doc.md"


def _stub_llm_client() -> MagicMock:
    """OpenAI Responses API互換のstub（固定のtoken列を流すのみ）。
    citations組み立てのテストで本文トークンの中身自体は検証対象外のため、
    test_chat_sse_structure.py::_make_stub_llm_client と同型の最小stub"""
    mock_client = MagicMock()
    event = MagicMock()
    event.type = "response.output_text.delta"
    event.delta = "Hello"

    final_response = MagicMock()
    final_response.usage.input_tokens = 10
    final_response.usage.output_tokens = 5

    completed_event = MagicMock()
    completed_event.type = "response.completed"
    completed_event.response = final_response

    mock_client.responses.create.return_value = [event, completed_event]
    return mock_client


def test_condense_empty_history():
    # Should skip condense if history is empty. M7 T5: condense() は
    # (search_query, rewrite_applied) のタプルを返す(スペック rev.3 §4.3 rewrite)
    result = condense("My query", [])
    assert result == ("My query", False)


@patch("private_rag_apps.generation.generator.get_llm_client")
def test_condense_with_history(mock_get_llm_client):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.output_text = "Condensed query"
    mock_response.usage.input_tokens = 10
    mock_response.usage.output_tokens = 5
    mock_client.responses.create.return_value = mock_response
    mock_get_llm_client.return_value = mock_client

    history = [
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "A programming language."},
    ]

    result = condense("Why is it good?", history)
    assert result == ("Condensed query", True)

    # M7 T5: eval再現性のため temperature=0 を明示指定する(スペック §3.5)
    _, kwargs = mock_client.responses.create.call_args
    assert kwargs["temperature"] == 0


@patch("private_rag_apps.generation.generator.get_llm_client")
def test_condense_output_identical_to_query_is_not_flagged_as_rewrite(mock_get_llm_client):
    """LLMが書き換え不要と判断し元のクエリをそのまま返した場合、rewrite_applied は False
    (スペック §4.3 rewrite: 「書き換え不要と判定した場合は user_query をそのまま通す」)"""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.output_text = "Why is it good?"
    mock_response.usage = None
    mock_client.responses.create.return_value = mock_response
    mock_get_llm_client.return_value = mock_client

    history = [
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "A programming language."},
    ]

    result = condense("Why is it good?", history)
    assert result == ("Why is it good?", False)


@patch("private_rag_apps.generation.generator.get_client")
@patch("private_rag_apps.generation.generator.get_llm_client")
def test_condense_llm_failure_falls_back_to_query(mock_get_llm_client, mock_get_client):
    """T5 完了条件: フォールバック動作のテスト(LLM呼び出しをmockで失敗させる)。
    LLM呼び出し失敗時は search_query = user_query で継続し、rewrite_applied は False。
    警告はLangfuseにも記録する(スペック §4.3 rewrite フォールバック。
    retrieval/searcher.py::_rerank() の rerank失敗時と同様のWARNING記録パターンを踏襲)"""
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = RuntimeError("LLM unavailable")
    mock_get_llm_client.return_value = mock_client
    mock_langfuse_client = MagicMock()
    mock_get_client.return_value = mock_langfuse_client

    history = [
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "A programming language."},
    ]

    result = condense("Why is it good?", history)

    assert result == ("Why is it good?", False)
    mock_langfuse_client.update_current_generation.assert_called_once()
    _, kwargs = mock_langfuse_client.update_current_generation.call_args
    assert kwargs["level"] == "WARNING"
    assert "LLM unavailable" in kwargs["status_message"]


@patch("private_rag_apps.generation.llm_client.openai.OpenAI")
def test_get_llm_client_openai(mock_openai, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    from private_rag_apps.generation.llm_client import get_llm_client

    get_llm_client()
    mock_openai.assert_called_once_with(api_key="sk-test")


@patch("private_rag_apps.generation.llm_client.openai.OpenAI")
def test_get_llm_client_ollama(mock_openai, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "ollama_api_key", "ollama")
    from private_rag_apps.generation.llm_client import get_llm_client

    get_llm_client()
    mock_openai.assert_called_once_with(api_key="ollama", base_url="http://localhost:11434/v1")


@patch("private_rag_apps.generation.generator.get_llm_client")
def test_condense_with_history_ollama_disables_reasoning(mock_get_llm_client, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.output_text = "Condensed query"
    mock_response.usage.input_tokens = 10
    mock_response.usage.output_tokens = 5
    mock_client.responses.create.return_value = mock_response
    mock_get_llm_client.return_value = mock_client

    history = [
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "A programming language."},
    ]

    result = condense("Why is it good?", history)
    assert result == ("Condensed query", True)

    _, kwargs = mock_client.responses.create.call_args
    assert kwargs["reasoning"] == {"effort": "none"}
    assert kwargs["max_output_tokens"] == 256


def test_condense_empty_output_falls_back_to_query():
    with patch("private_rag_apps.generation.generator.get_llm_client") as mock_get_llm_client:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = "   "
        mock_response.usage = None
        mock_client.responses.create.return_value = mock_response
        mock_get_llm_client.return_value = mock_client

        history = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "A programming language."},
        ]

        result = condense("Why is it good?", history)
        assert result == ("Why is it good?", False)


@patch("private_rag_apps.generation.generator.get_llm_client")
def test_generate_answer_stream_ollama_missing_usage(mock_get_llm_client, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    mock_client = MagicMock()

    delta_events = []
    for text in ("Hello", " ", "World"):
        event = MagicMock()
        event.type = "response.output_text.delta"
        event.delta = text
        delta_events.append(event)

    final_response = MagicMock()
    final_response.usage = None

    completed_event = MagicMock()
    completed_event.type = "response.completed"
    completed_event.response = final_response

    mock_client.responses.create.return_value = delta_events + [completed_event]
    mock_get_llm_client.return_value = mock_client

    chunks = [
        {"title": "T1", "path": "p1.md", "chunk_id": "c1", "content": "mock text"},
    ]

    generator = generate_answer_stream("query", chunks)
    events = list(generator)

    assert [e["event"] for e in events] == ["citations", "token", "token", "token"]
    assert [e["data"] for e in events[1:]] == ["Hello", " ", "World"]

    _, kwargs = mock_client.responses.create.call_args
    assert kwargs["reasoning"] == {"effort": "none"}
    assert kwargs["max_output_tokens"] == 1024


@patch("private_rag_apps.generation.generator.get_llm_client")
def test_generate_answer_stream_uses_grounded_system_prompt(mock_get_llm_client):
    """M7 T4: grounded経路は既存RAGプロンプトを基礎にした補足書式ルール入りの
    GROUNDED_SYSTEM_PROMPTを使う（スペック rev.3 §4.3 generate grounded）"""
    from private_rag_apps.prompts.routing import GROUNDED_SYSTEM_PROMPT

    mock_client = MagicMock()
    completed_event = MagicMock()
    completed_event.type = "response.completed"
    completed_event.response.usage = None
    mock_client.responses.create.return_value = [completed_event]
    mock_get_llm_client.return_value = mock_client

    chunks = [{"title": "T1", "path": "p1.md", "chunk_id": "c1", "content": "mock text"}]
    list(generate_answer_stream("query", chunks))

    _, kwargs = mock_client.responses.create.call_args
    assert kwargs["instructions"] == GROUNDED_SYSTEM_PROMPT


class TestGenerateDirectAnswerStream:
    """direct経路の生成: コンテキスト注入なし、コーパスに言及しないシステムプロンプトを使う
    （スペック rev.3 §4.3 generate direct）"""

    @patch("private_rag_apps.generation.generator.get_llm_client")
    def test_streams_tokens_with_empty_citations_and_no_context(self, mock_get_llm_client):
        from private_rag_apps.generation.generator import generate_direct_answer_stream
        from private_rag_apps.prompts.routing import DIRECT_SYSTEM_PROMPT

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
        mock_get_llm_client.return_value = mock_client

        generator = generate_direct_answer_stream("Pythonのwalrus operatorとは?")
        events = list(generator)

        # citations(空) -> token x3 の順(SSE契約はgroundedと同じ形にする)
        assert events[0] == {"event": "citations", "data": []}
        assert [e["event"] for e in events[1:]] == ["token", "token", "token"]
        assert [e["data"] for e in events[1:]] == ["Hello", " ", "World"]

        _, kwargs = mock_client.responses.create.call_args
        assert kwargs["instructions"] == DIRECT_SYSTEM_PROMPT
        assert kwargs["input"] == "Pythonのwalrus operatorとは?"
        assert kwargs["model"] == settings.llm_model
        assert kwargs["stream"] is True

    @patch("private_rag_apps.generation.generator.get_llm_client")
    def test_ollama_disables_reasoning(self, mock_get_llm_client, monkeypatch):
        from private_rag_apps.generation.generator import generate_direct_answer_stream

        monkeypatch.setattr(settings, "llm_provider", "ollama")
        mock_client = MagicMock()
        completed_event = MagicMock()
        completed_event.type = "response.completed"
        completed_event.response.usage = None
        mock_client.responses.create.return_value = [completed_event]
        mock_get_llm_client.return_value = mock_client

        list(generate_direct_answer_stream("query"))

        _, kwargs = mock_client.responses.create.call_args
        assert kwargs["reasoning"] == {"effort": "none"}

    @patch("private_rag_apps.generation.generator.get_llm_client")
    def test_llm_error_yields_error_event(self, mock_get_llm_client):
        from private_rag_apps.generation.generator import generate_direct_answer_stream

        mock_client = MagicMock()
        mock_client.responses.create.side_effect = RuntimeError("boom")
        mock_get_llm_client.return_value = mock_client

        events = list(generate_direct_answer_stream("query"))

        assert events[0] == {"event": "citations", "data": []}
        assert events[1]["event"] == "error"
        assert "boom" in events[1]["data"]
