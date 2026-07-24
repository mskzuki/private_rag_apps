import pytest
from pydantic import ValidationError

from private_rag_apps.core.config import Settings


class TestIngestTriggerValidation:
    def test_valid_trigger_values_accepted(self):
        assert Settings(ingest_trigger="cli").ingest_trigger == "cli"
        assert Settings(ingest_trigger="demo").ingest_trigger == "demo"

    def test_invalid_trigger_value_is_rejected(self):
        with pytest.raises(ValidationError):
            Settings(ingest_trigger="not-a-real-trigger")


class TestDatabaseUrlConstruction:
    def test_builds_url_from_components_by_default(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        settings = Settings(
            db_host="dbhost", db_port=1234, db_user="u", db_pass="p", db_name="n"
        )
        assert settings.database_url == "postgresql+psycopg://u:p@dbhost:1234/n"

    def test_explicit_database_url_takes_precedence(self):
        settings = Settings(
            database_url="postgresql+psycopg://x:y@z:1/w", db_host="ignored"
        )
        assert settings.database_url == "postgresql+psycopg://x:y@z:1/w"
