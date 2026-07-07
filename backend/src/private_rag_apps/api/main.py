from fastapi import FastAPI, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from langfuse import observe, get_client

from private_rag_apps.core.db import get_db
from private_rag_apps.retrieval.searcher import retrieve_context
from private_rag_apps.generation.generator import generate_answer

app = FastAPI(title="Private RAG Apps API")

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None

@app.get("/health")
def health_check():
    return {"status": "ok"}

@observe()
@app.post("/api/chat")
def chat(request: ChatRequest, db: Session = Depends(get_db)):
    get_client().update_current_span(name="chat_request", session_id=request.conversation_id)
    
    query = request.message
    
    # Retrieval
    context_chunks = retrieve_context(db, query=query)
    
    # Generation
    response = generate_answer(query=query, context_chunks=context_chunks)
    
    return response
