import voyageai
from sqlalchemy.orm import Session
from langfuse.decorators import observe
from private_rag_apps.models.rag import Source, Chunk, IngestRun
from private_rag_apps.core.config import settings
from .loader import load_directory
from .chunker import chunk_markdown
import datetime

@observe(name="ingest_run")
def run_ingestion(db: Session, trigger: str = "cli"):
    run = IngestRun(trigger=trigger, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        docs = load_directory(settings.corpus_dir)
        
        added = 0
        updated = 0
        skipped = 0
        failed_files = []

        # Simple M0 logic: load everything, upsert by path
        for doc in docs:
            try:
                # Upsert source
                source = db.query(Source).filter(Source.path == doc.path).first()
                if source:
                    # M0 requirement: replace chunks unconditionally
                    db.query(Chunk).filter(Chunk.source_id == source.id).delete()
                    source.title = doc.title
                    source.content_hash = doc.content_hash
                    source.source_updated_at = doc.updated_at
                    source.deleted_at = None
                    source.updated_at = datetime.datetime.now(datetime.timezone.utc)
                    updated += 1
                else:
                    source = Source(
                        path=doc.path,
                        title=doc.title,
                        content_hash=doc.content_hash,
                        source_updated_at=doc.updated_at
                    )
                    db.add(source)
                    db.flush() # to get source.id
                    added += 1

                # Chunking
                chunks = chunk_markdown(doc.content)
                if not chunks:
                    continue

                texts = [c.content for c in chunks]
                embeddings = _embed_documents(texts)

                for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                    db_chunk = Chunk(
                        source_id=source.id,
                        position=i,
                        content=chunk.content,
                        embedding=emb,
                        metadata_=chunk.metadata
                    )
                    db.add(db_chunk)
                
            except Exception as e:
                print(f"Error processing {doc.path}: {e}")
                failed_files.append(doc.path)
                db.rollback()
                continue
            
            db.commit()

        run.status = "success"
        run.stats = {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "failed_files": failed_files
        }
        run.finished_at = datetime.datetime.now(datetime.timezone.utc)
        db.commit()

    except Exception as e:
        run.status = "error"
        run.error = str(e)
        run.finished_at = datetime.datetime.now(datetime.timezone.utc)
        db.commit()
        raise e

@observe(name="embed_documents")
def _embed_documents(texts: list[str]) -> list[list[float]]:
    voyage_client = voyageai.Client(api_key=settings.voyage_api_key)
    return voyage_client.embed(texts, model="voyage-4-lite", input_type="document").embeddings
