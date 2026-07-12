import pytest
from unittest.mock import patch, MagicMock
from private_rag_apps.evals.judge import _call_judge, evaluate_faithfulness, evaluate_answer_relevance

def mock_openai_response(text: str):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.output_text = text
    mock_response.usage.input_tokens = 10
    mock_response.usage.output_tokens = 20
    mock_client.responses.create.return_value = mock_response
    return mock_client

@patch('private_rag_apps.evals.judge.openai.OpenAI')
@patch('private_rag_apps.evals.judge.get_client')
def test_call_judge_valid_json(mock_get_client, mock_openai):
    mock_openai.return_value = mock_openai_response('{"score": 1, "rationale": "good"}')
    result = _call_judge("test prompt")
    assert result == {"score": 1, "rationale": "good"}

@patch('private_rag_apps.evals.judge.openai.OpenAI')
@patch('private_rag_apps.evals.judge.get_client')
def test_call_judge_markdown_json(mock_get_client, mock_openai):
    mock_openai.return_value = mock_openai_response('```json\n{"score": 0, "rationale": "bad"}\n```')
    result = _call_judge("test prompt")
    assert result == {"score": 0, "rationale": "bad"}

@patch('private_rag_apps.evals.judge.openai.OpenAI')
@patch('private_rag_apps.evals.judge.get_client')
def test_call_judge_invalid_json(mock_get_client, mock_openai):
    mock_openai.return_value = mock_openai_response('invalid response')
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
