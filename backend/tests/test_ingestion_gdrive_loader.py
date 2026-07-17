import ast
import datetime
import inspect
import uuid
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

from private_rag_apps.core.db import SessionLocal
from private_rag_apps.ingestion.gdrive_client import GOOGLE_DOC_MIME_TYPE, DriveFile
from private_rag_apps.ingestion.gdrive_loader import load_drive
import private_rag_apps.ingestion.gdrive_loader as gdrive_loader_module
from private_rag_apps.models.rag import Source

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SHORTCUT_MIME_TYPE = "application/vnd.google-apps.shortcut"


def _drive_file(
    file_id: str,
    name: str,
    mime_type: str,
    modified_time: Optional[datetime.datetime] = None,
    web_view_link: Optional[str] = None,
    parents: Optional[List[str]] = None,
) -> DriveFile:
    return DriveFile(
        id=file_id,
        name=name,
        mime_type=mime_type,
        modified_time=modified_time,
        web_view_link=web_view_link,
        parents=parents or ["root-folder"],
    )


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


def _cleanup_sources(db, external_ids: List[str]) -> None:
    db.query(Source).filter(Source.external_id.in_(external_ids)).delete(synchronize_session=False)
    db.commit()


class TestRecursiveTraversal:
    """フォルダ再帰探索（子フォルダ・ページネーション）のテスト（完了条件1）"""

    def test_recurses_into_subfolders_and_builds_breadcrumb_path(self, db):
        client = MagicMock()
        subfolder = _drive_file("sub-1", "SubDir", FOLDER_MIME_TYPE)
        root_file = _drive_file(
            "file-1", "root.md", "text/markdown", modified_time=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        )
        nested_file = _drive_file(
            "file-2", "nested.txt", "text/plain", modified_time=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        )

        def list_children(folder_id: str):
            if folder_id == "root-folder":
                return [subfolder, root_file]
            if folder_id == "sub-1":
                return [nested_file]
            raise AssertionError(f"unexpected folder_id {folder_id}")

        client.list_children.side_effect = list_children
        client.download_content.return_value = b"content"

        with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
            result = load_drive(db, "root-folder")

        paths = {d.path for d in result.documents}
        assert paths == {"root.md", "SubDir/nested.txt"}
        client.list_children.assert_any_call("root-folder")
        client.list_children.assert_any_call("sub-1")
        assert client.list_children.call_count == 2

    def test_deeply_nested_folders_accumulate_breadcrumb(self, db):
        client = MagicMock()
        level1 = _drive_file("l1", "Level1", FOLDER_MIME_TYPE)
        level2 = _drive_file("l2", "Level2", FOLDER_MIME_TYPE)
        deep_file = _drive_file("f1", "deep.txt", "text/plain", modified_time=None)

        def list_children(folder_id: str):
            return {
                "root-folder": [level1],
                "l1": [level2],
                "l2": [deep_file],
            }[folder_id]

        client.list_children.side_effect = list_children
        client.download_content.return_value = b"deep content"

        with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
            result = load_drive(db, "root-folder")

        assert [d.path for d in result.documents] == ["Level1/Level2/deep.txt"]

    def test_list_children_called_once_per_folder_regardless_of_child_count(self, db):
        """`list_children` はページネーションを内部で処理済みで全件を返す（T2の責務）ため、
        ローダー側は1フォルダにつき1回呼べば足りる（ローダー側で独自にページングをやり直さない）ことを確認する。
        """
        client = MagicMock()
        many_files = [
            _drive_file(f"file-{i}", f"note-{i}.txt", "text/plain", modified_time=None) for i in range(250)
        ]
        client.list_children.return_value = many_files
        client.download_content.return_value = b"x"

        with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
            result = load_drive(db, "root-folder")

        assert len(result.documents) == 250
        assert client.list_children.call_count == 1


class TestMimeTypeFiltering:
    """mimeType判定 + 拡張子救済判定のテスト（完了条件2、曖昧なmimeTypeのケースを含む）"""

    def test_supported_mime_types_are_included(self, db):
        client = MagicMock()
        client.list_children.return_value = [
            _drive_file("f1", "a.txt", "text/plain", modified_time=None),
            _drive_file("f2", "b.md", "text/markdown", modified_time=None),
            _drive_file("f3", "My Doc", GOOGLE_DOC_MIME_TYPE, modified_time=None),
        ]
        client.download_content.return_value = b"content"

        with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
            result = load_drive(db, "root-folder")

        assert {d.external_id for d in result.documents} == {"f1", "f2", "f3"}
        assert result.skipped == []

    def test_ambiguous_mimetype_rescued_by_extension(self, db):
        client = MagicMock()
        client.list_children.return_value = [
            _drive_file("f1", "notes.md", "application/octet-stream", modified_time=None),
        ]
        client.download_content.return_value = b"rescued content"

        with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
            result = load_drive(db, "root-folder")

        assert len(result.documents) == 1
        assert result.documents[0].external_id == "f1"
        assert result.skipped == []

    def test_ambiguous_mimetype_without_rescue_extension_is_skipped(self, db):
        client = MagicMock()
        client.list_children.return_value = [
            _drive_file("f1", "image.bin", "application/octet-stream", modified_time=None),
        ]

        with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
            result = load_drive(db, "root-folder")

        assert result.documents == []
        assert len(result.skipped) == 1
        assert result.skipped[0].name == "image.bin"
        assert "mimeType" in result.skipped[0].reason
        client.download_content.assert_not_called()

    def test_unsupported_mimetype_is_skipped_and_recorded(self, db, capsys):
        client = MagicMock()
        client.list_children.return_value = [
            _drive_file("f1", "photo.png", "image/png", modified_time=None),
        ]

        with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
            result = load_drive(db, "root-folder")

        assert result.documents == []
        assert len(result.skipped) == 1
        skipped = result.skipped[0]
        assert skipped.name == "photo.png"
        assert skipped.path == "photo.png"
        assert "image/png" in skipped.reason
        client.download_content.assert_not_called()

        captured = capsys.readouterr()
        assert "Skipping photo.png" in captured.out

    def test_shortcut_is_skipped_and_recorded_without_resolving_target(self, db):
        client = MagicMock()
        client.list_children.return_value = [
            _drive_file("shortcut-1", "link-to-doc", SHORTCUT_MIME_TYPE, modified_time=None),
        ]

        with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
            result = load_drive(db, "root-folder")

        assert result.documents == []
        assert len(result.skipped) == 1
        assert result.skipped[0].name == "link-to-doc"
        assert "ショートカット" in result.skipped[0].reason
        # ショートカットの解決（リンク先取得）は行わない: list_childrenがshortcut-1をfolder_idとして
        # 再度呼ばれていないことを確認する
        client.list_children.assert_called_once_with("root-folder")
        client.download_content.assert_not_called()


class TestModifiedTimePrefilter:
    """`modifiedTime` 事前フィルタのテスト（完了条件4。実DBを使う）"""

    def test_unchanged_modified_time_skips_download(self, db):
        external_id = f"drive-{uuid.uuid4()}"
        modified = datetime.datetime(2026, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
        source = Source(
            path="Docs/note.md",
            title="note",
            content_hash="existinghash",
            source_type="google_drive",
            external_id=external_id,
            source_url="https://drive.google.com/file/d/x/view",
            source_updated_at=modified,
        )
        db.add(source)
        db.commit()
        try:
            client = MagicMock()
            client.list_children.return_value = [
                _drive_file(external_id, "note.md", "text/markdown", modified_time=modified)
            ]

            with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
                result = load_drive(db, "root-folder")

            assert result.documents == []
            client.download_content.assert_not_called()
            assert result.found_external_ids == {external_id}
        finally:
            _cleanup_sources(db, [external_id])

    def test_changed_modified_time_downloads_and_includes_document(self, db):
        external_id = f"drive-{uuid.uuid4()}"
        old_modified = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        new_modified = datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc)
        source = Source(
            path="Docs/note.md",
            title="note",
            content_hash="existinghash",
            source_type="google_drive",
            external_id=external_id,
            source_url="https://drive.google.com/file/d/x/view",
            source_updated_at=old_modified,
        )
        db.add(source)
        db.commit()
        try:
            client = MagicMock()
            client.list_children.return_value = [
                _drive_file(external_id, "note.md", "text/markdown", modified_time=new_modified)
            ]
            client.download_content.return_value = "new content".encode("utf-8")

            with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
                result = load_drive(db, "root-folder")

            assert len(result.documents) == 1
            doc = result.documents[0]
            assert doc.external_id == external_id
            assert doc.content == "new content"
            client.download_content.assert_called_once()
            assert result.found_external_ids == {external_id}
        finally:
            _cleanup_sources(db, [external_id])

    def test_new_file_without_existing_source_downloads(self, db):
        external_id = f"drive-{uuid.uuid4()}"
        client = MagicMock()
        client.list_children.return_value = [
            _drive_file(external_id, "brand-new.txt", "text/plain", modified_time=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
        ]
        client.download_content.return_value = "fresh content".encode("utf-8")

        with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
            result = load_drive(db, "root-folder")

        assert len(result.documents) == 1
        assert result.documents[0].external_id == external_id
        client.download_content.assert_called_once()

    def test_soft_deleted_source_redownloads_even_if_modified_time_unchanged(self, db):
        """ソフトデリート済み（deleted_at設定済み）のDriveソースは、modifiedTimeが無変化でも
        ダウンロードし直す（T4のclassify()がREVIVE_ONLY/REPLACEを判定するにはcontent_hashが必要なため）。
        """
        external_id = f"drive-{uuid.uuid4()}"
        modified = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        source = Source(
            path="Docs/note.md",
            title="note",
            content_hash="existinghash",
            source_type="google_drive",
            external_id=external_id,
            source_url="https://drive.google.com/file/d/x/view",
            source_updated_at=modified,
            deleted_at=datetime.datetime.now(datetime.timezone.utc),
        )
        db.add(source)
        db.commit()
        try:
            client = MagicMock()
            client.list_children.return_value = [
                _drive_file(external_id, "note.md", "text/markdown", modified_time=modified)
            ]
            client.download_content.return_value = "same content".encode("utf-8")

            with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
                result = load_drive(db, "root-folder")

            client.download_content.assert_called_once()
            assert len(result.documents) == 1
            assert result.documents[0].external_id == external_id
        finally:
            _cleanup_sources(db, [external_id])

    def test_mixed_changed_and_unchanged_files_found_external_ids_includes_both(self, db):
        unchanged_id = f"drive-{uuid.uuid4()}"
        changed_id = f"drive-{uuid.uuid4()}"
        modified = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        db.add(
            Source(
                path="a.md",
                title="a",
                content_hash="hash-a",
                source_type="google_drive",
                external_id=unchanged_id,
                source_updated_at=modified,
            )
        )
        db.add(
            Source(
                path="b.md",
                title="b",
                content_hash="hash-b",
                source_type="google_drive",
                external_id=changed_id,
                source_updated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            )
        )
        db.commit()
        try:
            client = MagicMock()
            client.list_children.return_value = [
                _drive_file(unchanged_id, "a.md", "text/markdown", modified_time=modified),
                _drive_file(changed_id, "b.md", "text/markdown", modified_time=modified),
            ]
            client.download_content.return_value = b"new b content"

            with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
                result = load_drive(db, "root-folder")

            assert {d.external_id for d in result.documents} == {changed_id}
            assert result.found_external_ids == {unchanged_id, changed_id}
            client.download_content.assert_called_once()
        finally:
            _cleanup_sources(db, [unchanged_id, changed_id])


class TestDocumentAssembly:
    """出力Documentがsource_type/external_id/source_urlを正しく持つことのテスト（作業項目4）"""

    def test_document_has_drive_fields_and_extracted_title(self, db):
        client = MagicMock()
        client.list_children.return_value = [
            _drive_file(
                "file-1",
                "note.md",
                "text/markdown",
                modified_time=None,
                web_view_link="https://drive.google.com/file/d/file-1/view",
            )
        ]
        client.download_content.return_value = "# My Title\n\nbody text".encode("utf-8")

        with patch("private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client):
            result = load_drive(db, "root-folder")

        doc = result.documents[0]
        assert doc.source_type == "google_drive"
        assert doc.external_id == "file-1"
        assert doc.source_url == "https://drive.google.com/file/d/file-1/view"
        assert doc.title == "My Title"
        assert doc.content_hash  # sha256はDocument.__init__が自動計算する


class TestDoesNotReimplementClassify:
    """完了条件5: classify()自体を変更せず、独自の変更検知ロジックを外に持ち込んでいないことの
    コードリーディング用セルフチェック（brief記載の「grep/コードレビューで十分」に対応する自動化版）。
    """

    def test_module_source_never_calls_classify(self):
        """docstring内で classify() に言及すること自体は許容する（「T4に委譲する」旨の説明のため）が、
        実際のコード（AST）としては classify の呼び出しも `ingestion.diff` のimportも存在しないことを
        確認する（docstringの文字列一致では誤検知するため、コメント・docstringを除いたASTを見る）。
        """
        source = inspect.getsource(gdrive_loader_module)
        tree = ast.parse(source)

        call_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "classify" not in call_names

        imported_modules = {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        assert "diff" not in imported_modules
        assert not any(m is not None and m.endswith(".diff") for m in imported_modules)

    def test_module_does_not_import_diff_module(self):
        assert not hasattr(gdrive_loader_module, "classify")
        assert not hasattr(gdrive_loader_module, "Action")
