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
