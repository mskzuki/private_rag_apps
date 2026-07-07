"""M1 Eval — 3-mode comparative evaluation.

Runs the golden dataset against ``vector``, ``hybrid``, and ``hybrid_rerank``
strategies and outputs Recall@5, Recall@10, nDCG@10, and MRR as a comparison
table.  Results are also saved to ``evals/results/m1_<timestamp>.json``.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import yaml

from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.retrieval.searcher import retrieve_context


# ---------------------------------------------------------------------------
# Metric helpers  (binary relevance: 0 or 1)
# ---------------------------------------------------------------------------

def calc_ndcg(rels: List[int], k: int = 10) -> float:
    """Compute nDCG@k assuming binary relevance."""
    rels_k = rels[:k]
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rels_k))
    n_relevant = sum(1 for r in rels_k if r > 0)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(k, n_relevant)))
    return dcg / idcg if idcg > 0 else 0.0


def calc_mrr(rels: List[int]) -> float:
    """Compute MRR (Mean Reciprocal Rank)."""
    for i, r in enumerate(rels):
        if r > 0:
            return 1.0 / (i + 1)
    return 0.0


def calc_recall(rels: List[int], k: int) -> float:
    """Compute Recall@k (binary: 1 if any relevant doc in top-k, else 0)."""
    return 1.0 if any(r > 0 for r in rels[:k]) else 0.0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_eval() -> None:
    yaml_path = Path("evals/golden/m0.yaml")
    if not yaml_path.exists():
        print(f"Golden dataset not found at {yaml_path}")
        return

    with open(yaml_path, "r", encoding="utf-8") as f:
        dataset = yaml.safe_load(f)

    db = SessionLocal()
    try:
        total = len(dataset)
        modes = ["vector", "hybrid", "hybrid_rerank"]
        results_by_mode = {
            mode: {"recall_5": 0.0, "recall_10": 0.0, "ndcg_10": 0.0, "mrr": 0.0}
            for mode in modes
        }

        # Temporarily increase rerank_top_k so we can measure Recall@10
        saved_rerank_top_k = settings.rerank_top_k
        settings.rerank_top_k = 10

        print(f"Running Eval on {total} questions...\n")

        for item in dataset:
            item["id"]
            question = item["question"]
            expected_sources = item["expected_sources"]

            for mode in modes:
                context_chunks = retrieve_context(db, query=question, strategy=mode)

                rels = [
                    1 if chunk["path"] in expected_sources else 0
                    for chunk in context_chunks
                ]

                results_by_mode[mode]["recall_5"] += calc_recall(rels, 5)
                results_by_mode[mode]["recall_10"] += calc_recall(rels, 10)
                results_by_mode[mode]["ndcg_10"] += calc_ndcg(rels, 10)
                results_by_mode[mode]["mrr"] += calc_mrr(rels)

        # Restore
        settings.rerank_top_k = saved_rerank_top_k

        # Average metrics
        for mode in modes:
            for key in results_by_mode[mode]:
                results_by_mode[mode][key] /= total

        # Print comparative table
        print("| mode | Recall@5 | Recall@10 | nDCG@10 | MRR |")
        print("|---|---|---|---|---|")
        for mode in modes:
            r = results_by_mode[mode]
            print(
                f"| {mode} | {r['recall_5']:.3f} | {r['recall_10']:.3f} "
                f"| {r['ndcg_10']:.3f} | {r['mrr']:.3f} |"
            )

        # Save to file
        out_dir = Path("evals/results")
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"m1_{timestamp}.json"

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results_by_mode, f, indent=2)

        print(f"\nSaved results to {out_path}")

    finally:
        db.close()


if __name__ == "__main__":
    run_eval()
