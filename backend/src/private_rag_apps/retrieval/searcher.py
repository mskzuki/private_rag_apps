import voyageai
from typing import List, Dict, Any, Optional, Sequence, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select, text
from langfuse import observe, get_client
from private_rag_apps.models.rag import Source, Chunk
from private_rag_apps.core.config import settings


@observe(name="retrieve_context")
def retrieve_context(
    db: Session,
    query: str,
    *,
    strategy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve context chunks for the given query.

    Args:
        db: Database session.
        query: User question.
        strategy: Override retrieval strategy (default: ``settings.retrieval_strategy``).
            One of ``"vector"``, ``"hybrid"``, ``"hybrid_rerank"``.
    """
    strategy = strategy or settings.retrieval_strategy

    emb = _embed_query(query)

    if strategy == "vector":
        chunks = _vector_search(db, emb, settings.rerank_top_k)
    elif strategy == "hybrid":
        chunks = _hybrid_search(
            db, query, emb, settings.candidate_k, settings.rrf_k, settings.rerank_top_k,
        )
    elif strategy == "hybrid_rerank":
        fused_chunks = _hybrid_search(
            db, query, emb, settings.candidate_k, settings.rrf_k, settings.fuse_k,
        )
        chunks = _rerank(query, fused_chunks, settings.rerank_top_k)
    else:
        raise ValueError(f"Unknown retrieval_strategy: {strategy}")

    return chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

@observe(name="embed_query")
def _embed_query(query: str) -> List[float]:
    voyage_client = voyageai.Client(api_key=settings.voyage_api_key)
    return voyage_client.embed(
        [query], model="voyage-4-lite", input_type="query",
    ).embeddings[0]


@observe(name="vector_search")
def _vector_search(
    db: Session, emb: List[float], top_k: int,
) -> List[Dict[str, Any]]:
    stmt = (
        select(Chunk, Source)
        .join(Source, Chunk.source_id == Source.id)
        .where(Source.deleted_at.is_(None))
        .order_by(Chunk.embedding.cosine_distance(emb))
        .limit(top_k)
    )
    results = db.execute(stmt).all()
    return _format_chunks(results)


@observe(name="hybrid_search")
def _hybrid_search(
    db: Session,
    query: str,
    emb: List[float],
    cand_k: int,
    rrf_k: int,
    fuse_k: int,
) -> List[Dict[str, Any]]:
    """Execute hybrid search via RRF fusion CTE.

    Note: ``vector_search`` and ``fts_search`` are executed inside a single
    CTE statement so they cannot be split into separate Langfuse spans.
    The ``rrf_fuse`` span wraps the entire SQL execution and records the
    number of fused candidates as metadata.
    """
    sql = text("""
    WITH vector_search AS (
        SELECT c.id,
               ROW_NUMBER() OVER (ORDER BY c.embedding <=> :q_embedding::vector) AS rank
        FROM chunks c
        JOIN sources s ON s.id = c.source_id AND s.deleted_at IS NULL
        ORDER BY c.embedding <=> :q_embedding::vector
        LIMIT :cand_k
    ),
    fts_search AS (
        SELECT c.id,
               ROW_NUMBER() OVER (ORDER BY bigm_similarity(c.content, :q_text) DESC) AS rank
        FROM chunks c
        JOIN sources s ON s.id = c.source_id AND s.deleted_at IS NULL
        WHERE c.content =% :q_text
        LIMIT :cand_k
    ),
    fused AS (
        SELECT id, 1.0/(:rrf_k + rank) AS score FROM vector_search
        UNION ALL
        SELECT id, 1.0/(:rrf_k + rank) AS score FROM fts_search
    )
    SELECT id, SUM(score) AS rrf_score
    FROM fused GROUP BY id
    ORDER BY rrf_score DESC
    LIMIT :fuse_k;
    """)

    emb_str = "[" + ",".join(f"{v:.8e}" for v in emb) + "]"

    client = get_client()
    with client.start_as_current_observation(name="rrf_fuse"):
        result_ids = db.execute(sql, {
            "q_embedding": emb_str,
            "q_text": query,
            "cand_k": cand_k,
            "rrf_k": rrf_k,
            "fuse_k": fuse_k,
        }).scalars().all()

        client.update_current_span(
            metadata={"fused_candidates": len(result_ids)},
        )

    if not result_ids:
        return []

    # Fetch full chunk + source rows, preserving RRF order
    stmt = (
        select(Chunk, Source)
        .join(Source, Chunk.source_id == Source.id)
        .where(Chunk.id.in_(result_ids))
    )
    rows = db.execute(stmt).all()
    row_map = {str(c.id): (c, s) for c, s in rows}

    ordered_results = []
    for chunk_id in result_ids:
        cid_str = str(chunk_id)
        if cid_str in row_map:
            ordered_results.append(row_map[cid_str])

    return _format_chunks(ordered_results)


@observe(name="rerank")
def _rerank(
    query: str,
    chunks: List[Dict[str, Any]],
    top_k: int,
) -> List[Dict[str, Any]]:
    """Rerank chunks using Voyage rerank-2.5.

    Falls back to RRF-ordered ``chunks[:top_k]`` if the API call fails.
    """
    if not chunks:
        return []

    documents = [c["content"] for c in chunks]
    try:
        voyage_client = voyageai.Client(api_key=settings.voyage_api_key)
        rerank_result = voyage_client.rerank(
            query=query,
            documents=documents,
            model="rerank-2.5",
            top_k=top_k,
        )

        # Record usage if available
        try:
            client = get_client()
            client.update_current_span(
                usage={"total": rerank_result.total_tokens},
                model="rerank-2.5",
            )
        except (AttributeError, Exception):
            pass  # SDK version may not expose total_tokens or client may be disabled

        reranked_chunks = [chunks[r.index] for r in rerank_result.results]
        return reranked_chunks

    except Exception as e:
        client = get_client()
        client.update_current_span(
            level="WARNING",
            status_message=f"Rerank failed, falling back to RRF order: {e}",
        )
        return chunks[:top_k]


def _format_chunks(
    results: Sequence[Tuple[Chunk, Source]],
) -> List[Dict[str, Any]]:
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
