import math
from typing import List, Dict, Any, Set
from .schema import RelevantDoc

def evaluate_retrieval(
    retrieved_chunks: List[Dict[str, Any]],
    expected_docs: List[RelevantDoc]
) -> Dict[str, float]:
    """
    Calculate retrieval metrics (Recall@5, Recall@10, nDCG@10, MRR) based on chunk lists.
    Applies doc-deduplication: only the highest-ranked chunk for a given relevant document contributes.
    """
    if not expected_docs:
        # If there are no expected docs (e.g. negative case), retrieval metrics aren't applicable.
        return {"recall_5": 1.0, "recall_10": 1.0, "ndcg_10": 1.0, "mrr": 1.0}

    expected_paths = {doc.path for doc in expected_docs}
    expected_grades = {doc.path: doc.grade for doc in expected_docs}
    
    # doc-dedup: track seen paths to zero out subsequent chunk scores for the same doc
    seen_paths: Set[str] = set()
    rels = []
    
    for chunk in retrieved_chunks:
        path = chunk.get("path")
        if path in expected_paths and path not in seen_paths:
            rels.append(expected_grades[path])
            seen_paths.add(path)
        else:
            rels.append(0)
            
    # Calculate IDCG based on ideal ordering of expected_docs
    ideal_rels = sorted([doc.grade for doc in expected_docs], reverse=True)
    idcg_10 = sum(r / math.log2(i + 2) for i, r in enumerate(ideal_rels[:10]))
            
    # Calculate metrics
    def calc_recall(rels_list, top_n):
        return 1.0 if any(r > 0 for r in rels_list[:top_n]) else 0.0

    def calc_ndcg(rels_list, top_n, idcg):
        if idcg == 0:
            return 0.0
        dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rels_list[:top_n]))
        return dcg / idcg

    def calc_mrr(rels_list):
        for i, r in enumerate(rels_list):
            if r > 0:
                return 1.0 / (i + 1)
        return 0.0

    return {
        "recall_5": calc_recall(rels, 5),
        "recall_10": calc_recall(rels, 10),
        "ndcg_10": calc_ndcg(rels, 10, idcg_10),
        "mrr": calc_mrr(rels)
    }
