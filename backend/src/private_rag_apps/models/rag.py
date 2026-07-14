import datetime
from typing import Optional, Dict, Any
from uuid import UUID

from sqlalchemy import text, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID, TIMESTAMP
from pgvector.sqlalchemy import Vector

from .base import Base

class Source(Base):
    """取り込み対象の原文書（コーパス内の1ファイル）を表す。"""

    __tablename__ = "sources"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    path: Mapped[str] = mapped_column(unique=True, nullable=False)
    title: Mapped[str] = mapped_column(server_default="", nullable=False)
    content_hash: Mapped[str] = mapped_column(nullable=False)
    source_updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class Chunk(Base):
    """Source を分割したチャンク。埋め込みベクトルと全文検索用インデックスを持つ検索単位。"""

    __tablename__ = "chunks"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(1024), nullable=False)
    metadata_: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, server_default='{}', nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("uq_chunk_source_position", "source_id", "position", unique=True),
        Index("chunks_content_bigm", "content", postgresql_using="gin", postgresql_ops={"content": "gin_bigm_ops"}),
    )

class IngestRun(Base):
    """取り込みジョブ（CLI / BackgroundTasks）1回分の実行記録。"""

    __tablename__ = "ingest_runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    trigger: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False)
    stats: Mapped[Dict[str, Any]] = mapped_column(JSONB, server_default='{}', nullable=False)
    error: Mapped[Optional[str]]
    started_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP(timezone=True))

class Conversation(Base):
    """チャットの1スレッド（複数の Message をまとめる単位）。"""

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    title: Mapped[str] = mapped_column(server_default="", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class Message(Base):
    """Conversation 内の1発言。role が assistant の場合は citations に出典を保持する。"""

    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(nullable=False)
    citations: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("messages_conversation", "conversation_id", "created_at"),
    )
