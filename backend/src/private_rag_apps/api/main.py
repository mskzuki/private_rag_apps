import json
import uuid
import asyncio
import time
from datetime import datetime
from fastapi import FastAPI, Depends, Request, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc
from langfuse import observe, propagate_attributes
from sse_starlette.sse import EventSourceResponse

from private_rag_apps.core.db import get_db
from private_rag_apps.core.config import settings
from private_rag_apps.models.rag import Conversation, Message
from private_rag_apps.retrieval.searcher import retrieve_context
from private_rag_apps.generation.generator import condense, generate_answer_stream

app = FastAPI(title="Private RAG Apps API")

class ChatRequest(BaseModel):
    message: str = Field(description="ユーザーからの質問メッセージ")
    conversation_id: Optional[str] = Field(
        default=None,
        description="継続する会話のID。未指定の場合は新規会話を作成する",
    )

class ConversationResponse(BaseModel):
    id: str = Field(description="会話ID")
    title: str = Field(description="会話タイトル（初回メッセージの先頭から自動生成）")
    updated_at: datetime = Field(description="最終更新日時")

class MessageResponse(BaseModel):
    id: str = Field(description="メッセージID")
    role: str = Field(description="発言者の役割（user または assistant）")
    content: str = Field(description="メッセージ本文")
    citations: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="回答の根拠となった出典情報（assistantメッセージのみ）",
    )
    created_at: datetime = Field(description="作成日時")

@app.get("/health")
def health_check():
    """APIの死活監視のためのヘルスチェックエンドポイント"""
    return {"status": "ok"}

@app.post("/api/conversations")
def create_conversation(db: Session = Depends(get_db)):
    """新規の会話（チャットセッション）を作成し、IDと初期タイトルを返す"""
    conv = Conversation()
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {"id": str(conv.id), "title": conv.title}

@app.get("/api/conversations", response_model=List[ConversationResponse])
def list_conversations(db: Session = Depends(get_db)):
    """保存されているすべての会話履歴の一覧を更新日時順に取得する"""
    convs = db.query(Conversation).order_by(desc(Conversation.updated_at)).all()
    return [{"id": str(c.id), "title": c.title, "updated_at": c.updated_at} for c in convs]

@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str, db: Session = Depends(get_db)):
    """指定されたIDの会話詳細と、それに紐づくメッセージ履歴を取得する"""
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    messages = db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at).all()
    
    return {
        "id": str(conv.id),
        "title": conv.title,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "citations": m.citations,
                "created_at": m.created_at.isoformat()
            } for m in messages
        ]
    }

@observe()
@app.post("/api/chat")
async def chat(request: Request, body: ChatRequest, db: Session = Depends(get_db)):
    """
    ユーザーからのメッセージを受け取り、会話履歴を踏まえた検索と回答生成を行う。
    生成結果はServer-Sent Events (SSE)を用いてストリーミングで返却し、終了時にメッセージをDBへ一括保存する。
    """
    start_time = time.time()
    conversation_id = body.conversation_id
    if not conversation_id:
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)
        conversation_id = str(conv.id)
        
    propagate_attributes(session_id=conversation_id)
    
    # Check if this is the first turn to set title
    existing_messages_count = db.query(Message).filter(Message.conversation_id == conversation_id).count()
    if existing_messages_count == 0:
        existing_conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if existing_conv:
            existing_conv.title = body.message[:settings.title_max_chars]
            db.commit()

    # Get history for condense
    history = db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at).all()
    history_dicts = [{"role": m.role, "content": m.content} for m in history]
    
    # Condense
    if existing_messages_count > 0:
        search_query = condense(body.message, history_dicts)
    else:
        search_query = body.message

    # Retrieval
    context_chunks = retrieve_context(db, query=search_query)

    async def event_generator():
        message_id = str(uuid.uuid4())
        full_content = ""
        citations_data = []
        has_error = False
        first_token = False

        try:
            # Generate Answer
            for event in generate_answer_stream(search_query, context_chunks):
                if await request.is_disconnected():
                    has_error = True
                    break
                
                event_type = event["event"]
                data = event["data"]

                if event_type == "token":
                    if not first_token:
                        ttft_ms = (time.time() - start_time) * 1000
                        first_token = True
                        try:
                            from langfuse import get_client
                            get_client().update_current_trace(metadata={"ttft_ms": round(ttft_ms, 2)})
                        except Exception:
                            pass
                    full_content += data
                    yield {"event": "token", "data": json.dumps(data, ensure_ascii=False)}
                elif event_type == "citations":
                    citations_data = data
                    yield {"event": "citations", "data": json.dumps(data, ensure_ascii=False)}
                elif event_type == "error":
                    has_error = True
                    yield {"event": "error", "data": json.dumps(data, ensure_ascii=False)}
                    break
                
                await asyncio.sleep(0) # Yield control

        except Exception as e:
            has_error = True
            yield {"event": "error", "data": json.dumps(str(e), ensure_ascii=False)}

        if not has_error:
            # Bulk Save
            try:
                user_msg = Message(
                    conversation_id=conversation_id,
                    role="user",
                    content=body.message,
                )
                assistant_msg = Message(
                    id=message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_content,
                    citations=citations_data
                )
                db.add(user_msg)
                db.add(assistant_msg)
                
                from sqlalchemy.sql import func
                conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
                if conv:
                    conv.updated_at = func.now()
                
                db.commit()
            except Exception as e:
                db.rollback()
                has_error = True
                yield {"event": "error", "data": json.dumps(f"DB save error: {e}", ensure_ascii=False)}

        if not has_error:
            done_payload = {
                "message_id": message_id,
                "conversation_id": conversation_id
            }
            yield {"event": "done", "data": json.dumps(done_payload, ensure_ascii=False)}

    return EventSourceResponse(event_generator(), ping=settings.sse_keepalive_sec)
