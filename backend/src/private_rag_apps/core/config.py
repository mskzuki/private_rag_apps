from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    database_url: str = "postgresql+psycopg://rag_user:rag_pass@localhost:5432/rag_db"
    corpus_dir: str = "seed/corpus"
    llm_model: str = "claude-3-haiku-20240307"

    # M1 Retrieval Settings
    retrieval_strategy: str = "hybrid_rerank"  # vector, hybrid, hybrid_rerank
    candidate_k: int = 50
    rrf_k: int = 60
    fuse_k: int = 40
    rerank_top_k: int = 8

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

settings = Settings()
