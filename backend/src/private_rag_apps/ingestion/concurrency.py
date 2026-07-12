import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from private_rag_apps.core.config import settings
from private_rag_apps.core.time import utcnow
from private_rag_apps.models.rag import IngestRun


class IngestAlreadyRunningError(Exception):
    pass


def reap_stale_running(db: Session) -> None:
    """クラッシュ等で残った古いrunning行をerrorへ回収する"""
    cutoff = utcnow() - datetime.timedelta(seconds=settings.ingest_stale_running_sec)
    stale_runs = (
        db.query(IngestRun)
        .filter(
            IngestRun.status == "running",
            IngestRun.finished_at.is_(None),
            IngestRun.started_at < cutoff,
        )
        .all()
    )
    if not stale_runs:
        return
    now = utcnow()
    for run in stale_runs:
        run.status = "error"
        run.error = "stale running row reaped at start of new run"
        run.finished_at = now
    db.commit()


def acquire_start_lock(db: Session) -> None:
    """running行の有無チェックを原子化するための advisory lock を取得する。
    呼び出し側のトランザクションが commit/rollback されるまで保持される
    （start_run・reset_index の双方が同じキーで取得し、互いに排他する）"""
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": settings.ingest_advisory_lock_key})


def get_running_run(db: Session) -> Optional[IngestRun]:
    return db.query(IngestRun).filter(IngestRun.status == "running").first()


def start_run(db: Session, trigger: str) -> IngestRun:
    """stale running行を回収した上で、running行の存在チェックとINSERTをadvisory lockで原子的に行う。
    実行中ずっとの排他はrunning行の存在そのものが担う（advisory lockは開始のraceのみを消す）"""
    reap_stale_running(db)
    acquire_start_lock(db)
    existing = get_running_run(db)
    if existing:
        raise IngestAlreadyRunningError(str(existing.id))
    run = IngestRun(trigger=trigger, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
