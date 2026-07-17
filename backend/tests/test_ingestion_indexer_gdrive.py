"""ingestion/indexer.py の Google Drive 統合（M9 T4）テスト。

観点（m9_tasklist.md T4 完了条件）:
1. execute_gdrive_ingestion() がチャンキング以降で execute_ingestion() と共通のコードパスを通る
   （REPLACE/INSERT/embedding が indexer.voyageai 経由で同一に動く。TestSharedChunkingPath）
2. ソース照合が source_type に応じて正しく分岐する（同一pathのローカル/Driveソースが
   誤って同一視されない。TestSourceTypeScopedMatching）
3. 削除安全弁・削除検知が source_type ごとに独立して判定される
   （TestDeletionPhaseSourceTypeIsolation。本ファイルで最も安全性が重要なテスト）
4. `make ingest-gdrive` 相当のCLIコードパスがモック化したDriveクライアントに対して
   一気通貫で動作する（TestCliEndToEnd）
5. skipped（ショートカット/非対応mimeType）が run.stats に畳み込まれる（TestSkippedItemsInStats）

Driveクライアントは gdrive_loader.GoogleDriveClient の境界でモックする（T2/T3のテストと同じ
パターン）。実際のDrive APIは一切呼ばない。DB側はモックせず、make test 規約の実DB（rag_test）を
使う（AGENTS.md §8、CLAUDE.md 経由のプロジェクト規約）。
"""

import datetime
import uuid
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import or_

from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.ingestion.gdrive_client import DriveFile
from private_rag_apps.ingestion.gdrive_loader import DriveLoadResult, SkippedItem
from private_rag_apps.ingestion.indexer import run_gdrive_ingestion, run_ingestion
from private_rag_apps.ingestion.loader import Document
from private_rag_apps.models.rag import Chunk, IngestRun, Source

FAKE_EMBEDDING = [0.1] * 1024


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


def _write_corpus(tmp_path, files: dict[str, str]):
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


def _mock_embed(mock_voyage):
    mock_voyage.Client.return_value.embed.side_effect = (
        lambda texts, **kwargs: type("R", (), {"embeddings": [FAKE_EMBEDDING for _ in texts]})()
    )


@pytest.fixture()
def corpus_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "corpus_dir", str(tmp_path))
    return tmp_path


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


def _cleanup(db, paths=None, external_ids=None, run_ids=None) -> None:
    conditions = []
    if paths:
        conditions.append(Source.path.in_(paths))
    if external_ids:
        conditions.append(Source.external_id.in_(external_ids))
    if conditions:
        sources = db.query(Source).filter(or_(*conditions)).all()
        for s in sources:
            db.query(Chunk).filter(Chunk.source_id == s.id).delete()
        db.query(Source).filter(or_(*conditions)).delete(synchronize_session=False)
    if run_ids:
        db.query(IngestRun).filter(IngestRun.id.in_(run_ids)).delete(synchronize_session=False)
    db.commit()


class TestSharedChunkingPath:
    """execute_gdrive_ingestion() が classify() 以降、execute_ingestion() と同じ
    チャンキング・埋め込み・DB反映コードパス（_process_one/_insert_chunks）を通ることを確認する。
    二重実装がないことのコードレビュー確認を、実行結果で裏付ける"""

    def test_drive_document_insert_creates_chunks_via_shared_path(self, db):
        external_id = f"drv-{uuid.uuid4()}"
        drive_doc = Document(
            path="Notes/hello.md",
            title="Hello",
            content="# Hello\n\nsome drive content to chunk and embed",
            updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            source_type="google_drive",
            external_id=external_id,
            source_url="https://drive.google.com/file/d/abc/view",
        )
        drive_result = DriveLoadResult(documents=[drive_doc], skipped=[], found_external_ids={external_id})
        run_ids: list = []
        try:
            with (
                patch("private_rag_apps.ingestion.indexer.load_drive", return_value=drive_result),
                patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage,
            ):
                _mock_embed(mock_voyage)
                run = run_gdrive_ingestion(db, folder_id="root-folder", trigger="cli")
                run_ids.append(run.id)

            assert run.status == "success"
            assert run.source_type == "google_drive"
            source = db.query(Source).filter(Source.external_id == external_id).first()
            assert source is not None
            assert source.source_type == "google_drive"
            assert source.source_url == "https://drive.google.com/file/d/abc/view"
            chunks = db.query(Chunk).filter(Chunk.source_id == source.id).all()
            assert len(chunks) > 0
            assert run.stats["added"] == 1
        finally:
            _cleanup(db, external_ids=[external_id], run_ids=run_ids)


class TestSourceTypeScopedMatching:
    """ソース照合が source_type に応じて分岐し、同一pathのローカル/Driveソースが
    誤って同一視されないことを確認する（完了条件2）"""

    def test_same_path_local_and_drive_are_distinct_sources(self, db, corpus_dir):
        shared_path = f"{uuid.uuid4()}.md"
        _write_corpus(corpus_dir, {shared_path: "# Local\n\nlocal-only content"})
        external_id = f"drv-{uuid.uuid4()}"
        run_ids: list = []
        try:
            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)

            # Driveのドキュメントに、意図的にローカルと全く同じpath文字列を使う
            drive_doc = Document(
                path=shared_path,
                title="Drive Doc",
                content="drive-only content, unrelated to the local file",
                updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
                source_type="google_drive",
                external_id=external_id,
                source_url="https://drive.google.com/file/d/dup/view",
            )
            drive_result = DriveLoadResult(documents=[drive_doc], skipped=[], found_external_ids={external_id})
            with (
                patch("private_rag_apps.ingestion.indexer.load_drive", return_value=drive_result),
                patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2,
            ):
                _mock_embed(mock_voyage2)
                run_ids.append(run_gdrive_ingestion(db, folder_id="root-folder", trigger="cli").id)

            local_source = (
                db.query(Source).filter(Source.path == shared_path, Source.source_type == "local_fs").first()
            )
            drive_source = (
                db.query(Source)
                .filter(Source.external_id == external_id, Source.source_type == "google_drive")
                .first()
            )

            assert local_source is not None
            assert drive_source is not None
            assert local_source.id != drive_source.id
            assert drive_source.path == shared_path  # 表示用pathが重複していても別レコードのまま
        finally:
            _cleanup(db, paths=[shared_path], external_ids=[external_id], run_ids=run_ids)

    def test_second_gdrive_run_updates_existing_drive_source_by_external_id_not_path(self, db):
        external_id = f"drv-{uuid.uuid4()}"

        def _doc(content: str, path: str = "Notes/renamed-along-the-way.md") -> Document:
            return Document(
                path=path,
                title="Doc",
                content=content,
                updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
                source_type="google_drive",
                external_id=external_id,
                source_url="https://drive.google.com/file/d/x/view",
            )

        run_ids: list = []
        try:
            first_result = DriveLoadResult(
                documents=[_doc("# V1\n\noriginal drive content")], skipped=[], found_external_ids={external_id}
            )
            with (
                patch("private_rag_apps.ingestion.indexer.load_drive", return_value=first_result),
                patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage,
            ):
                _mock_embed(mock_voyage)
                run_ids.append(run_gdrive_ingestion(db, folder_id="root-folder", trigger="cli").id)

            source_after_first = db.query(Source).filter(Source.external_id == external_id).first()
            assert source_after_first is not None
            source_id = source_after_first.id

            # pathが変わっても(Driveでの移動/リネームを模擬)、external_idが同じなら同一Sourceとして更新される
            second_result = DriveLoadResult(
                documents=[_doc("# V2\n\ncompletely different drive content now", path="Notes/new-name.md")],
                skipped=[],
                found_external_ids={external_id},
            )
            with (
                patch("private_rag_apps.ingestion.indexer.load_drive", return_value=second_result),
                patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2,
            ):
                _mock_embed(mock_voyage2)
                run_ids.append(run_gdrive_ingestion(db, folder_id="root-folder", trigger="cli").id)

            all_sources = db.query(Source).filter(Source.external_id == external_id).all()
            assert len(all_sources) == 1
            assert all_sources[0].id == source_id
            assert all_sources[0].path == "Notes/new-name.md"
        finally:
            _cleanup(db, external_ids=[external_id], run_ids=run_ids)


class TestDeletionPhaseSourceTypeIsolation:
    """削除安全弁・削除検知が source_type ごとに独立して判定されることを確認する（完了条件3）。

    m9_google_drive_ingestion.md §8 が名指しする最重要リスク: 走査結果とDB生存ソースの突き合わせが
    ローカル/Driveで混同されると、一方の取り込み実行がもう一方のソースを誤って論理削除する。
    ここでの2テストは、indexer.py の _apply_deletion_phase/_apply_deletions に
    `Source.source_type == source_type` フィルタを付け忘れた場合に確実に失敗する
    （RED/GREENの詳細はタスクレポート参照）。
    """

    def test_local_only_run_does_not_soft_delete_drive_sources(self, db, corpus_dir):
        keep_path = f"{uuid.uuid4()}.md"
        gone_path = f"{uuid.uuid4()}.md"
        drive_external_id = f"drv-{uuid.uuid4()}"
        run_ids: list = []
        try:
            _write_corpus(corpus_dir, {keep_path: "# Keep\n\nkeep me", gone_path: "# Gone\n\nremove me"})
            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)

            # 生存しているDriveソースを1件seedする。ローカルの走査結果には一切登場しない
            # 疑似path("Drive/keep-drive.md")を持つため、pathベースの見つかった集合には
            # 決して現れない
            drive_source = Source(
                path="Drive/keep-drive.md",
                title="Drive Keep",
                content_hash="deadbeef",
                source_type="google_drive",
                external_id=drive_external_id,
                source_url="https://drive.google.com/file/d/keep/view",
            )
            db.add(drive_source)
            db.commit()

            # ローカルコーパスから gone_path を消し、ローカルのみの取り込みをforce_deleteで実行する
            (corpus_dir / gone_path).unlink()
            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2:
                _mock_embed(mock_voyage2)
                run_ids.append(run_ingestion(db, trigger="cli", force_delete=True).id)

            gone_source = (
                db.query(Source).filter(Source.path == gone_path, Source.source_type == "local_fs").first()
            )
            db.refresh(drive_source)

            assert gone_source is not None
            assert gone_source.deleted_at is not None  # ローカルの削除は正しく反映される
            # source_typeスコープなしの旧ロジックでは、drive_source.path が
            # found_paths（ローカルのpathのみ）に含まれないため、ここで誤って削除されていた
            assert drive_source.deleted_at is None
        finally:
            _cleanup(db, paths=[keep_path, gone_path], external_ids=[drive_external_id], run_ids=run_ids)

    def test_drive_only_run_does_not_soft_delete_local_sources(self, db, corpus_dir):
        local_keep_path = f"{uuid.uuid4()}.md"
        drive_keep_id = f"drv-keep-{uuid.uuid4()}"
        drive_gone_id = f"drv-gone-{uuid.uuid4()}"
        run_ids: list = []
        try:
            _write_corpus(corpus_dir, {local_keep_path: "# Local Keep\n\nkeep me"})
            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)
            local_source = db.query(Source).filter(Source.path == local_keep_path).first()
            assert local_source is not None

            drive_gone_source = Source(
                path="Drive/gone.md",
                title="Drive Gone",
                content_hash="deadbeef",
                source_type="google_drive",
                external_id=drive_gone_id,
            )
            db.add(drive_gone_source)
            db.commit()

            # Driveの走査結果は drive_keep_id のみを見つけた、という状況を模擬する
            # （drive_gone_id は見つからなかった = 消えた）
            drive_result = DriveLoadResult(documents=[], skipped=[], found_external_ids={drive_keep_id})
            with (
                patch("private_rag_apps.ingestion.indexer.load_drive", return_value=drive_result),
                patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2,
            ):
                _mock_embed(mock_voyage2)
                run_ids.append(
                    run_gdrive_ingestion(db, folder_id="root-folder", trigger="cli", force_delete=True).id
                )

            db.refresh(drive_gone_source)
            db.refresh(local_source)

            assert drive_gone_source.deleted_at is not None  # Driveの削除は正しく反映される
            # source_typeスコープなしの実装では、local_source.path が found_keys
            # （Driveのexternal_id集合）に含まれないため、ここで誤って削除されうる
            assert local_source.deleted_at is None
        finally:
            _cleanup(
                db,
                paths=[local_keep_path],
                external_ids=[drive_gone_id, drive_keep_id],
                run_ids=run_ids,
            )

    def test_large_local_corpus_does_not_block_legitimate_small_drive_deletion(self, db, corpus_dir):
        """安全弁（削除ガード）の alive_count が source_type ごとに独立して計算されることを
        確認する。ローカル側に大量の生存ソースがあっても、それが Drive 側の alive_count に
        混入して正当な少数件の Drive 削除まで誤ってブロックしてしまわないこと。

        Drive生存2件中1件が消えた(50%)状況を作る。ガード比率の既定値
        (INGEST_DELETE_GUARD_RATIO=0.5)に対し found/alive = 1/2 = 0.5 はちょうど許可境界
        （`< ratio` でブロックなので 0.5 は許可）。ローカル20件がalive_countへ混入すれば
        found/alive = 1/22 ≈ 0.045 << 0.5 となり、force指定なしで誤ってブロックされるはず"""
        local_paths = [f"{uuid.uuid4()}.md" for _ in range(20)]
        _write_corpus(corpus_dir, {p: f"# Doc\n\ncontent {p}" for p in local_paths})
        drive_keep_id = f"drv-keep-{uuid.uuid4()}"
        drive_gone_id = f"drv-gone-{uuid.uuid4()}"
        run_ids: list = []
        try:
            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)  # ローカルソース20件を生存させる

            for external_id in (drive_keep_id, drive_gone_id):
                db.add(
                    Source(
                        path=f"Drive/{external_id}.md",
                        title="Drive Doc",
                        content_hash="deadbeef",
                        source_type="google_drive",
                        external_id=external_id,
                    )
                )
            db.commit()

            drive_result = DriveLoadResult(documents=[], skipped=[], found_external_ids={drive_keep_id})
            with (
                patch("private_rag_apps.ingestion.indexer.load_drive", return_value=drive_result),
                patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2,
            ):
                _mock_embed(mock_voyage2)
                run = run_gdrive_ingestion(db, folder_id="root-folder", trigger="cli", force_delete=False)
                run_ids.append(run.id)

            gone_source = db.query(Source).filter(Source.external_id == drive_gone_id).first()
            assert run.status == "success"
            assert run.error is None  # ガードは発動しない（scoped ratioはちょうど許可境界）
            assert gone_source.deleted_at is not None  # 削除が正しく反映される
        finally:
            _cleanup(
                db, paths=local_paths, external_ids=[drive_keep_id, drive_gone_id], run_ids=run_ids
            )


class TestSkippedItemsInStats:
    """gdrive_loaderのskipped（ショートカット/非対応mimeType）がrun.statsへ畳み込まれることを確認する
    （完了条件5関連。m9_tasklist T4作業項目5）"""

    def test_skipped_items_are_folded_into_run_stats(self, db):
        skipped = [
            SkippedItem(name="link.gshortcut", path="link.gshortcut", reason="ショートカットは非対応です"),
            SkippedItem(name="image.png", path="sub/image.png", reason="非対応のmimeTypeです"),
        ]
        drive_result = DriveLoadResult(documents=[], skipped=skipped, found_external_ids=set())
        run_ids: list = []
        try:
            with patch("private_rag_apps.ingestion.indexer.load_drive", return_value=drive_result):
                run = run_gdrive_ingestion(db, folder_id="root-folder", trigger="cli")
                run_ids.append(run.id)

            assert run.source_type == "google_drive"
            skipped_items = run.stats["skipped_items"]
            assert len(skipped_items) == 2
            assert skipped_items[0]["name"] == "link.gshortcut"
            assert skipped_items[1]["reason"] == "非対応のmimeTypeです"
            # skipped_items追加は既存のskippedキー（classify()のSKIP件数）の意味を変えない
            assert run.stats["skipped"] == 0
        finally:
            _cleanup(db, run_ids=run_ids)


class TestCliEndToEnd:
    """`make ingest-gdrive` が呼び出す cli.main.ingest_gdrive() から、モック化した
    GoogleDriveClient（gdrive_loader.GoogleDriveClient境界）に対して一気通貫
    （探索→取り込み→DB反映）で動作することを確認する（完了条件4）。
    実際のCLIプロセス起動ではなく、CLIが呼ぶのと全く同じ関数を直接呼ぶことで検証する
    （認証情報なしで実行できるようにするため）"""

    def test_ingest_gdrive_cli_entrypoint_runs_end_to_end_with_mocked_drive_client(self, db, monkeypatch):
        external_id = f"drv-{uuid.uuid4()}"
        run_ids: list = []

        client = MagicMock()
        root_file = _drive_file(
            external_id,
            "cli-e2e.md",
            "text/markdown",
            modified_time=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        )

        def list_children(folder_id: str):
            if folder_id == "cli-e2e-root":
                return [root_file]
            return []

        client.list_children.side_effect = list_children
        client.download_content.return_value = b"# CLI End to End\n\nhello from a mocked drive client"

        monkeypatch.setattr(settings, "drive_folder_id", "cli-e2e-root")

        try:
            with (
                patch(
                    "private_rag_apps.ingestion.gdrive_loader.GoogleDriveClient", return_value=client
                ),
                patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage,
            ):
                _mock_embed(mock_voyage)
                from private_rag_apps.cli.main import ingest_gdrive

                ingest_gdrive(trigger="cli", force_delete=True, folder_id="cli-e2e-root")

            source = db.query(Source).filter(Source.external_id == external_id).first()
            assert source is not None
            assert source.source_type == "google_drive"
            chunks = db.query(Chunk).filter(Chunk.source_id == source.id).all()
            assert len(chunks) > 0

            latest_run = db.query(IngestRun).order_by(IngestRun.started_at.desc()).first()
            assert latest_run is not None
            assert latest_run.source_type == "google_drive"
            assert latest_run.status == "success"
            run_ids.append(latest_run.id)
        finally:
            _cleanup(db, external_ids=[external_id], run_ids=run_ids)
