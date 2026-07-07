import voyageai
from sqlalchemy.orm import Session
from sqlalchemy import select
from langfuse.decorators import observe
from private_rag_apps.models.rag import Source, Chunk
from private_rag_apps.core.config import settings

@observe(name="retrieve_context")
def retrieve_context(db: Session, query: str, top_k: int = 5):
    # embed_query span
    emb = _embed_query(query)

    # Vector search: ORDER BY c.embedding <=> :emb LIMIT top_k
    stmt = (
        select(Chunk, Source)
        .join(Source, Chunk.source_id == Source.id)
        .where(Source.deleted_at.is_(None))
        .order_by(Chunk.embedding.cosine_distance(emb))
        .limit(top_k)
    )

    results = db.execute(stmt).all()
    
    context_chunks = []
    for chunk, source in results:
        context_chunks.append({
            "chunk_id": str(chunk.id),
            "content": chunk.content,
            "metadata": chunk.metadata_,
            "title": source.title,
            "path": source.path,
        })

    return context_chunks

@observe(name="embed_query")
def _embed_query(query: str):
    voyage_client = voyageai.Client(api_key=settings.voyage_api_key)
    return voyage_client.embed([query], model="voyage-4-lite", input_type="query").embeddings[0]
