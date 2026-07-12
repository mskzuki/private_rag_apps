import datetime
import uuid

import pytest

from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.models.rag import Chunk, IngestRun, Source
from private_rag_apps.ingestion.concurrency import IngestAlreadyRunningError
from private_rag_apps.ingestion.indexer import run_ingestion


FAKE_EMBEDDING = [0.1] * 1024


def _write_corpus(tmp_path, files: dict[str, str]):
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


def _mock_embed(mock_voyage, dim_per_call=None):
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


def _cleanup(db, paths, run_ids=None):
    sources = db.query(Source).filter(Source.path.in_(paths)).all()
    for s in sources:
        db.query(Chunk).filter(Chunk.source_id == s.id).delete()
    db.query(Source).filter(Source.path.in_(paths)).delete(synchronize_session=False)
    if run_ids:
        db.query(IngestRun).filter(IngestRun.id.in_(run_ids)).delete(synchronize_session=False)
    db.commit()


class TestInsertSkipReplace:
    def test_insert_new_source_creates_chunks(self, corpus_dir, db):
        path = f"{uuid.uuid4()}.md"
        _write_corpus(corpus_dir, {path: "# Title\n\nsome content here"})
        run_ids = []
        try:
            from unittest.mock import patch

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)

            source = db.query(Source).filter(Source.path == path).first()
            assert source is not None
            chunks = db.query(Chunk).filter(Chunk.source_id == source.id).all()
            assert len(chunks) > 0
        finally:
            _cleanup(db, [path], run_ids)

    def test_skip_unchanged_source_does_not_reembed(self, corpus_dir, db):
        path = f"{uuid.uuid4()}.md"
        _write_corpus(corpus_dir, {path: "# Title\n\nunchanged content"})
        run_ids = []
        try:
            from unittest.mock import patch

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2:
                _mock_embed(mock_voyage2)
                run_ids.append(run_ingestion(db, trigger="cli").id)
                mock_voyage2.Client.return_value.embed.assert_not_called()

            latest_run = (
                db.query(IngestRun).order_by(IngestRun.started_at.desc()).first()
            )
            assert latest_run.stats["skipped"] == 1
        finally:
            _cleanup(db, [path], run_ids)

    def test_replace_changed_source_swaps_chunks_atomically(self, corpus_dir, db):
        path = f"{uuid.uuid4()}.md"
        _write_corpus(corpus_dir, {path: "# Title\n\noriginal content"})
        run_ids = []
        try:
            from unittest.mock import patch

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)

            source = db.query(Source).filter(Source.path == path).first()
            old_chunk_ids = {c.id for c in db.query(Chunk).filter(Chunk.source_id == source.id).all()}

            _write_corpus(corpus_dir, {path: "# Title\n\ncompletely different content now"})
            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2:
                _mock_embed(mock_voyage2)
                run_ids.append(run_ingestion(db, trigger="cli").id)

            new_chunks = db.query(Chunk).filter(Chunk.source_id == source.id).all()
            new_chunk_ids = {c.id for c in new_chunks}
            assert old_chunk_ids.isdisjoint(new_chunk_ids)
            assert any("completely different" in c.content for c in new_chunks)
        finally:
            _cleanup(db, [path], run_ids)

    def test_replace_failure_leaves_old_chunks_intact(self, corpus_dir, db):
        path = f"{uuid.uuid4()}.md"
        _write_corpus(corpus_dir, {path: "# Title\n\noriginal content"})
        run_ids = []
        try:
            from unittest.mock import patch

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)

            source = db.query(Source).filter(Source.path == path).first()
            old_chunks_before = db.query(Chunk).filter(Chunk.source_id == source.id).all()
            assert len(old_chunks_before) > 0

            _write_corpus(corpus_dir, {path: "# Title\n\nchanged content that fails to embed"})
            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2:
                mock_voyage2.Client.return_value.embed.side_effect = RuntimeError("embed API down")
                run_ids.append(run_ingestion(db, trigger="cli").id)

            old_chunks_after = db.query(Chunk).filter(Chunk.source_id == source.id).all()
            assert {c.id for c in old_chunks_after} == {c.id for c in old_chunks_before}

            latest_run = db.query(IngestRun).order_by(IngestRun.started_at.desc()).first()
            assert path in latest_run.stats["failed_files"]
        finally:
            _cleanup(db, [path], run_ids)


class TestReviveSoftDeleted:
    def test_revive_unchanged_does_not_reembed(self, corpus_dir, db):
        path = f"{uuid.uuid4()}.md"
        _write_corpus(corpus_dir, {path: "# Title\n\nstable content"})
        run_ids = []
        try:
            from unittest.mock import patch

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)

            source = db.query(Source).filter(Source.path == path).first()
            source.deleted_at = datetime.datetime.now(datetime.timezone.utc)
            db.commit()

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2:
                _mock_embed(mock_voyage2)
                run_ids.append(run_ingestion(db, trigger="cli").id)
                mock_voyage2.Client.return_value.embed.assert_not_called()

            db.refresh(source)
            assert source.deleted_at is None
        finally:
            _cleanup(db, [path], run_ids)

    def test_revive_changed_replaces_chunks(self, corpus_dir, db):
        path = f"{uuid.uuid4()}.md"
        _write_corpus(corpus_dir, {path: "# Title\n\noriginal content"})
        run_ids = []
        try:
            from unittest.mock import patch

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)

            source = db.query(Source).filter(Source.path == path).first()
            source.deleted_at = datetime.datetime.now(datetime.timezone.utc)
            db.commit()

            _write_corpus(corpus_dir, {path: "# Title\n\nrevived with new content"})
            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2:
                _mock_embed(mock_voyage2)
                run_ids.append(run_ingestion(db, trigger="cli").id)
                mock_voyage2.Client.return_value.embed.assert_called()

            db.refresh(source)
            assert source.deleted_at is None
            chunks = db.query(Chunk).filter(Chunk.source_id == source.id).all()
            assert any("revived with new content" in c.content for c in chunks)
        finally:
            _cleanup(db, [path], run_ids)


class TestDeletionAndGuard:
    def test_missing_source_gets_soft_deleted(self, corpus_dir, db):
        keep_path = f"{uuid.uuid4()}.md"
        gone_path = f"{uuid.uuid4()}.md"
        _write_corpus(corpus_dir, {keep_path: "# Keep\n\nkeep me", gone_path: "# Gone\n\nremove me"})
        run_ids = []
        try:
            from unittest.mock import patch

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)

            (corpus_dir / gone_path).unlink()

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2:
                _mock_embed(mock_voyage2)
                run_ids.append(run_ingestion(db, trigger="cli", force_delete=True).id)

            gone_source = db.query(Source).filter(Source.path == gone_path).first()
            assert gone_source.deleted_at is not None
        finally:
            _cleanup(db, [keep_path, gone_path], run_ids)

    def test_guard_blocks_mass_deletion_without_force(self, corpus_dir, db):
        paths = [f"{uuid.uuid4()}.md" for _ in range(4)]
        _write_corpus(corpus_dir, {p: f"# Doc\n\ncontent {p}" for p in paths})
        run_ids = []
        try:
            from unittest.mock import patch

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                _mock_embed(mock_voyage)
                run_ids.append(run_ingestion(db, trigger="cli").id)

            for p in paths:
                (corpus_dir / p).unlink()

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage2:
                _mock_embed(mock_voyage2)
                run = run_ingestion(db, trigger="cli", force_delete=False)
                run_ids.append(run.id)

            assert run.status == "success"
            assert "delete phase aborted" in run.error
            for p in paths:
                source = db.query(Source).filter(Source.path == p).first()
                assert source.deleted_at is None
        finally:
            _cleanup(db, paths, run_ids)


class TestConcurrencyIntegration:
    def test_run_ingestion_raises_when_already_running(self, corpus_dir, db):
        running = IngestRun(trigger="cli", status="running")
        db.add(running)
        db.commit()
        try:
            with pytest.raises(IngestAlreadyRunningError):
                run_ingestion(db, trigger="cli")
        finally:
            db.query(IngestRun).filter(IngestRun.id == running.id).delete()
            db.commit()
