"""ARQ workerのエントリポイント設定（m9_google_drive_ingestion.md §4.6/§4.8）。

`make worker`（`cd backend && uv run arq private_rag_apps.worker.settings.WorkerSettings`）から
dotted pathで参照される。ARQのCLI（`arq.cli`）はこのクラスの属性のうち `Worker.__init__` の
キーワード引数名と一致するものをそのまま渡す（`arq.worker.get_kwargs`）ため、属性名は
ARQ側の規約（`functions`/`redis_settings`/`max_tries` 等）に合わせる必要がある。"""

from arq.connections import RedisSettings

from private_rag_apps.core.config import settings
from private_rag_apps.worker.tasks import run_gdrive_ingestion


class WorkerSettings:
    functions = [run_gdrive_ingestion]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # API経由トリガのARQジョブ最大試行回数（既定3。worker/tasks.pyのリトライ排他ロジックと
    # 同じ設定値を参照するため、job関数側でも `settings.ingest_gdrive_job_max_tries` を直接読む）
    max_tries = settings.ingest_gdrive_job_max_tries
