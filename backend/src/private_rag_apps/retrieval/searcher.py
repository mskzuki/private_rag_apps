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
    diagnostic_mode: bool = False,
) -> List[Dict[str, Any]] | Dict[str, List[Dict[str, Any]]]:
    """与えられたクエリに対して関連するコンテキスト（チャンク）を取得する。
    設定された戦略（vector, hybrid, hybrid_rerank）に応じて検索処理を振り分ける。
    diagnostic_mode が True の場合、リランク前後のリストを含む辞書を返す。
    """
    strategy = strategy or settings.retrieval_strategy

    if diagnostic_mode:
        db.execute(text(f"SET LOCAL hnsw.ef_search = {settings.eval_ef_search}"))

    emb = _embed_query(query)

    if strategy == "vector":
        chunks = _vector_search(db, emb, settings.rerank_top_k)
        if diagnostic_mode:
            return {"fused_ranking": chunks, "reranked_ranking": chunks}
    elif strategy == "hybrid":
        chunks = _hybrid_search(
            db, query, emb, settings.candidate_k, settings.rrf_k, settings.rerank_top_k,
        )
        if diagnostic_mode:
            return {"fused_ranking": chunks, "reranked_ranking": chunks}
    elif strategy == "hybrid_rerank":
        fused_chunks = _hybrid_search(
            db, query, emb, settings.candidate_k, settings.rrf_k, settings.fuse_k,
        )
        # 評価用トップKが指定されていればそれを使用、そうでなければ rerank_top_k
        top_k = settings.eval_top_k if diagnostic_mode else settings.rerank_top_k
        chunks = _rerank(query, fused_chunks, top_k)
        if diagnostic_mode:
            return {"fused_ranking": fused_chunks, "reranked_ranking": chunks}
    else:
        raise ValueError(f"Unknown retrieval_strategy: {strategy}")

    return chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

@observe(name="embed_query")
@observe(name="embed_query")
def _embed_query(query: str) -> List[float]:
    """検索クエリを埋め込みモデル(Voyage)を用いてベクトル化する"""
    voyage_client = voyageai.Client(api_key=settings.voyage_api_key)
    from typing import cast
    return cast(List[float], voyage_client.embed(
        [query], model="voyage-4-lite", input_type="query",
    ).embeddings[0])


@observe(name="vector_search")
def _vector_search(
    db: Session, emb: List[float], top_k: int,
) -> List[Dict[str, Any]]:
    """コサイン類似度を用いた純粋なベクトル検索（pgvector）を実行し、上位結果を返す"""
    stmt = (
        select(Chunk, Source)
        .join(Source, Chunk.source_id == Source.id)
        .where(Source.deleted_at.is_(None))
        .order_by(Chunk.embedding.cosine_distance(emb))
        .limit(top_k)
    )
    results = db.execute(stmt).all()
    return _format_chunks([(row[0], row[1]) for row in results])


@observe(name="hybrid_search")
def _hybrid_search(
    db: Session,
    query: str,
    emb: List[float],
    candidate_k: int,
    rrf_k: int,
    top_k: int,
) -> List[Dict[str, Any]]:
    """ベクトル検索と全文検索（pg_bigm）を並行して実行し、Reciprocal Rank Fusion (RRF) を用いてスコアを融合して上位結果を返す"""
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
            "cand_k": candidate_k,
            "rrf_k": rrf_k,
            "fuse_k": top_k,
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
    """Voyage AIのリランクモデルを用いて、検索で得られたチャンク群をクエリとの関連度順に再スコアリング（リランク）する"""
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
            get_client().update_current_generation(
                usage_details={
                    "input": rerank_result.total_tokens,
                    "output": 0
                },
                model="rerank-2.5"
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
