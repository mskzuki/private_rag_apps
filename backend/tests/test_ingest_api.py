import threading
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from private_rag_apps.api.main import app
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.models.rag import Chunk, Conversation, IngestRun, Source

client = TestClient(app)


def _cleanup(paths=None, run_ids=None):
    db = SessionLocal()
    try:
        if paths:
            sources = db.query(Source).filter(Source.path.in_(paths)).all()
            for s in sources:
                db.query(Chunk).filter(Chunk.source_id == s.id).delete()
            db.query(Source).filter(Source.path.in_(paths)).delete(synchronize_session=False)
        if run_ids:
            db.query(IngestRun).filter(IngestRun.id.in_(run_ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


class TestListSources:
    def test_list_sources_returns_chunk_counts_and_metadata(self):
        db = SessionLocal()
        path = f"{uuid.uuid4()}.md"
        source = Source(path=path, title="Test Doc", content_hash="abc")
        db.add(source)
        db.commit()
        db.refresh(source)
        db.add(Chunk(source_id=source.id, position=0, content="chunk one", embedding=[0.1] * 1024))
        db.add(Chunk(source_id=source.id, position=1, content="chunk two", embedding=[0.1] * 1024))
        db.commit()
        db.close()

        try:
            response = client.get("/api/sources")
            assert response.status_code == 200
            body = response.json()
            entry = next(s for s in body if s["path"] == path)
            assert entry["title"] == "Test Doc"
            assert entry["chunk_count"] == 2
            assert entry["deleted_at"] is None
        finally:
            _cleanup(paths=[path])

    def test_list_sources_excludes_deleted_by_default_and_include_deleted_toggle(self):
        db = SessionLocal()
        path = f"{uuid.uuid4()}.md"
        source = Source(
            path=path,
            title="Deleted Doc",
            content_hash="abc",
            deleted_at=time_now(),
        )
        db.add(source)
        db.commit()
        db.close()

        try:
            response = client.get("/api/sources")
            assert response.status_code == 200
            paths = [s["path"] for s in response.json()]
            assert path not in paths

            response2 = client.get("/api/sources?include_deleted=true")
            assert response2.status_code == 200
            paths2 = [s["path"] for s in response2.json()]
            assert path in paths2
        finally:
            _cleanup(paths=[path])


def time_now():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc)


class TestPostIngest:
    def test_post_ingest_returns_run_id_with_api_trigger(self):
        with patch("private_rag_apps.api.main._run_ingest_in_background") as mock_bg:
            response = client.post("/api/ingest")
        body = response.json()
        try:
            assert response.status_code == 200
            assert "id" in body
            db = SessionLocal()
            run = db.query(IngestRun).filter(IngestRun.id == body["id"]).first()
            db.close()
            assert run is not None
            assert run.trigger == "api"
            mock_bg.assert_called_once()
        finally:
            run_id = body.get("id")
            if run_id:
                _cleanup(run_ids=[run_id])

    def test_post_ingest_rejects_with_409_when_already_running(self):
        db = SessionLocal()
        running = IngestRun(trigger="cli", status="running")
        db.add(running)
        db.commit()
        db.refresh(running)
        run_id = running.id
        db.close()

        try:
            response = client.post("/api/ingest")
            assert response.status_code == 409
        finally:
            _cleanup(run_ids=[run_id])

    def test_second_post_ingest_rejected_while_first_still_processing(self):
        release_event = threading.Event()
        started_event = threading.Event()

        def slow_background(run_id: str, force_delete: bool) -> None:
            started_event.set()
            release_event.wait(timeout=5)

        results = {}

        def call_first():
            with patch("private_rag_apps.api.main._run_ingest_in_background", side_effect=slow_background):
                results["first"] = client.post("/api/ingest")

        t = threading.Thread(target=call_first)
        t.start()
        assert started_event.wait(timeout=5)

        try:
            second_response = client.post("/api/ingest")
            assert second_response.status_code == 409
        finally:
            release_event.set()
            t.join(timeout=5)
            first_run_id = results["first"].json()["id"]
            _cleanup(run_ids=[first_run_id])


class TestListIngestRuns:
    def test_list_ingest_runs_returns_history_ordered_by_started_at_desc(self):
        db = SessionLocal()
        r1 = IngestRun(trigger="cli", status="success", stats={"added": 1})
        db.add(r1)
        db.commit()
        db.refresh(r1)
        r2 = IngestRun(trigger="demo", status="success", stats={"added": 2})
        db.add(r2)
        db.commit()
        db.refresh(r2)
        r1_id, r2_id = r1.id, r2.id
        db.close()

        try:
            response = client.get("/api/ingest/runs")
            assert response.status_code == 200
            body = response.json()
            ids = [r["id"] for r in body]
            assert str(r2_id) in ids
            assert str(r1_id) in ids
            assert ids.index(str(r2_id)) < ids.index(str(r1_id))
        finally:
            _cleanup(run_ids=[r1_id, r2_id])


class TestDeleteIndex:
    def test_delete_index_removes_sources_and_chunks_but_keeps_conversations(self):
        db = SessionLocal()
        path = f"{uuid.uuid4()}.md"
        source = Source(path=path, title="Doc", content_hash="abc")
        db.add(source)
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(source)
        db.refresh(conv)
        db.add(Chunk(source_id=source.id, position=0, content="c", embedding=[0.1] * 1024))
        db.commit()
        conv_id = conv.id
        source_id = source.id
        db.close()

        try:
            response = client.delete("/api/index")
            assert response.status_code == 200

            db2 = SessionLocal()
            assert db2.query(Source).filter(Source.path == path).first() is None
            assert db2.query(Chunk).filter(Chunk.source_id == source_id).count() == 0
            assert db2.query(Conversation).filter(Conversation.id == conv_id).first() is not None
            db2.close()
        finally:
            db3 = SessionLocal()
            db3.query(Conversation).filter(Conversation.id == conv_id).delete()
            db3.commit()
            db3.close()

    def test_delete_index_rejected_while_ingestion_running(self):
        db = SessionLocal()
        running = IngestRun(trigger="cli", status="running")
        db.add(running)
        db.commit()
        db.refresh(running)
        run_id = running.id
        db.close()

        try:
            response = client.delete("/api/index")
            assert response.status_code == 409
        finally:
            _cleanup(run_ids=[run_id])
