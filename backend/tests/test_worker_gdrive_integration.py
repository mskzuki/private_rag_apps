"""実Redis + 実ARQ Worker（burstモード）を使った統合テスト（M9 T5）。

m9_tasklist.md T5完了条件のうち「ローカルで起動したARQ worker（実Redis、モックDriveクライアント）
がジョブを実際に消費しexecute_gdrive_ingestion()を呼ぶ」を検証する。

このテストは実際にRedisへ到達できることを前提とする（docker-compose.ymlの`redis`サービス。
`docker compose up -d redis`で起動、または`make setup`後）。既存のDB統合テストが実Postgres
（rag_test。AGENTS.md §8）を前提とし到達性チェックを行わないのと同じ哲学で、ここでも
到達性チェックは行わない（未起動なら接続エラーで分かりやすく失敗する）。

実プロセスとしてワーカーを別プロセス起動する代わりに、`arq.worker.Worker`をburst=Trueで
直接インスタンス化し、キューが空になるまで処理させてから終了する。ARQ本体が公式に
提供するテスト向けの実行経路（`Worker(burst=True)` → `async_run()`）であり、実際に
`make worker`が起動するのと同じWorkerクラス・同じジョブ関数登録経路を通る。

Driveクライアントはgdrive_loader.GoogleDriveClientの境界でモックする（T2/T3/T4テストと
同じパターン）。実際のDrive APIは一切呼ばない。埋め込みも同様にvoyageaiをモックする。
"""

import asyncio
import datetime
import uuid
from unittest.mock import MagicMock, patch

from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker

from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.ingestion.concurrency import start_run
from private_rag_apps.ingestion.gdrive_client import DriveFile
from private_rag_apps.models.rag import Chunk, IngestRun, Source
from private_rag_apps.worker.tasks import run_gdrive_ingestion

FAKE_EMBEDDING = [0.1] * 1024


def _mock_embed(mock_voyage):
    mock_voyage.Client.return_value.embed.side_effect = (
        lambda texts, **kwargs: type("R", (), {"embeddings": [FAKE_EMBEDDING for _ in texts]})()
    )


def _cleanup(db, external_ids=None, run_ids=None) -> None:
    if external_ids:
        sources = db.query(Source).filter(Source.external_id.in_(external_ids)).all()
        for s in sources:
            db.query(Chunk).filter(Chunk.source_id == s.id).delete()
        db.query(Source).filter(Source.external_id.in_(external_ids)).delete(synchronize_session=False)
    if run_ids:
        db.query(IngestRun).filter(IngestRun.id.in_(run_ids)).delete(synchronize_session=False)
    db.commit()


async def _enqueue_and_drain(run_id: str, folder_id: str) -> None:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    pool = await create_pool(redis_settings)
    try:
        await pool.enqueue_job("run_gdrive_ingestion", run_id, folder_id, False)
    finally:
        await pool.aclose()

    # `make worker`が起動するのと同じWorkerクラス・同じジョブ関数を、burst=True（キューが
    # 空になったら自動停止）で動かす。実プロセスの起動はしないが、enqueue→実Redis経由での
    # dequeue→ジョブ関数呼び出しという経路は本物のARQを通る
    worker = Worker(
        functions=[run_gdrive_ingestion],
        redis_settings=redis_settings,
        burst=True,
    )
    try:
        await worker.async_run()
    finally:
        await worker.close()


class TestArqWorkerConsumesGdriveJob:
    def test_worker_consumes_enqueued_job_and_calls_execute_gdrive_ingestion(self):
        external_id = f"drv-{uuid.uuid4()}"
        run_ids: list = []
        db = SessionLocal()
        try:
            run = start_run(db, trigger="api")
            run_ids.append(run.id)
            run_id = str(run.id)

            drive_file = DriveFile(
                id=external_id,
                name="worker-e2e.md",
                mime_type="text/markdown",
                modified_time=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
                web_view_link=None,
                parents=["worker-e2e-root"],
            )
            mock_client = MagicMock()
            mock_client.list_children.side_effect = (
                lambda folder_id: [drive_file] if folder_id == "worker-e2e-root" else []
            )
            mock_client.download_content.return_value = b"# Worker E2E\n\nreal redis, real arq worker"

            with (
                patch(
                    "private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient",
                    return_value=mock_client,
                ),
                patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage,
            ):
                _mock_embed(mock_voyage)
                asyncio.run(_enqueue_and_drain(run_id, "worker-e2e-root"))

            db.refresh(run)
            assert run.status == "success"
            assert run.source_type == "google_drive"

            source = db.query(Source).filter(Source.external_id == external_id).first()
            assert source is not None
            chunks = db.query(Chunk).filter(Chunk.source_id == source.id).all()
            assert len(chunks) > 0
        finally:
            _cleanup(db, external_ids=[external_id], run_ids=run_ids)
            db.close()
