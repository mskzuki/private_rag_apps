import pytest

from private_rag_apps.ingestion import indexer as indexer_module


@pytest.fixture(autouse=True)
def _fast_ingest_embed_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """デフォルトで_pace_embed_callの実待機を無効化し、ペーシングを検証しないテストが
    実時間で待たされないようにする（TestEmbedPacingは自前でtime.sleep/monotonicを
    monkeypatchして上書きするため影響を受けない）。"""
    monkeypatch.setattr(indexer_module.time, "sleep", lambda seconds: None)
