import pytest
from unittest.mock import patch, MagicMock
from private_rag_apps.evals.judge import _call_judge, evaluate_faithfulness, evaluate_answer_relevance

def mock_anthropic_response(text: str):
    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_content = MagicMock()
    mock_content.text = text
    mock_message.content = [mock_content]
    mock_message.usage.input_tokens = 10
    mock_message.usage.output_tokens = 20
    mock_client.messages.create.return_value = mock_message
    return mock_client

@patch('private_rag_apps.evals.judge.anthropic.Anthropic')
@patch('private_rag_apps.evals.judge.get_client')
def test_call_judge_valid_json(mock_get_client, mock_anthropic):
    mock_anthropic.return_value = mock_anthropic_response('{"score": 1, "rationale": "good"}')
    result = _call_judge("test prompt")
    assert result == {"score": 1, "rationale": "good"}

@patch('private_rag_apps.evals.judge.anthropic.Anthropic')
@patch('private_rag_apps.evals.judge.get_client')
def test_call_judge_markdown_json(mock_get_client, mock_anthropic):
    mock_anthropic.return_value = mock_anthropic_response('```json\n{"score": 0, "rationale": "bad"}\n```')
    result = _call_judge("test prompt")
    assert result == {"score": 0, "rationale": "bad"}

@patch('private_rag_apps.evals.judge.anthropic.Anthropic')
@patch('private_rag_apps.evals.judge.get_client')
def test_call_judge_invalid_json(mock_get_client, mock_anthropic):
    mock_anthropic.return_value = mock_anthropic_response('invalid response')
    result = _call_judge("test prompt")
    assert result["score"] == 0
    assert "Error parsing judge output" in result["rationale"]

@patch('private_rag_apps.evals.judge._call_judge')
def test_evaluate_faithfulness(mock_call_judge):
    mock_call_judge.return_value = {"score": 1, "rationale": "ok"}
    result = evaluate_faithfulness("q", "a", [{"content": "ctx"}])
    assert result["score"] == 1
    assert mock_call_judge.called

@patch('private_rag_apps.evals.judge._call_judge')
def test_evaluate_answer_relevance(mock_call_judge):
    mock_call_judge.return_value = {"score": 1, "rationale": "ok"}
    result = evaluate_answer_relevance("q", "a", "ref")
    assert result["score"] == 1
    assert mock_call_judge.called
