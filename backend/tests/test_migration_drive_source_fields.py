"""M9 T1: 0004マイグレーション(sources/ingest_runsへのsource_type等追加)の検証。

TestMigrationCycle は、実DB(rag_test)を一切汚さないよう使い捨てのPostgres DBを都度
作成してalembic upgrade/downgradeを実行する(同一インスタンス内でも、同一DB内のスキーマを
search_pathで切り替える方式は、既存のpublicスキーマがrag_test上で既にhead(0004)まで
適用済みだと、alembic_version/sourcesの検索がsearch_pathのフォールバックでpublicに
漏れてしまい正しく隔離できないため採用していない。DB自体を分ければpublicスキーマの
存在チェックがDB単位で独立するため安全に隔離できる)。
TestPartialUniqueIndexes は、rag_testのpublicスキーマ(head=0004まで適用済み前提)に
対してORMモデル経由で検証する。
"""
import os
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.models.rag import IngestRun, Source

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _database_url(dbname: str) -> str:
    """settings.database_url(make testではrag_test)のホスト/認証情報はそのままに、
    データベース名だけを差し替えたURLを返す。"""
    parts = urlsplit(settings.database_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


def _run_alembic(args: list[str], dbname: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = _database_url(dbname)
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
    )


def _run_alembic_ok(args: list[str], dbname: str) -> subprocess.CompletedProcess[str]:
    result = _run_alembic(args, dbname)
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    return result


def _current_revision(dbname: str) -> str:
    return _run_alembic_ok(["current"], dbname).stdout.strip()


@pytest.fixture()
def isolated_database():
    """0004のupgrade/downgradeサイクルを検証するための使い捨てPostgres DB。
    rag_test(他テストが依存する本番相当の状態)には一切触れない。"""
    dbname = f"m9_t1_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    try:
        yield dbname
    finally:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        admin_engine.dispose()


class TestMigrationCycle:
    def test_upgrade_head_marks_existing_local_sources_as_local_fs(self, isolated_database):
        _run_alembic_ok(["upgrade", "0003"], isolated_database)

        engine = create_engine(_database_url(isolated_database))
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO sources (path, title, content_hash) "
                        "VALUES (:path, 'legacy title', 'hash-legacy')"
                    ),
                    {"path": "legacy/doc.md"},
                )

            _run_alembic_ok(["upgrade", "head"], isolated_database)
            assert _current_revision(isolated_database).startswith("0004")

            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT source_type, external_id, source_url FROM sources WHERE path = :p"
                    ),
                    {"p": "legacy/doc.md"},
                ).one()
            assert row.source_type == "local_fs"
            assert row.external_id is None
            assert row.source_url is None
        finally:
            engine.dispose()

    def test_downgrade_minus_one_restores_local_path_uniqueness(self, isolated_database):
        _run_alembic_ok(["upgrade", "head"], isolated_database)
        assert _current_revision(isolated_database).startswith("0004")

        _run_alembic_ok(["downgrade", "-1"], isolated_database)
        assert _current_revision(isolated_database).startswith("0003")

        engine = create_engine(_database_url(isolated_database))
        try:
            with engine.connect() as conn:
                cols = {
                    r[0]
                    for r in conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'sources'"
                        )
                    )
                }
                run_cols = {
                    r[0]
                    for r in conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'ingest_runs'"
                        )
                    )
                }
            assert "source_type" not in cols
            assert "external_id" not in cols
            assert "source_url" not in cols
            assert "source_type" not in run_cols

            with engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO sources (path, title, content_hash) VALUES ('dup.md', 't', 'h1')")
                )
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO sources (path, title, content_hash) VALUES ('dup.md', 't', 'h2')"
                        )
                    )
        finally:
            engine.dispose()

    def test_downgrade_minus_one_fails_atomically_when_drive_rows_share_path(self, isolated_database):
        """既知のダウングレード制約: 同一pathを共有するDrive由来の行が存在する場合、
        UNIQUE(path)の復元がunique violationで失敗する。トランザクショナルDDLにより
        ダウングレード自体はロールバックされ、スキーマは0004のまま安全に保たれることを確認する。"""
        _run_alembic_ok(["upgrade", "head"], isolated_database)

        engine = create_engine(_database_url(isolated_database))
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO sources (path, content_hash, source_type, external_id) "
                        "VALUES ('shared/dup.md', 'h1', 'google_drive', 'gdrive-a')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO sources (path, content_hash, source_type, external_id) "
                        "VALUES ('shared/dup.md', 'h2', 'google_drive', 'gdrive-b')"
                    )
                )

            result = _run_alembic(["downgrade", "-1"], isolated_database)
            assert result.returncode != 0

            # ロールバックにより0004のまま、partial unique indexも残っていること
            assert _current_revision(isolated_database).startswith("0004")
            with engine.connect() as conn:
                idx = conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE indexname = 'sources_path_unique_local'"
                    )
                ).first()
            assert idx is not None
        finally:
            engine.dispose()


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


class TestPartialUniqueIndexes:
    """rag_test(head=0004適用済み前提)に対してORM経由でsources/ingest_runsを検証する。"""

    def test_google_drive_sources_can_share_path(self, db):
        shared_path = f"shared/{uuid.uuid4()}.md"
        ids: list = []
        try:
            a = Source(
                path=shared_path,
                content_hash="hash-a",
                source_type="google_drive",
                external_id=f"gdrive-{uuid.uuid4()}",
                source_url="https://drive.google.com/a",
            )
            b = Source(
                path=shared_path,
                content_hash="hash-b",
                source_type="google_drive",
                external_id=f"gdrive-{uuid.uuid4()}",
                source_url="https://drive.google.com/b",
            )
            db.add_all([a, b])
            db.commit()
            ids = [a.id, b.id]

            assert db.query(Source).filter(Source.id.in_(ids)).count() == 2
        finally:
            db.query(Source).filter(Source.id.in_(ids)).delete(synchronize_session=False)
            db.commit()

    def test_local_fs_sources_still_enforce_path_uniqueness(self, db):
        path = f"local/{uuid.uuid4()}.md"
        first = Source(path=path, content_hash="hash-1")
        db.add(first)
        db.commit()
        try:
            dup = Source(path=path, content_hash="hash-2")
            db.add(dup)
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
        finally:
            db.query(Source).filter(Source.path == path).delete(synchronize_session=False)
            db.commit()

    def test_invalid_source_type_rejected_by_check_constraint(self, db):
        path = f"invalid/{uuid.uuid4()}.md"
        try:
            with pytest.raises(IntegrityError):
                db.execute(
                    text(
                        "INSERT INTO sources (path, content_hash, source_type) "
                        "VALUES (:p, 'h', 'dropbox')"
                    ),
                    {"p": path},
                )
                db.commit()
        finally:
            db.rollback()

    def test_ingest_run_defaults_source_type_to_local_fs(self, db):
        run = IngestRun(trigger="cli", status="success")
        db.add(run)
        db.commit()
        try:
            db.refresh(run)
            assert run.source_type == "local_fs"
        finally:
            db.query(IngestRun).filter(IngestRun.id == run.id).delete(synchronize_session=False)
            db.commit()
