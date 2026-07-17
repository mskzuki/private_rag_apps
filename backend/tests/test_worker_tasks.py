"""worker/tasks.py の run_gdrive_ingestion（ARQジョブ関数）テスト（M9 T5）。

観点（m9_tasklist.md T5完了条件）:
- INGEST_GDRIVE_JOB_MAX_TRIES 回失敗後に ingest_runs.status='error' が記録される
- グローバル排他ロックが、リトライの合間（run.statusが一時的にerrorへ書き換わる窓）でも
  途切れないこと（本ファイルで最も安全性が重要なテスト。TestExclusivityHoldsDuringRetryGap）

ジョブ関数は SessionLocal() で独自のDBセッションを開くため、テスト側でも別セッションを使い、
db.refresh()で最新状態を確認する（実プロセス分離時の挙動に近い）。実Redis/実ARQ Workerは
使わず、ctx={"job_try": N}を直接組み立てて関数を呼ぶ（retry判定ロジック自体の単体テスト）。
"""

import asyncio

import pytest
from arq.worker import Retry

from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.core.time import utcnow
from private_rag_apps.ingestion.concurrency import IngestAlreadyRunningError, start_run
from private_rag_apps.models.rag import IngestRun
from private_rag_apps.worker.tasks import run_gdrive_ingestion


def _cleanup(run_ids):
    db = SessionLocal()
    try:
        db.query(IngestRun).filter(IngestRun.id.in_(run_ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _failing_execute(fail_message: str = "boom"):
    def _inner(db, run, folder_id, force_delete=False):
        run.status = "error"
        run.error = fail_message
        run.finished_at = utcnow()
        db.commit()
        raise RuntimeError(fail_message)

    return _inner


class TestExclusivityHoldsDuringRetryGap:
    """m9_google_drive_ingestion.md §4.6が要求する「Driveジョブの実行中（リトライ含む）は
    グローバル排他ロックが途切れない」ことの直接証明。

    RED（このfixを適用する前の素朴な実装、つまり単に execute_gdrive_ingestion() を毎回
    素通しで呼ぶだけの実装）だとどうなるか: execute_gdrive_ingestion() は例外発生時に
    無条件で run.status='error' / run.finished_at=now をcommitして再送出する。job_tryが
    max_triesに満たずARQがこの後リトライする想定でも、run.statusは'error'のまま残ってしまう。
    その状態で start_run() を呼ぶと get_running_run() が status=='running' の行を
    見つけられず、例外を送出せず新規runningを作ってしまう（=このテストがfailする）。
    本実装ではリトライ枠が残っている失敗時にrunをrunning状態へ戻してからRetry()を送出するため、
    start_run()は引き続きIngestAlreadyRunningErrorを送出し続ける（このテストがpassする）。
    """

    def test_start_run_still_rejected_immediately_after_a_retryable_failure(self, monkeypatch):
        monkeypatch.setattr(settings, "ingest_gdrive_job_max_tries", 3)
        db = SessionLocal()
        run_ids: list = []
        try:
            run = start_run(db, trigger="api")
            run_ids.append(run.id)
            run_id = str(run.id)

            monkeypatch.setattr(
                "private_rag_apps.worker.tasks.execute_gdrive_ingestion", _failing_execute()
            )

            # job_try=1 < max_tries=3 なのでARQ再試行が予定されている状況を模擬する
            with pytest.raises(Retry):
                asyncio.run(run_gdrive_ingestion({"job_try": 1}, run_id, "folder-id", False))

            # ジョブ関数自身が別セッションでrun.statusをrunningへ戻しているはず
            db.refresh(run)
            assert run.status == "running"
            assert run.error is None
            assert run.finished_at is None

            # 直接的な排他性の証明: 別セッションからstart_run()を呼んでも即座に拒否される
            db2 = SessionLocal()
            try:
                with pytest.raises(IngestAlreadyRunningError):
                    start_run(db2, trigger="api")
            finally:
                db2.close()
        finally:
            _cleanup(run_ids)
            db.close()

    def test_exclusivity_holds_across_multiple_retry_gaps(self, monkeypatch):
        """job_try=1, 2 と連続して失敗しても(max_tries=3)、都度running状態へ戻り
        排他が途切れないことを確認する"""
        monkeypatch.setattr(settings, "ingest_gdrive_job_max_tries", 3)
        db = SessionLocal()
        run_ids: list = []
        try:
            run = start_run(db, trigger="api")
            run_ids.append(run.id)
            run_id = str(run.id)

            monkeypatch.setattr(
                "private_rag_apps.worker.tasks.execute_gdrive_ingestion", _failing_execute()
            )

            for job_try in (1, 2):
                with pytest.raises(Retry):
                    asyncio.run(run_gdrive_ingestion({"job_try": job_try}, run_id, "folder-id", False))
                db.refresh(run)
                assert run.status == "running"

                db2 = SessionLocal()
                try:
                    with pytest.raises(IngestAlreadyRunningError):
                        start_run(db2, trigger="api")
                finally:
                    db2.close()
        finally:
            _cleanup(run_ids)
            db.close()


class TestFinalAttemptLeavesTerminalErrorState:
    """INGEST_GDRIVE_JOB_MAX_TRIES回失敗後にingest_runs.status='error'が記録され、
    かつグローバル排他ロックが正しく解放される（=次の取り込みが開始できる）ことを確認する"""

    def test_final_try_failure_leaves_error_status_and_releases_lock(self, monkeypatch):
        monkeypatch.setattr(settings, "ingest_gdrive_job_max_tries", 2)
        db = SessionLocal()
        run_ids: list = []
        try:
            run = start_run(db, trigger="api")
            run_ids.append(run.id)
            run_id = str(run.id)

            monkeypatch.setattr(
                "private_rag_apps.worker.tasks.execute_gdrive_ingestion", _failing_execute("final boom")
            )

            # job_try=2 == max_tries=2: これ以上のリトライは予定されていない最終試行
            with pytest.raises(RuntimeError, match="final boom"):
                asyncio.run(run_gdrive_ingestion({"job_try": 2}, run_id, "folder-id", False))

            db.refresh(run)
            assert run.status == "error"
            assert run.error == "final boom"
            assert run.finished_at is not None

            # 排他ロックは解放され、新しい取り込みを開始できる
            db2 = SessionLocal()
            try:
                new_run = start_run(db2, trigger="api")
                run_ids.append(new_run.id)
                assert new_run.status == "running"
            finally:
                db2.close()
        finally:
            _cleanup(run_ids)
            db.close()


class TestSuccessPath:
    def test_successful_execution_returns_success_status(self, monkeypatch):
        db = SessionLocal()
        run_ids: list = []
        try:
            run = start_run(db, trigger="api")
            run_ids.append(run.id)
            run_id = str(run.id)

            def _succeed(db_, run_, folder_id, force_delete=False):
                run_.status = "success"
                run_.stats = {"added": 1}
                run_.finished_at = utcnow()
                db_.commit()
                return run_

            monkeypatch.setattr("private_rag_apps.worker.tasks.execute_gdrive_ingestion", _succeed)

            result = asyncio.run(run_gdrive_ingestion({"job_try": 1}, run_id, "folder-id", False))
            assert result == {"status": "success", "run_id": run_id}

            db.refresh(run)
            assert run.status == "success"
        finally:
            _cleanup(run_ids)
            db.close()

    def test_missing_run_row_returns_not_found_without_raising(self):
        result = asyncio.run(
            run_gdrive_ingestion({"job_try": 1}, "00000000-0000-0000-0000-000000000000", "folder-id", False)
        )
        assert result["status"] == "not_found"
