from typing import List, TypedDict, cast
from uuid import UUID

import voyageai
from langfuse import observe
from sqlalchemy.orm import Session

from private_rag_apps.core.config import settings
from private_rag_apps.core.time import utcnow
from private_rag_apps.models.rag import Chunk, IngestRun, Source
from .chunker import ChunkResult, chunk_markdown
from .concurrency import start_run
from .diff import Action, classify, should_apply_deletions
from .loader import Document, load_directory


class Stats(TypedDict):
    added: int
    updated: int
    deleted: int
    skipped: int
    failed_files: List[str]


def run_ingestion(db: Session, trigger: str = "cli", force_delete: bool = False) -> IngestRun:
    """running行の作成から一連のインジェスト処理までを同期的に実行する（CLI用）。
    APIからのBackgroundTasks経由の実行はstart_runで作成したrunをexecute_ingestionに渡す"""
    run = start_run(db, trigger)
    return execute_ingestion(db, run, force_delete)


@observe(name="ingest_run")
def execute_ingestion(db: Session, run: IngestRun, force_delete: bool = False) -> IngestRun:
    """既にrunning行が作成済みのIngestRunを引数に、増分判定・チャンキング・ベクトル化・DB反映を実行する"""
    try:
        docs = load_directory(settings.corpus_dir)
        stats: Stats = {"added": 0, "updated": 0, "deleted": 0, "skipped": 0, "failed_files": []}
        found_paths: set[str] = set()

        for i, doc in enumerate(docs):
            found_paths.add(doc.path)
            try:
                _process_one(db, doc, stats)
            except Exception as e:
                print(f"Error processing {doc.path}: {e}")
                stats["failed_files"].append(doc.path)
                db.rollback()

            if (i + 1) % settings.ingest_stats_flush_every == 0:
                _flush_stats(db, run, stats)

        _flush_stats(db, run, stats)

        _apply_deletion_phase(db, run, stats, found_paths, force_delete)

        run.stats = dict(stats)
        run.finished_at = utcnow()
        db.commit()
        return run

    except Exception as e:
        run.status = "error"
        run.error = str(e)
        run.finished_at = utcnow()
        db.commit()
        raise e


def _apply_deletion_phase(
    db: Session, run: IngestRun, stats: Stats, found_paths: set[str], force_delete: bool
) -> None:
    """削除安全弁を判定し、許可されれば削除を反映する。
    安全弁が発動しても追加/更新は既にcommit済みのため、run.statusはsuccessのまま維持し、
    理由をrun.errorに記録する（実行全体の失敗と区別する。m4_ingestion_and_demo.md §4.3）"""
    alive_count = db.query(Source).filter(Source.deleted_at.is_(None)).count()
    allowed, reason = should_apply_deletions(
        alive_count=alive_count,
        found_count=len(found_paths),
        ratio=settings.ingest_delete_guard_ratio,
        force=force_delete,
    )
    run.status = "success"
    if allowed:
        stats["deleted"] = _apply_deletions(db, found_paths)
    else:
        run.error = f"delete phase aborted: {reason}"


def _process_one(db: Session, doc: Document, stats: Stats) -> None:
    existing = db.query(Source).filter(Source.path == doc.path).first()
    action = classify(
        existing_hash=existing.content_hash if existing else None,
        existing_deleted_at=existing.deleted_at if existing else None,
        new_hash=doc.content_hash,
    )

    if action == Action.SKIP:
        stats["skipped"] += 1
        return

    if action == Action.REVIVE_ONLY:
        assert existing is not None  # classifyの契約上、REVIVE_ONLYはexisting_hashがNoneでない場合のみ返る
        existing.deleted_at = None
        existing.source_updated_at = doc.updated_at
        existing.updated_at = utcnow()
        db.commit()
        stats["updated"] += 1
        return

    # INSERT / REPLACE: 埋め込みはトランザクション外で先に実行する
    chunks = chunk_markdown(doc.content)
    embeddings = _embed_documents([c.content for c in chunks]) if chunks else []

    if action == Action.INSERT:
        new_source = Source(
            path=doc.path,
            title=doc.title,
            content_hash=doc.content_hash,
            source_updated_at=doc.updated_at,
        )
        db.add(new_source)
        db.flush()
        _insert_chunks(db, new_source.id, chunks, embeddings)
        db.commit()
        stats["added"] += 1
        return

    # REPLACE: delete + insert を1つの短いトランザクションで実行
    assert existing is not None  # classifyの契約上、REPLACEはexisting_hashがNoneでない場合のみ返る
    db.query(Chunk).filter(Chunk.source_id == existing.id).delete()
    existing.title = doc.title
    existing.content_hash = doc.content_hash
    existing.source_updated_at = doc.updated_at
    existing.deleted_at = None
    existing.updated_at = utcnow()
    _insert_chunks(db, existing.id, chunks, embeddings)
    db.commit()
    stats["updated"] += 1


def _insert_chunks(
    db: Session, source_id: UUID, chunks: List[ChunkResult], embeddings: List[List[float]]
) -> None:
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        db.add(
            Chunk(
                source_id=source_id,
                position=i,
                content=chunk.content,
                embedding=emb,
                metadata_=chunk.metadata,
            )
        )


def _apply_deletions(db: Session, found_paths: set[str]) -> int:
    missing = db.query(Source).filter(Source.deleted_at.is_(None), Source.path.notin_(found_paths)).all()
    now = utcnow()
    for source in missing:
        source.deleted_at = now
    db.commit()
    return len(missing)


def _flush_stats(db: Session, run: IngestRun, stats: Stats) -> None:
    run.stats = dict(stats)
    db.commit()


@observe(name="embed_documents")
def _embed_documents(texts: List[str]) -> List[List[float]]:
    """チャンク化されたテキストのリストをVoyage AIを用いてバッチ単位でベクトル化（埋め込み）する"""
    if not texts:
        return []
    voyage_client = voyageai.Client(api_key=settings.voyage_api_key)
    batch_size = settings.ingest_embed_batch_size
    embeddings: List[List[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        result = voyage_client.embed(batch, model=settings.embed_model, input_type="document")
        embeddings.extend(cast(List[List[float]], result.embeddings))
    return embeddings
