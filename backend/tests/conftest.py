import pytest

from private_rag_apps.ingestion import indexer as indexer_module


@pytest.fixture(autouse=True)
def _fast_ingest_embed_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """デフォルトで_pace_embed_callを無効化し、ペーシングを検証しないテストが
    実時間で待たされないようにする（TestEmbedPacingは自前でtime.sleep/monotonicを
    monkeypatchするため、そちらでは_pace_embed_call自体を後から上書きしてこの
    no-opを無効化する）。"""
    monkeypatch.setattr(indexer_module, "_pace_embed_call", lambda: None)
