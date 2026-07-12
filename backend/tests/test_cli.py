from unittest.mock import MagicMock, patch

import pytest

from private_rag_apps.cli.main import ingest, main
from private_rag_apps.core.config import settings


class TestIngestExitCode:
    def test_exits_nonzero_when_run_status_is_error(self):
        fake_run = MagicMock(status="error", error="delete phase aborted: scan found 0 files")
        with (
            patch("private_rag_apps.cli.main.SessionLocal"),
            patch("private_rag_apps.cli.main.run_ingestion", return_value=fake_run),
        ):
            with pytest.raises(SystemExit) as exc_info:
                ingest(trigger="cli", force_delete=False)
        assert exc_info.value.code == 1

    def test_does_not_exit_when_run_status_is_success(self):
        fake_run = MagicMock(status="success", error=None)
        with (
            patch("private_rag_apps.cli.main.SessionLocal"),
            patch("private_rag_apps.cli.main.run_ingestion", return_value=fake_run),
        ):
            ingest(trigger="cli", force_delete=False)


class TestCliDefaultsFromSettings:
    def test_trigger_and_force_delete_defaults_come_from_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "ingest_trigger", "demo")
        monkeypatch.setattr(settings, "force_delete", True)
        monkeypatch.setattr("sys.argv", ["private-rag-apps", "ingest"])
        with patch("private_rag_apps.cli.main.ingest") as mock_ingest:
            main()
        mock_ingest.assert_called_once_with(trigger="demo", force_delete=True)

    def test_explicit_cli_flags_override_settings_defaults(self, monkeypatch):
        monkeypatch.setattr(settings, "ingest_trigger", "demo")
        monkeypatch.setattr(settings, "force_delete", True)
        monkeypatch.setattr("sys.argv", ["private-rag-apps", "ingest", "--trigger", "cli"])
        with patch("private_rag_apps.cli.main.ingest") as mock_ingest:
            main()
        mock_ingest.assert_called_once_with(trigger="cli", force_delete=True)
