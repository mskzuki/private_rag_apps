from unittest.mock import MagicMock, patch
from private_rag_apps.generation.generator import generate_answer_stream, condense

def test_generate_answer_stream_no_chunks():
    generator = generate_answer_stream("query", [])
    events = list(generator)
    
    assert len(events) == 2
    assert events[0]["event"] == "token"
    assert "見つかりませんでした" in events[0]["data"]
    
    assert events[1]["event"] == "citations"
    assert events[1]["data"] == []

@patch("private_rag_apps.generation.generator.anthropic.Anthropic")
def test_generate_answer_stream_with_chunks(mock_anthropic):
    # Mock Anthropic streaming response
    mock_client = MagicMock()
    mock_stream = MagicMock()
    
    mock_stream.__enter__.return_value = mock_stream
    mock_stream.text_stream = ["Hello", " ", "World"]
    
    mock_final_message = MagicMock()
    mock_final_message.usage.input_tokens = 10
    mock_final_message.usage.output_tokens = 5
    mock_stream.get_final_message.return_value = mock_final_message
    
    mock_client.messages.stream.return_value = mock_stream
    mock_anthropic.return_value = mock_client
    
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

@patch("private_rag_apps.generation.generator.anthropic.Anthropic")
def test_condense_with_history(mock_anthropic):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Condensed query")]
    mock_response.usage.input_tokens = 10
    mock_response.usage.output_tokens = 5
    mock_client.messages.create.return_value = mock_response
    mock_anthropic.return_value = mock_client
    
    history = [
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "A programming language."}
    ]
    
    result = condense("Why is it good?", history)
    assert result == "Condensed query"
