import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from googleapiclient.errors import HttpError

from private_rag_apps.core.config import settings
from private_rag_apps.ingestion.gdrive_client import (
    GOOGLE_DOC_MIME_TYPE,
    DriveFile,
    GoogleDriveAccessError,
    GoogleDriveAuthError,
    GoogleDriveClient,
    GoogleDriveConfigError,
)

FAKE_SERVICE_ACCOUNT_EMAIL = "m9-test-sa@m9-test-project.iam.gserviceaccount.com"


def _write_valid_service_account_key(path: Path, email: str = FAKE_SERVICE_ACCOUNT_EMAIL) -> None:
    """実際に google-auth が読み込める最小限に妥当なサービスアカウントJSONキーを書き出す
    （private_keyはローカル生成のRSA鍵。ネットワークアクセスは一切発生しない）。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    info = {
        "type": "service_account",
        "project_id": "m9-test-project",
        "private_key_id": "abc123",
        "private_key": pem,
        "client_email": email,
        "client_id": "12345",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    path.write_text(json.dumps(info), encoding="utf-8")


def _http_error(status: int, message: str = "error") -> HttpError:
    resp = httplib2.Response({"status": status})
    content = json.dumps({"error": {"message": message}}).encode("utf-8")
    return HttpError(resp, content)


@pytest.fixture()
def valid_key_file(tmp_path: Path) -> Path:
    key_path = tmp_path / "service_account.json"
    _write_valid_service_account_key(key_path)
    return key_path


@pytest.fixture()
def configured_settings(monkeypatch: pytest.MonkeyPatch, valid_key_file: Path):
    """DRIVE_FOLDER_ID/DRIVE_SERVICE_ACCOUNT_FILEを有効な値に設定する（成功系テスト用）"""
    monkeypatch.setattr(settings, "drive_folder_id", "folder-abc123")
    monkeypatch.setattr(settings, "drive_service_account_file", str(valid_key_file))
    return settings


def _mock_service() -> MagicMock:
    """googleapiclientのDrive Serviceリソース全体を模したMagicMock
    （private_rag_apps.ingestion.indexer.voyageaiをまるごとpatchする既存の
    モック方式に倣う。実Drive APIには一切アクセスしない）。"""
    return MagicMock()


class TestConfigValidation:
    """DRIVE_FOLDER_ID/DRIVE_SERVICE_ACCOUNT_FILEが空の場合、即座に分かりやすいエラーになること"""

    def test_empty_folder_id_raises_config_error(self, monkeypatch, valid_key_file):
        monkeypatch.setattr(settings, "drive_folder_id", "")
        monkeypatch.setattr(settings, "drive_service_account_file", str(valid_key_file))
        with pytest.raises(GoogleDriveConfigError, match="DRIVE_FOLDER_ID"):
            GoogleDriveClient()

    def test_empty_service_account_file_raises_config_error(self, monkeypatch):
        monkeypatch.setattr(settings, "drive_folder_id", "folder-abc123")
        monkeypatch.setattr(settings, "drive_service_account_file", "")
        with pytest.raises(GoogleDriveConfigError, match="DRIVE_SERVICE_ACCOUNT_FILE"):
            GoogleDriveClient()

    def test_both_empty_raises_config_error(self, monkeypatch):
        monkeypatch.setattr(settings, "drive_folder_id", "")
        monkeypatch.setattr(settings, "drive_service_account_file", "")
        with pytest.raises(GoogleDriveConfigError):
            GoogleDriveClient()


class TestAuthFailures:
    """サービスアカウントキーファイルが不在・不正な場合、即座に分かりやすいエラーになること"""

    def test_missing_key_file_raises_auth_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "drive_folder_id", "folder-abc123")
        monkeypatch.setattr(
            settings, "drive_service_account_file", str(tmp_path / "does_not_exist.json")
        )
        with pytest.raises(GoogleDriveAuthError, match="見つかりません"):
            GoogleDriveClient()

    def test_malformed_json_raises_auth_error(self, monkeypatch, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json", encoding="utf-8")
        monkeypatch.setattr(settings, "drive_folder_id", "folder-abc123")
        monkeypatch.setattr(settings, "drive_service_account_file", str(bad_file))
        with pytest.raises(GoogleDriveAuthError):
            GoogleDriveClient()

    def test_missing_required_fields_raises_auth_error(self, monkeypatch, tmp_path):
        incomplete_file = tmp_path / "incomplete.json"
        incomplete_file.write_text(json.dumps({"type": "service_account"}), encoding="utf-8")
        monkeypatch.setattr(settings, "drive_folder_id", "folder-abc123")
        monkeypatch.setattr(settings, "drive_service_account_file", str(incomplete_file))
        with pytest.raises(GoogleDriveAuthError):
            GoogleDriveClient()


class TestListChildren:
    def test_list_children_parses_response_fields(self, configured_settings):
        service = _mock_service()
        service.files.return_value.list.return_value.execute.return_value = {
            "files": [
                {
                    "id": "file-1",
                    "name": "note.md",
                    "mimeType": "text/markdown",
                    "modifiedTime": "2026-07-01T10:00:00.000Z",
                    "webViewLink": "https://drive.google.com/file/d/file-1/view",
                    "parents": ["folder-abc123"],
                }
            ],
            "nextPageToken": None,
        }
        with patch("private_rag_apps.ingestion.gdrive_client.build", return_value=service):
            client = GoogleDriveClient()
            result = client.list_children("folder-abc123")

        assert result == [
            DriveFile(
                id="file-1",
                name="note.md",
                mime_type="text/markdown",
                modified_time=result[0].modified_time,
                web_view_link="https://drive.google.com/file/d/file-1/view",
                parents=["folder-abc123"],
            )
        ]
        assert result[0].modified_time is not None
        assert result[0].modified_time.year == 2026
        service.files.return_value.list.assert_called_once()
        called_kwargs = service.files.return_value.list.call_args.kwargs
        assert called_kwargs["q"] == "'folder-abc123' in parents and trashed = false"

    def test_list_children_follows_page_token(self, configured_settings):
        service = _mock_service()
        service.files.return_value.list.return_value.execute.side_effect = [
            {
                "files": [
                    {"id": "file-1", "name": "a.txt", "mimeType": "text/plain", "parents": ["f"]}
                ],
                "nextPageToken": "PAGE2",
            },
            {
                "files": [
                    {"id": "file-2", "name": "b.txt", "mimeType": "text/plain", "parents": ["f"]}
                ],
                "nextPageToken": None,
            },
        ]
        with patch("private_rag_apps.ingestion.gdrive_client.build", return_value=service):
            client = GoogleDriveClient()
            result = client.list_children("folder-abc123")

        assert [f.id for f in result] == ["file-1", "file-2"]
        assert service.files.return_value.list.call_count == 2
        second_call_kwargs = service.files.return_value.list.call_args_list[1].kwargs
        assert second_call_kwargs["pageToken"] == "PAGE2"

    def test_list_children_does_not_recurse_into_subfolders(self, configured_settings):
        """薄いラッパーであることの確認: フォルダ種別のchildが返っても、list_children自身は
        再帰探索を行わずそのままDriveFileとして返す（再帰はT3の責務）"""
        service = _mock_service()
        service.files.return_value.list.return_value.execute.return_value = {
            "files": [
                {
                    "id": "subfolder-1",
                    "name": "SubDir",
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": ["folder-abc123"],
                }
            ],
            "nextPageToken": None,
        }
        with patch("private_rag_apps.ingestion.gdrive_client.build", return_value=service):
            client = GoogleDriveClient()
            result = client.list_children("folder-abc123")

        assert len(result) == 1
        assert result[0].mime_type == "application/vnd.google-apps.folder"
        # list()はfolder-abc123の直下に対してのみ呼ばれ、subfolder-1に対しては呼ばれない
        service.files.return_value.list.assert_called_once()

    def test_folder_not_found_or_not_shared_raises_access_error_with_email_and_instructions(
        self, configured_settings
    ):
        service = _mock_service()
        service.files.return_value.list.return_value.execute.side_effect = _http_error(404)
        with patch("private_rag_apps.ingestion.gdrive_client.build", return_value=service):
            client = GoogleDriveClient()
            with pytest.raises(GoogleDriveAccessError) as exc_info:
                client.list_children("missing-folder")

        message = str(exc_info.value)
        assert FAKE_SERVICE_ACCOUNT_EMAIL in message
        assert "閲覧者" in message
        assert "共有" in message

    def test_folder_forbidden_raises_access_error_with_email_and_instructions(
        self, configured_settings
    ):
        service = _mock_service()
        service.files.return_value.list.return_value.execute.side_effect = _http_error(403)
        with patch("private_rag_apps.ingestion.gdrive_client.build", return_value=service):
            client = GoogleDriveClient()
            with pytest.raises(GoogleDriveAccessError) as exc_info:
                client.list_children("unshared-folder")

        assert FAKE_SERVICE_ACCOUNT_EMAIL in str(exc_info.value)

    def test_unauthorized_raises_auth_error(self, configured_settings):
        service = _mock_service()
        service.files.return_value.list.return_value.execute.side_effect = _http_error(401)
        with patch("private_rag_apps.ingestion.gdrive_client.build", return_value=service):
            client = GoogleDriveClient()
            with pytest.raises(GoogleDriveAuthError):
                client.list_children("folder-abc123")

    def test_unexpected_http_error_propagates_unwrapped(self, configured_settings):
        service = _mock_service()
        service.files.return_value.list.return_value.execute.side_effect = _http_error(500)
        with patch("private_rag_apps.ingestion.gdrive_client.build", return_value=service):
            client = GoogleDriveClient()
            with pytest.raises(HttpError):
                client.list_children("folder-abc123")


class TestDownloadContent:
    def test_download_plain_file_uses_get_media(self, configured_settings):
        service = _mock_service()
        service.files.return_value.get_media.return_value.execute.return_value = b"hello world"
        file = DriveFile(
            id="file-1",
            name="note.txt",
            mime_type="text/plain",
            modified_time=None,
            web_view_link=None,
            parents=["folder-abc123"],
        )
        with patch("private_rag_apps.ingestion.gdrive_client.build", return_value=service):
            client = GoogleDriveClient()
            content = client.download_content(file)

        assert content == b"hello world"
        service.files.return_value.get_media.assert_called_once_with(fileId="file-1")
        service.files.return_value.export.assert_not_called()

    def test_download_google_doc_uses_export_as_plain_text(self, configured_settings):
        service = _mock_service()
        service.files.return_value.export.return_value.execute.return_value = b"exported text"
        file = DriveFile(
            id="doc-1",
            name="My Doc",
            mime_type=GOOGLE_DOC_MIME_TYPE,
            modified_time=None,
            web_view_link=None,
            parents=["folder-abc123"],
        )
        with patch("private_rag_apps.ingestion.gdrive_client.build", return_value=service):
            client = GoogleDriveClient()
            content = client.download_content(file)

        assert content == b"exported text"
        service.files.return_value.export.assert_called_once_with(fileId="doc-1", mimeType="text/plain")
        service.files.return_value.get_media.assert_not_called()

    def test_download_failure_raises_access_error(self, configured_settings):
        service = _mock_service()
        service.files.return_value.get_media.return_value.execute.side_effect = _http_error(404)
        file = DriveFile(
            id="file-1",
            name="gone.txt",
            mime_type="text/plain",
            modified_time=None,
            web_view_link=None,
            parents=["folder-abc123"],
        )
        with patch("private_rag_apps.ingestion.gdrive_client.build", return_value=service):
            client = GoogleDriveClient()
            with pytest.raises(GoogleDriveAccessError, match="gone.txt"):
                client.download_content(file)


class TestServiceAccountEmail:
    def test_service_account_email_reflects_key_file(self, configured_settings):
        service = _mock_service()
        with patch("private_rag_apps.ingestion.gdrive_client.build", return_value=service):
            client = GoogleDriveClient()
        assert client.service_account_email == FAKE_SERVICE_ACCOUNT_EMAIL
