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


def test_condense_empty_history():
    # Should skip condense if history is empty
    result = condense("My query", [])
    assert result == "My query"


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
    assert result == "Condensed query"


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
    assert result == "Condensed query"

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
        assert result == "Why is it good?"


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
