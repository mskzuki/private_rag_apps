import datetime

from private_rag_apps.core.db import SessionLocal
from private_rag_apps.models.rag import IngestRun
from private_rag_apps.ingestion.concurrency import (
    IngestAlreadyRunningError,
    reap_stale_running,
    start_run,
)


def _cleanup_runs(db, run_ids):
    db.query(IngestRun).filter(IngestRun.id.in_(run_ids)).delete(synchronize_session=False)
    db.commit()


def test_start_run_creates_running_row():
    db = SessionLocal()
    run_ids = []
    try:
        run = start_run(db, trigger="cli")
        run_ids.append(run.id)
        assert run.status == "running"
        assert run.trigger == "cli"
    finally:
        _cleanup_runs(db, run_ids)
        db.close()


def test_start_run_rejects_when_already_running():
    db = SessionLocal()
    run_ids = []
    try:
        first = start_run(db, trigger="cli")
        run_ids.append(first.id)
        try:
            start_run(db, trigger="cli")
            assert False, "expected IngestAlreadyRunningError"
        except IngestAlreadyRunningError:
            pass
    finally:
        _cleanup_runs(db, run_ids)
        db.close()


def test_stale_running_is_reaped_before_new_run_starts():
    db = SessionLocal()
    run_ids = []
    try:
        stale = IngestRun(
            trigger="cli",
            status="running",
            started_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1),
        )
        db.add(stale)
        db.commit()
        db.refresh(stale)
        run_ids.append(stale.id)

        new_run = start_run(db, trigger="cli")
        run_ids.append(new_run.id)

        db.refresh(stale)
        assert stale.status == "error"
        assert stale.finished_at is not None
        assert new_run.status == "running"
        assert new_run.id != stale.id
    finally:
        _cleanup_runs(db, run_ids)
        db.close()


def test_start_run_advisory_lock_serializes_concurrent_starts():
    import threading

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    started_runs: list[IngestRun] = []
    lock = threading.Lock()

    def attempt():
        session = SessionLocal()
        try:
            barrier.wait(timeout=5)
            run = start_run(session, trigger="cli")
            with lock:
                outcomes.append("started")
                started_runs.append(run)
        except IngestAlreadyRunningError:
            with lock:
                outcomes.append("rejected")
        finally:
            session.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    try:
        assert sorted(outcomes) == ["rejected", "started"]
        assert len(started_runs) == 1
    finally:
        db = SessionLocal()
        try:
            _cleanup_runs(db, [r.id for r in started_runs])
        finally:
            db.close()


def test_reap_stale_running_ignores_recent_running_rows():
    db = SessionLocal()
    run_ids = []
    try:
        recent = IngestRun(trigger="cli", status="running")
        db.add(recent)
        db.commit()
        db.refresh(recent)
        run_ids.append(recent.id)

        reap_stale_running(db)

        db.refresh(recent)
        assert recent.status == "running"
    finally:
        _cleanup_runs(db, run_ids)
        db.close()
