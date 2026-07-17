"""POST /api/ingest/gdrive エンドポイントのテスト（M9 T5）。

観点（m9_tasklist.md T5完了条件）:
- 呼び出し時点でingest_runsのrunning行を同期作成してから応答を返す
- グローバル排他ロックにより、ローカル取り込み実行中はDrive取り込みジョブが（逆方向も）
  起動できない
- GET /api/ingest/runs の応答形式（既存API契約）が変更されていない

ARQへのenqueue自体（`_enqueue_gdrive_job`）はmockする。実Redis/実ARQ Workerを使った
「ジョブが実際に消費されること」の検証はtest_worker_gdrive_integration.pyで行う（責務分離。
既存のPOST /api/ingestテスト（tests/test_ingest_api.py）が`_run_ingest_in_background`を
mockするのと同じパターン）"""

import threading
from unittest.mock import patch

from fastapi.testclient import TestClient

import private_rag_apps.api.main
from private_rag_apps.api.main import app
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.models.rag import IngestRun

client = TestClient(app)


def _cleanup(run_ids=None):
    if not run_ids:
        return
    db = SessionLocal()
    try:
        db.query(IngestRun).filter(IngestRun.id.in_(run_ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


class TestPostIngestGdrive:
    def test_returns_run_id_with_running_row_created_synchronously(self):
        with patch("private_rag_apps.api.main._enqueue_gdrive_job") as mock_enqueue:
            response = client.post("/api/ingest/gdrive")
        body = response.json()
        try:
            assert response.status_code == 200
            assert "id" in body
            db = SessionLocal()
            run = db.query(IngestRun).filter(IngestRun.id == body["id"]).first()
            db.close()
            assert run is not None
            assert run.trigger == "api"
            assert run.status == "running"
            mock_enqueue.assert_called_once()
        finally:
            _cleanup(run_ids=[body.get("id")] if body.get("id") else None)

    def test_marks_run_as_error_when_enqueue_fails(self):
        """RedisダウンなどでARQへのenqueue自体が失敗した場合、start_run()で作成済みの
        running行をerror状態に確定させる（放置するとreap_stale_runningが回収するまでの間
        他の取り込みがすべてブロックされ続けるため）"""
        created_run_ids = []
        real_start_run = private_rag_apps.api.main.start_run

        def _capturing_start_run(db, trigger):
            run = real_start_run(db, trigger=trigger)
            created_run_ids.append(run.id)
            return run

        run_id = None
        try:
            with (
                patch(
                    "private_rag_apps.api.main.start_run",
                    side_effect=_capturing_start_run,
                ),
                patch(
                    "private_rag_apps.api.main._enqueue_gdrive_job",
                    side_effect=ConnectionError("redis unreachable"),
                ) as mock_enqueue,
            ):
                response = client.post("/api/ingest/gdrive")

            assert response.status_code == 500
            mock_enqueue.assert_called_once()
            assert len(created_run_ids) == 1
            run_id = created_run_ids[0]

            db = SessionLocal()
            run = db.query(IngestRun).filter(IngestRun.id == run_id).first()
            db.close()

            assert run is not None
            assert run.status == "error"
            assert run.error is not None
            assert "redis unreachable" in run.error
            assert run.finished_at is not None
        finally:
            _cleanup(run_ids=[run_id] if run_id else None)

    def test_rejects_with_409_when_already_running(self):
        db = SessionLocal()
        running = IngestRun(trigger="cli", status="running")
        db.add(running)
        db.commit()
        db.refresh(running)
        run_id = running.id
        db.close()

        try:
            with patch("private_rag_apps.api.main._enqueue_gdrive_job") as mock_enqueue:
                response = client.post("/api/ingest/gdrive")
            assert response.status_code == 409
            mock_enqueue.assert_not_called()
        finally:
            _cleanup(run_ids=[run_id])


class TestGlobalExclusivityAcrossSourceTypes:
    """m9_google_drive_ingestion.md §4.6: 多重実行の抑止はsource_typeに関わらずグローバルに
    1本のrunning行で行う。ローカル取り込み実行中はDrive取り込みが、Drive取り込み実行中は
    ローカル取り込みが、互いに起動できないことを両方向で確認する"""

    def test_gdrive_trigger_rejected_while_local_ingest_running(self):
        release_event = threading.Event()
        started_event = threading.Event()

        def slow_local_background(run_id: str, force_delete: bool) -> None:
            started_event.set()
            release_event.wait(timeout=5)

        results = {}

        def call_local():
            with patch(
                "private_rag_apps.api.main._run_ingest_in_background", side_effect=slow_local_background
            ):
                results["local"] = client.post("/api/ingest")

        t = threading.Thread(target=call_local)
        t.start()
        assert started_event.wait(timeout=5)

        try:
            with patch("private_rag_apps.api.main._enqueue_gdrive_job") as mock_enqueue:
                gdrive_response = client.post("/api/ingest/gdrive")
            assert gdrive_response.status_code == 409
            mock_enqueue.assert_not_called()
        finally:
            release_event.set()
            t.join(timeout=5)
            local_run_id = results["local"].json()["id"]
            _cleanup(run_ids=[local_run_id])

    def test_local_trigger_rejected_while_gdrive_ingest_running(self):
        release_event = threading.Event()
        started_event = threading.Event()

        async def slow_gdrive_enqueue(run_id: str, folder_id: str, force_delete: bool) -> None:
            started_event.set()
            # threading.Eventはブロッキングだが、この関数はテストの一瞬だけしか使わないため許容する
            release_event.wait(timeout=5)

        results = {}

        def call_gdrive():
            with patch(
                "private_rag_apps.api.main._enqueue_gdrive_job", side_effect=slow_gdrive_enqueue
            ):
                results["gdrive"] = client.post("/api/ingest/gdrive")

        t = threading.Thread(target=call_gdrive)
        t.start()
        assert started_event.wait(timeout=5)

        try:
            with patch("private_rag_apps.api.main._run_ingest_in_background") as mock_bg:
                local_response = client.post("/api/ingest")
            assert local_response.status_code == 409
            mock_bg.assert_not_called()
        finally:
            release_event.set()
            t.join(timeout=5)
            gdrive_run_id = results["gdrive"].json()["id"]
            _cleanup(run_ids=[gdrive_run_id])


class TestGetIngestRunsContractUnchanged:
    def test_response_shape_unchanged_for_api_triggered_gdrive_runs(self):
        db = SessionLocal()
        run = IngestRun(
            trigger="api",
            status="success",
            source_type="google_drive",
            stats={"added": 1, "skipped_items": []},
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
        db.close()

        try:
            response = client.get("/api/ingest/runs")
            assert response.status_code == 200
            entry = next(r for r in response.json() if r["id"] == str(run_id))
            assert set(entry.keys()) == {
                "id",
                "trigger",
                "status",
                "stats",
                "error",
                "started_at",
                "finished_at",
            }
            assert entry["trigger"] == "api"
            assert entry["status"] == "success"
        finally:
            _cleanup(run_ids=[run_id])
