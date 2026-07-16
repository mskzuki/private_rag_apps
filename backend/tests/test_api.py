from fastapi.testclient import TestClient
from unittest.mock import patch

from private_rag_apps.api.main import app
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.models.rag import Conversation, Message

client = TestClient(app)

def test_conversations_crud():
    # 1. Create a conversation
    response = client.post("/api/conversations")
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["title"] == ""
    
    conv_id = data["id"]
    
    # 2. List conversations
    response = client.get("/api/conversations")
    assert response.status_code == 200
    convs = response.json()
    assert any(c["id"] == conv_id for c in convs)
    
    # 3. Get specific conversation (should be empty initially)
    response = client.get(f"/api/conversations/{conv_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["id"] == conv_id
    assert len(detail["messages"]) == 0
    
    # Cleanup (optional but good practice)
    db = SessionLocal()
    db.query(Conversation).filter(Conversation.id == conv_id).delete()
    db.commit()
    db.close()

@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
@patch("private_rag_apps.graph.nodes.generate.generate_answer_stream")
@patch("private_rag_apps.graph.nodes.rewrite.condense")
def test_chat_bulk_save_and_history(mock_condense, mock_generate, mock_retrieve):
    # Mock retrieval
    mock_retrieve.return_value = [
        {"title": "Doc1", "chunk_id": "c1", "path": "p1.md", "content": "text"}
    ]

    # Mock streaming response
    def mock_stream(*args, **kwargs):
        yield {"event": "citations", "data": [{"title": "Doc1", "n": 1}]}
        yield {"event": "token", "data": "Hello"}
        yield {"event": "token", "data": " World"}

    mock_generate.side_effect = mock_stream
    mock_condense.return_value = ("condensed query", True)

    # Create conversation manually
    db = SessionLocal()
    conv = Conversation()
    db.add(conv)
    db.commit()
    db.refresh(conv)
    conv_id = str(conv.id)
    db.close()

    # 1. First turn: rewriteノードはグラフ内で常に実行される(condense()自身が
    # history空時にLLMを呼ばず早期returnする。ここではcondenseをmockしているため
    # 呼び出し自体は発生するが、historyが空リストで呼ばれることを確認する
    # （M7 T5: rewriteはcondense()を呼ぶだけの薄いラッパー。スペック §3.3）
    response = client.post("/api/chat", json={
        "message": "First message",
        "conversation_id": conv_id
    })

    assert response.status_code == 200
    mock_condense.assert_called_once()
    first_args, _ = mock_condense.call_args
    assert first_args[0] == "First message"
    assert first_args[1] == []

    # Parse SSE output (line by line)
    content = response.content.decode("utf-8")
    assert "event: citations" in content
    assert "event: token" in content
    assert "event: done" in content
    
    # Verify DB save
    db = SessionLocal()
    messages = db.query(Message).filter(Message.conversation_id == conv_id).order_by(Message.created_at).all()
    assert len(messages) == 2
    
    assert messages[0].role == "user"
    assert messages[0].content == "First message"
    
    assert messages[1].role == "assistant"
    assert messages[1].content == "Hello World"
    assert messages[1].citations[0]["title"] == "Doc1"
    
    # Verify title was updated
    updated_conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    assert updated_conv.title == "First message"
    
    # 2. Second turn (condense SHOULD be called)
    mock_condense.reset_mock()
    response2 = client.post("/api/chat", json={
        "message": "Second message",
        "conversation_id": conv_id
    })
    
    assert response2.status_code == 200
    mock_condense.assert_called_once()
    args, kwargs = mock_condense.call_args
    assert args[0] == "Second message"
    assert len(args[1]) == 2 # history should have 2 messages
    
    # Verify DB has 4 messages now
    messages = db.query(Message).filter(Message.conversation_id == conv_id).order_by(Message.created_at).all()
    assert len(messages) == 4
    
    # Cleanup
    db.query(Conversation).filter(Conversation.id == conv_id).delete()
    db.commit()
    db.close()
