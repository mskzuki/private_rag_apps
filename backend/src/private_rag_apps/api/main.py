import json
import uuid
import asyncio
import time
from datetime import datetime
from fastapi import FastAPI, Depends, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, TypedDict
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from langfuse import observe, propagate_attributes
from sse_starlette.sse import EventSourceResponse

from private_rag_apps.core.db import get_db, SessionLocal
from private_rag_apps.core.config import settings
from private_rag_apps.models.rag import Chunk, Conversation, IngestRun, Message, Source
from private_rag_apps.ingestion.concurrency import (
    IngestAlreadyRunningError,
    acquire_start_lock,
    get_running_run,
    reap_stale_running,
    start_run,
)
from private_rag_apps.ingestion.indexer import execute_ingestion
from private_rag_apps.graph.builder import build_graph
from private_rag_apps.graph.state import GraphState
from private_rag_apps.graph.state import Message as HistoryMessage

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

class SourceListItem(TypedDict):
    id: str
    path: str
    title: str
    chunk_count: int
    updated_at: datetime
    deleted_at: Optional[datetime]

class TriggerIngestResult(TypedDict):
    id: str
    status: str
    trigger: str

class IngestRunListItem(TypedDict):
    id: str
    trigger: str
    status: str
    stats: Dict[str, Any]
    error: Optional[str]
    started_at: datetime
    finished_at: Optional[datetime]

class ResetIndexResult(TypedDict):
    status: str
    deleted_sources: int
    deleted_chunks: int

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
        
    # session_id は「現在アクティブなspan(=@observe()によるこのリクエストのルートtrace)」に
    # 即時付与される(with に入った時点で同期的に設定される。langfuse SDK 4.x OTelネイティブ実装。
    # graph/nodes/grade.py の trace レベルmetadata記録と同じ仕組み)。空bodyのwithで良い
    with propagate_attributes(session_id=conversation_id):
        pass
    
    # Check if this is the first turn to set title
    existing_messages_count = db.query(Message).filter(Message.conversation_id == conversation_id).count()
    if existing_messages_count == 0:
        existing_conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if existing_conv:
            existing_conv.title = body.message[:settings.title_max_chars]
            db.commit()

    # 会話履歴のロード（正はDB。グラフはステートレスに保つ。スペック §3.4）。
    # rewrite ノード（condense()呼び出し）はグラフ内で実行するため、ここでは
    # 生の user_query と history をそのままグラフに渡す（M7 T5でAGENTS.md §3の
    # 暫定例外を解消。以前はここでグラフの外からcondense()を直接呼んでいた）
    history = db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at).all()
    history_dicts: List[HistoryMessage] = [{"role": m.role, "content": m.content} for m in history]

    graph = build_graph(db)
    initial_state: GraphState = {
        "conversation_id": conversation_id,
        "user_query": body.message,
        "history": history_dicts,
    }

    async def event_generator():
        message_id = str(uuid.uuid4())
        full_content = ""
        citations_data = []
        has_error = False
        first_token = False

        try:
            # Retrieve → Generate をグラフ経由で実行し、custom streamのイベントを消費する
            async for event in graph.astream(initial_state, stream_mode="custom"):
                if await request.is_disconnected():
                    has_error = True
                    break
                
                event_type = event["event"]
                data = event["data"]

                if event_type == "token":
                    if not first_token:
                        ttft_ms = (time.time() - start_time) * 1000
                        first_token = True
                        # update_current_trace() はインストール済みlangfuse SDK(4.x、OTelネイティブ
                        # 実装)には存在しないメソッドで、以前はここで呼んでいたため常に例外が
                        # 握りつぶされ記録されていなかった(2026-07-16発見・修正)。propagate_attributes
                        # のmetadataキーは langfuse.trace.metadata.* 名前空間のため、現在アクティブな
                        # spanに関わらずtraceレベルのmetadataとして記録される(graph/nodes/grade.py参照)
                        with propagate_attributes(metadata={"ttft_ms": round(ttft_ms, 2)}):
                            pass
                    full_content += data
                    yield {"event": "token", "data": json.dumps(data, ensure_ascii=False)}
                elif event_type == "citations":
                    citations_data = data
                    yield {"event": "citations", "data": json.dumps(data, ensure_ascii=False)}
                elif event_type in ("node_start", "route_decided", "rewrite_result"):
                    # M7 T6（スペック §5.2）: グラフ実行の透明性用イベント。
                    # full_content/citations_data の蓄積には関与しない素通し
                    yield {"event": event_type, "data": json.dumps(data, ensure_ascii=False)}
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


@app.get("/api/sources")
def list_sources(
    include_deleted: bool = False, db: Session = Depends(get_db)
) -> List[SourceListItem]:
    """取り込み済みソース一覧を、チャンク数・最終取り込み日時とともに返す（N+1を避けGROUP BYで集計）"""
    query = (
        db.query(Source, func.count(Chunk.id).label("chunk_count"))
        .outerjoin(Chunk, Chunk.source_id == Source.id)
        .group_by(Source.id)
        .order_by(desc(Source.updated_at))
    )
    if not include_deleted:
        query = query.filter(Source.deleted_at.is_(None))
    rows = query.all()
    return [
        {
            "id": str(source.id),
            "path": source.path,
            "title": source.title,
            "chunk_count": chunk_count,
            "updated_at": source.updated_at,
            "deleted_at": source.deleted_at,
        }
        for source, chunk_count in rows
    ]


def _run_ingest_in_background(run_id: str, force_delete: bool) -> None:
    """BackgroundTasksから呼ばれる実処理。リクエストスコープのSessionとは独立したSessionを使う"""
    db = SessionLocal()
    try:
        run = db.query(IngestRun).filter(IngestRun.id == run_id).first()
        if run is None:
            return
        execute_ingestion(db, run, force_delete=force_delete)
    finally:
        db.close()


@app.post("/api/ingest")
def trigger_ingest(
    background_tasks: BackgroundTasks, force_delete: bool = False, db: Session = Depends(get_db)
) -> TriggerIngestResult:
    """増分再取り込みをBackgroundTasksで起動し、作成したingest_runのidを即返す。
    既にrunning中のrunがあれば409を返す（実行中ずっと有効な排他はrunning行の存在そのものが担う）"""
    try:
        run = start_run(db, trigger="api")
    except IngestAlreadyRunningError as e:
        raise HTTPException(status_code=409, detail=f"ingestion already running: {e}")

    run_id = str(run.id)
    background_tasks.add_task(_run_ingest_in_background, run_id, force_delete)
    return {"id": run_id, "status": run.status, "trigger": run.trigger}


@app.get("/api/ingest/runs")
def list_ingest_runs(limit: int = 20, db: Session = Depends(get_db)) -> List[IngestRunListItem]:
    """取り込み実行履歴・進行状態を新しい順に返す（UIはこれをポーリングして進捗表示する）"""
    runs = db.query(IngestRun).order_by(desc(IngestRun.started_at)).limit(limit).all()
    return [
        {
            "id": str(r.id),
            "trigger": r.trigger,
            "status": r.status,
            "stats": r.stats,
            "error": r.error,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
        }
        for r in runs
    ]


@app.delete("/api/index")
def reset_index(db: Session = Depends(get_db)) -> ResetIndexResult:
    """コーパスのインデックス（sources/chunks）のみを初期化する。会話は保持する。
    取り込み中/開始直後は拒否する（start_runと同じadvisory lockで排他し、開始レースを防ぐ。
    監査はアプリログのみ、ingest_runsには記録しない）"""
    reap_stale_running(db)
    acquire_start_lock(db)
    if get_running_run(db):
        raise HTTPException(status_code=409, detail="ingestion is running")

    deleted_chunks = db.query(Chunk).delete()
    deleted_sources = db.query(Source).delete()
    db.commit()
    print(f"Index reset: deleted {deleted_sources} sources, {deleted_chunks} chunks")
    return {"status": "ok", "deleted_sources": deleted_sources, "deleted_chunks": deleted_chunks}
