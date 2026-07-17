import time
from typing import Dict, List, NotRequired, Optional, TypedDict, cast
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
from .gdrive_loader import load_drive
from .loader import Document, load_directory


# モジュールグローバルで安全: running行/advisory lockの排他（concurrency.start_run）により
# 同時に走る取り込みは常に1つだけなので、単一プロセス内での競合は起きない
_last_embed_call_at: Optional[float] = None


def _pace_embed_call() -> None:
    """Voyage embed呼び出し間隔がINGEST_EMBED_MIN_INTERVAL_SEC未満にならないよう待機する
    （レート制限予防。m4_ingestion_and_demo.md §4.2）"""
    global _last_embed_call_at
    now = time.monotonic()
    if _last_embed_call_at is not None:
        wait = settings.ingest_embed_min_interval_sec - (now - _last_embed_call_at)
        if wait > 0:
            time.sleep(wait)
    _last_embed_call_at = time.monotonic()


_real_pace_embed_call = _pace_embed_call


class Stats(TypedDict):
    added: int
    updated: int
    deleted: int
    skipped: int
    failed_files: List[str]
    # Drive取り込み限定・任意項目: ショートカット/非対応mimeTypeによりスキップされたアイテム
    # （gdrive_loader.SkippedItem 相当）を構造化情報として畳み込む。既存キーの意味は変えない
    # 純粋加算のため、ローカル取り込みの `Stats` 生成コードは変更不要（m9 T4 完了条件）
    skipped_items: NotRequired[List[Dict[str, str]]]


def run_ingestion(db: Session, trigger: str = "cli", force_delete: bool = False) -> IngestRun:
    """running行の作成から一連のインジェスト処理までを同期的に実行する（CLI用）。
    APIからのBackgroundTasks経由の実行はstart_runで作成したrunをexecute_ingestionに渡す"""
    run = start_run(db, trigger)
    return execute_ingestion(db, run, force_delete)


def run_gdrive_ingestion(
    db: Session, folder_id: str, trigger: str = "cli", force_delete: bool = False
) -> IngestRun:
    """running行の作成から一連のGoogle Drive取り込み処理までを同期的に実行する（CLI用）。
    run_ingestion と同じ start_run→execute_* パターン（多重実行の抑止は source_type に関わらず
    グローバルに1本のrunning行で行う。m9_google_drive_ingestion.md §4.6）"""
    run = start_run(db, trigger)
    return execute_gdrive_ingestion(db, run, folder_id, force_delete)


@observe(name="ingest_run")
def execute_ingestion(db: Session, run: IngestRun, force_delete: bool = False) -> IngestRun:
    """既にrunning行が作成済みのIngestRunを引数に、増分判定・チャンキング・ベクトル化・DB反映を実行する"""
    try:
        docs = load_directory(settings.corpus_dir)
        stats: Stats = {"added": 0, "updated": 0, "deleted": 0, "skipped": 0, "failed_files": []}
        found_paths = {doc.path for doc in docs}

        _process_documents(db, run, stats, docs)

        _apply_deletion_phase(
            db, run, stats, source_type="local_fs", found_keys=found_paths, force_delete=force_delete
        )

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


@observe(name="ingest_run_gdrive")
def execute_gdrive_ingestion(
    db: Session, run: IngestRun, folder_id: str, force_delete: bool = False
) -> IngestRun:
    """既にrunning行が作成済みのIngestRunを引数に、Google Driveフォルダ配下の増分判定・
    チャンキング・ベクトル化・DB反映を実行する。ローダーが gdrive_loader.load_drive() に
    差し替わり、削除検知が source_type='google_drive' に独立してスコープされる以外は
    execute_ingestion() と共通のコードパス（_process_documents/_process_one 以降）を通る
    （m9_google_drive_ingestion.md §4.5, AGENTS.md §3 の worker/cli 同様 ingestion 層は単一入口）。
    T2由来の GoogleDriveConfigError/AuthError/AccessError も他の例外と同様に run.error として
    記録し、プロセスをクラッシュさせない"""
    run.source_type = "google_drive"
    try:
        result = load_drive(db, folder_id)
        stats: Stats = {"added": 0, "updated": 0, "deleted": 0, "skipped": 0, "failed_files": []}
        if result.skipped:
            stats["skipped_items"] = [
                {"name": item.name, "path": item.path, "reason": item.reason} for item in result.skipped
            ]

        _process_documents(db, run, stats, result.documents)

        _apply_deletion_phase(
            db,
            run,
            stats,
            source_type="google_drive",
            found_keys=result.found_external_ids,
            force_delete=force_delete,
        )

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


def _process_documents(db: Session, run: IngestRun, stats: Stats, docs: List[Document]) -> None:
    """docsを順に_process_oneへ渡し、1件の失敗を隔離しつつ定期的にstatsを永続化する
    （execute_ingestion/execute_gdrive_ingestion で共有するループ本体）"""
    for i, doc in enumerate(docs):
        try:
            _process_one(db, doc, stats)
        except Exception as e:
            print(f"Error processing {doc.path}: {e}")
            stats["failed_files"].append(doc.path)
            db.rollback()

        if (i + 1) % settings.ingest_stats_flush_every == 0:
            _flush_stats(db, run, stats)

    _flush_stats(db, run, stats)


def _apply_deletion_phase(
    db: Session,
    run: IngestRun,
    stats: Stats,
    source_type: str,
    found_keys: set[str],
    force_delete: bool,
) -> None:
    """削除安全弁を判定し、許可されれば削除を反映する。source_type ごとに完全に独立して
    判定する（alive_count・found_count・削除対象クエリのいずれも他方のsource_typeを一切
    参照しない）。ローカル/Driveの走査結果とDB生存ソースの突き合わせが混同されると、
    一方の取り込み実行がもう一方のソースを誤って論理削除しかねない
    （m9_google_drive_ingestion.md §8 の重要リスク）。
    安全弁が発動しても追加/更新は既にcommit済みのため、run.statusはsuccessのまま維持し、
    理由をrun.errorに記録する（実行全体の失敗と区別する。m4_ingestion_and_demo.md §4.3）"""
    alive_count = (
        db.query(Source).filter(Source.source_type == source_type, Source.deleted_at.is_(None)).count()
    )
    allowed, reason = should_apply_deletions(
        alive_count=alive_count,
        found_count=len(found_keys),
        ratio=settings.ingest_delete_guard_ratio,
        force=force_delete,
    )
    run.status = "success"
    if allowed:
        stats["deleted"] = _apply_deletions(db, source_type, found_keys)
    else:
        run.error = f"delete phase aborted: {reason}"


def _process_one(db: Session, doc: Document, stats: Stats) -> None:
    """既存ソースの照合ロジックを doc.source_type に応じて分岐する（ローカル: path一致 /
    Drive: external_id一致。m9_google_drive_ingestion.md §4.5）。classify() 呼び出し以降の
    チャンキング・埋め込み・DB反映は source_type に関わらず完全に共通のコードパスを通る"""
    if doc.source_type == "local_fs":
        existing = (
            db.query(Source).filter(Source.path == doc.path, Source.source_type == "local_fs").first()
        )
    else:
        existing = (
            db.query(Source)
            .filter(Source.external_id == doc.external_id, Source.source_type == doc.source_type)
            .first()
        )
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
            # ローカルDocumentはsource_type="local_fs"/external_id=None/source_url=Noneが既定値のため、
            # ローカル取り込みの挙動は変わらない（m9_google_drive_ingestion.md §4.5）
            source_type=doc.source_type,
            external_id=doc.external_id,
            source_url=doc.source_url,
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
    # path/source_urlはDrive側の識別キーではない表示用フィールドのため、Drive上でのファイル
    # 移動/リネーム（pathの変化）・webViewLinkの変化があれば追随させる。ローカルは常に
    # doc.path == existing.path（識別キーそのもの）/doc.source_url == None のため無害
    existing.path = doc.path
    existing.source_url = doc.source_url
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


def _apply_deletions(db: Session, source_type: str, found_keys: set[str]) -> int:
    """`source_type` にスコープした削除検知本体。ローカルは path、Driveは external_id を
    識別キーとして使う（sources.path はDrive側でフォルダ内重複を許容するため識別キーに
    使えない。m9_google_drive_ingestion.md §4.2）。source_type フィルタは
    Source.deleted_at.is_(None) と同様に必須条件であり、外すと他source_typeの生存ソースを
    巻き込んで論理削除してしまう"""
    key_column = Source.path if source_type == "local_fs" else Source.external_id
    missing = (
        db.query(Source)
        .filter(
            Source.source_type == source_type,
            Source.deleted_at.is_(None),
            key_column.notin_(found_keys),
        )
        .all()
    )
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
    voyage_client = voyageai.Client(api_key=settings.voyage_api_key, max_retries=settings.voyage_max_retries)
    batch_size = settings.ingest_embed_batch_size
    embeddings: List[List[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        _pace_embed_call()
        result = voyage_client.embed(batch, model=settings.embed_model, input_type="document")
        embeddings.extend(cast(List[List[float]], result.embeddings))
    return embeddings
