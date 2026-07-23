"""ARQジョブ関数。`ingestion.execute_gdrive_ingestion()` を呼ぶだけの薄い層で、
Driveスキャン・チャンキング・埋め込み等の業務ロジックは一切持たない
（AGENTS.md §3。`cli/main.py`・`worker/` を同じ位置づけとする方針。m9_tasklist.md T5作業項目2）。

リトライとグローバル排他ロックの整合性（m9_google_drive_ingestion.md §4.6）:
`execute_gdrive_ingestion()` は例外発生時に無条件で `run.status='error'` / `run.finished_at=now` を
commitして例外を再送出する。ARQ側でこの後さらにリトライが控えている場合、その一瞬でも
running行が「存在しない」（status=='running'ではない）状態になると、`start_run()` の排他チェック
（status=='running'の行の有無）が別の取り込み実行を誤って許可してしまい、多重実行の抑止
（m9_google_drive_ingestion.md §4.6が要求するグローバル排他）が破れる。

これを防ぐため、まだリトライ枠が残っている失敗（`ctx["job_try"] < max_tries`）では、
再送出の前にrunを running 状態へ戻してから明示的に `arq.worker.Retry()` を送出してARQに
再試行させる。最終試行（`job_try >= max_tries`）の失敗は `execute_gdrive_ingestion()` 側の
`run.status='error'` 書き込みをそのまま最終状態として残し、ここでは何もしない。

注記: ARQは既定では素の例外を自動リトライしない（`arq.worker.Worker.run_job` は非`Retry`例外を
`finish=True` として即座に失敗終了させる）。自動リトライさせるには明示的に `arq.worker.Retry` を
送出する必要があるため、上記の判定・送出をこの関数自身が担う。

`execute_gdrive_ingestion()` は同期関数（Drive API・埋め込みAPI・DB呼び出しがいずれも同期IO）
のため、`asyncio.to_thread()` で別スレッドへオフロードして呼ぶ。素朴に直接呼ぶと単一スレッドの
イベントループをその間ずっと専有してしまい、ARQの`job_timeout`（`asyncio.wait_for`によるキャンセルは
await地点でしか効かない）やコンテナ停止時のSIGTERMハンドリング（`docker compose down`/再起動）が
取り込み完了まで一切効かなくなる（`WorkerSettings.job_timeout` も参照）。"""

import asyncio
from typing import Any, Dict

from arq.worker import Retry

from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.ingestion.indexer import execute_gdrive_ingestion
from private_rag_apps.models.rag import IngestRun


async def run_gdrive_ingestion(
    ctx: Dict[str, Any], run_id: str, folder_id: str, force_delete: bool = False
) -> Dict[str, str]:
    """`POST /api/ingest/gdrive` が同期作成した running行（run_id）を引き継ぎ、
    `execute_gdrive_ingestion()` を呼ぶだけのジョブ関数。"""
    job_try: int = ctx["job_try"]
    max_tries = settings.ingest_gdrive_job_max_tries

    db = SessionLocal()
    try:
        run = db.query(IngestRun).filter(IngestRun.id == run_id).first()
        if run is None:
            # running行が見つからない（想定外の状態）。ARQ側の再試行対象にはしない
            return {"status": "not_found", "run_id": run_id}

        try:
            await asyncio.to_thread(
                execute_gdrive_ingestion, db, run, folder_id, force_delete=force_delete
            )
        except Exception:
            if job_try < max_tries:
                run.status = "running"
                run.error = None
                run.finished_at = None
                db.commit()
                raise Retry() from None
            # 最終試行の失敗: execute_gdrive_ingestion()が既にrun.status='error'を確定済み
            raise

        return {"status": run.status, "run_id": run_id}
    finally:
        db.close()
