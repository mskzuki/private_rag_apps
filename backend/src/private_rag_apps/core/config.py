import os
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


# APIキー、LLMモデル、Embedモデルは.envから読み込み
class Settings(BaseSettings):
    # 基本接続情報
    openai_api_key: str = ""  # OpenAI API キー（generation・evals で使用）
    llm_provider: Literal["openai", "ollama"] = (
        "openai"  # 生成に使うLLMプロバイダ（condense/generate_answer_streamのみ対象。judgeは対象外）
    )
    ollama_base_url: str = "http://localhost:11434/v1"  # Ollama の OpenAI互換エンドポイント（/v1/responses対応。v0.13.3以降が前提）
    ollama_api_key: str = "ollama"  # Ollamaはキー不要だがopenai SDKがダミー値を要求するため固定値
    voyage_api_key: str = ""  # Voyage AI API キー（埋め込み・リランクで使用）
    voyage_max_retries: int = (
        5  # Voyage呼び出し失敗時の再試行回数（SDK組み込みのexponential backoff。レート制限対策）
    )
    langfuse_public_key: str = ""  # Langfuse 公開鍵（任意。未設定なら計装は no-op）
    langfuse_secret_key: str = ""  # Langfuse 秘密鍵（任意。未設定なら計装は no-op）
    langfuse_host: str = "https://cloud.langfuse.com"  # Langfuse 送信先ホスト
    database_url: str = "postgresql+psycopg://rag_user:rag_pass@localhost:5432/rag_dev"  # PostgreSQL(pgvector+pg_bigm)接続文字列。開発/デモ用DB（テストは rag_test を使う。docs/architecture.md §9）
    corpus_dir: str = "seed/corpus"  # 取り込み対象コーパス（Markdown/テキスト）のディレクトリ
    llm_model: str = ""  # 回答生成に使う OpenAI モデル
    embed_model: str = ""  # 埋め込みに使う Voyage モデル（ingestion/retrieval で共有）

    # Retrieval Settings
    retrieval_strategy: str = "hybrid_rerank"  # vector, hybrid, hybrid_rerank
    candidate_k: int = 50  # 各検索経路（ベクトル/全文）から取得する候補件数
    rrf_k: int = 60  # RRF(Reciprocal Rank Fusion)融合の減衰パラメータ
    fuse_k: int = 40  # RRF 融合後に残す件数
    rerank_top_k: int = 8  # リランク後、最終的にコンテキストとして使う件数
    # Chat & Streaming Settings
    condense_model: str = ""  # クエリ書き換え(query condensation)に使うモデル
    condense_history_turns: int = 5  # クエリ書き換え時に考慮する直近の会話ターン数
    chat_history_token_budget: int = 1000  # プロンプトに含める会話履歴のトークン上限
    sse_keepalive_sec: int = 15  # SSE 接続の keep-alive 送信間隔（秒）
    title_max_chars: int = 40  # スレッドタイトルの最大文字数

    # Evaluation Settings
    judge_model: str = ""
    judge_temperature: float = 0.0
    eval_gen_temperature: float = 0.0
    eval_gen_max_tokens: int = 1024
    eval_top_k: int = 12
    eval_ef_search: int = 100  # 大きめの値
    eval_judge_samples: int = 1
    eval_dataset_path: str = "evals/dataset/m3_golden.jsonl"

    # Routing Settings (M7 adaptive routing)
    routing_theta: float = 0.56  # grade の grounded/direct 分岐閾値(THETA)。rerank_score >= routing_theta のchunkのみkeepする。ADR 0001でキャリブレーション決定した値をデフォルトに採用（docs/adr/0001_m7_theta_threshold.md）

    # Ingestion Settings
    ingest_delete_guard_ratio: float = (
        0.5  # 削除安全弁: 生存source比がこの値未満なら削除フェーズを中断（暫定値）
    )
    ingest_advisory_lock_key: int = 727110001  # 多重実行抑止（開始の原子性）用 advisory lock キー
    ingest_stale_running_sec: int = 600  # running行をstaleとみなす経過秒（暫定値。実測後見直し）
    ingest_stats_flush_every: int = 10  # 走査N件ごとにingest_runs.statsを逐次UPDATE
    ingest_embed_batch_size: int = 64  # Voyage embed呼び出し1回あたりのチャンク数上限
    ingest_embed_min_interval_sec: float = 21.0  # Voyage embed呼び出し間の最低待機秒数（レート制限予防のペーシング。無支払い枠3RPM=20秒間隔が理論上限）
    ingest_trigger: Literal["cli", "demo"] = "cli"  # CLI --trigger 省略時の既定値（INGEST_TRIGGER）
    force_delete: bool = False  # CLI --force-delete 省略時の既定値（FORCE_DELETE）

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

# langfuse の @observe()/get_client() はプロセス環境変数を直接参照するため、
# pydantic-settings が読み込んだ .env の値をここで反映する（未設定なら何もせず no-op のまま。NFR-4）
if settings.langfuse_public_key:
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
if settings.langfuse_secret_key:
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
if settings.langfuse_host:
    os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
