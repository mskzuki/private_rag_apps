import argparse
import json
import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, cast

from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.retrieval.searcher import retrieve_context
from private_rag_apps.generation.generator import generate_answer_stream, condense
from private_rag_apps.evals.schema import load_dataset, validate_paths
from private_rag_apps.evals.metrics import evaluate_retrieval
from private_rag_apps.evals.retrieval_cache import (
    file_sha256,
    load_retrieval_cache,
    read_cached_rankings,
    write_retrieval_cache,
)
from private_rag_apps.evals.judge import (
    evaluate_faithfulness,
    evaluate_answer_relevance,
    evaluate_supplement_format,
)

_last_voyage_call_at: Optional[float] = None


def _pace_voyage_call() -> None:
    """retrieve_context()内のVoyage呼び出し(embed+rerank)前に呼ぶことで、アイテム間の
    呼び出し間隔が settings.ingest_embed_min_interval_sec 未満にならないよう待機する。
    ingestion/indexer.py::_pace_embed_call / evals/routing.py::_pace_voyage_call と同じ実装
    (アプリコード[retrieval/]は変更しない方針のため、ここに複製している)。
    既知の制約(docs/adr/0003_m7_t3_eval_baseline_gap.md): retrieve_context内部でembedと
    rerankが連続して呼ばれる箇所は本関数ではペーシングできない(アイテム間のみ)。
    """
    global _last_voyage_call_at
    now = time.monotonic()
    if _last_voyage_call_at is not None:
        wait = settings.ingest_embed_min_interval_sec - (now - _last_voyage_call_at)
        if wait > 0:
            time.sleep(wait)
    _last_voyage_call_at = time.monotonic()


def get_corpus_hash() -> str:
    seed_path = Path(settings.corpus_dir)
    # コーパスの状態を表すため、全ファイル名とmtimeの単純なハッシュを取るだけ
    if not seed_path.exists():
        return "missing"
    hasher = hashlib.sha256()
    for p in sorted(seed_path.rglob("*")):
        if p.is_file():
            hasher.update(str(p.relative_to(seed_path)).encode())
            hasher.update(str(p.stat().st_mtime).encode())
    return hasher.hexdigest()


def get_answer(query: str, context: List[Dict[str, Any]]) -> str:
    # generator.py がtemp/max_tokensの上書きに対応していればここで上書きできるが、現状は1024固定・デフォルトtempがハードコードされている。
    # generator.py がeval用の設定を使うべきか、M3が言う「fixed max_tokens」の通り現状のまま使うべきかは未確定。
    # generator.py を変更しない限りtempを簡単に上書きできないので、ひとまずこのまま実行する（M3スペックではEVAL_GEN_TEMPERATURE=0とされている点に注意）。
    # 可能であればkwargsとして渡したいが、現状generatorはkwargsを受け付けない。
    # M3に厳密に従うにはgenerator.pyにパッチを当てるか、現状の実装をそのまま使うしかない。
    # 今のところはストリームされたトークンを収集するだけにする。
    stream = generate_answer_stream(query, context)
    full_text = ""
    for event in stream:
        if event.get("event") == "token":
            full_text += event.get("data", "")
    return full_text


def _retrieval_cache_provenance(dataset_path: Path, corpus_hash: str) -> Dict[str, Any]:
    """検索結果を安全に再生するための有効性判定情報を構築する。"""
    return {
        "dataset_sha256": file_sha256(dataset_path),
        "corpus_hash": corpus_hash,
        "retrieval_params": {
            "eval_top_k": settings.eval_top_k,
            "eval_ef_search": settings.eval_ef_search,
            "candidate_k": settings.candidate_k,
            "rrf_k": settings.rrf_k,
            "fuse_k": settings.fuse_k,
            "rerank_top_k": settings.rerank_top_k,
        },
        "models": {"embed": settings.embed_model, "rerank": "rerank-2.5"},
    }


def run_eval(*, no_cache: bool = False) -> None:
    dataset_path = Path(settings.eval_dataset_path)
    if not dataset_path.exists():
        print(f"Dataset not found at {dataset_path}")
        sys.exit(1)

    dataset = load_dataset(dataset_path)
    validate_paths(dataset, settings.corpus_dir)
    cache_path = Path(settings.eval_retrieval_cache_path)
    cache_provenance = _retrieval_cache_provenance(dataset_path, get_corpus_hash())
    cached_entries: Dict[str, Any] = {}
    refreshed_entries: Dict[str, Dict[str, Any]] = {}
    if no_cache:
        print(f"Refreshing retrieval cache at {cache_path} (Voyage API will be called).")
    else:
        cached_entries = load_retrieval_cache(cache_path, cache_provenance)
        print(f"Replaying retrieval cache at {cache_path} (Voyage API will not be called).")

    db = SessionLocal()
    try:
        total = len(dataset)
        print(f"Running M3 Eval on {total} questions...\n")

        results: List[Dict[str, Any]] = []

        # 集計用(値がfloat/int/list等で不均一なため、Dict[str, Any]として明示的に型付けする)
        agg: Dict[str, Any] = {
            "fused": {"recall_5": 0.0, "recall_10": 0.0, "ndcg_10": 0.0, "mrr": 0.0},
            "reranked": {"recall_5": 0.0, "recall_10": 0.0, "ndcg_10": 0.0, "mrr": 0.0},
            "generation": {"faithfulness": 0.0, "answer_relevance": 0.0, "count": 0},
            # M7 T4: 複合質問(tags=["compound", ...])の「一般知識に基づく補足」書式検証
            # (スペック rev.3 §7.2 補足書式の遵守、§7.3 make eval に組み込み)
            "supplement_format": {"total": 0, "passed": 0, "failed_ids": []},
        }

        for item in dataset:
            print(f"Evaluating {item.id}...")

            # キャッシュ再生時は、保存済みの検索結果をそのまま使う。
            # これによりマルチターンの rewrite も再実行せず、検索入力の再現性を保つ。
            query_to_search = item.question
            if item.turns and no_cache:
                history_messages = []
                for i, text in enumerate(item.turns):
                    role = "user" if i % 2 == 0 else "assistant"
                    history_messages.append({"role": role, "content": text})

                # M7 T5: condense() は (search_query, rewrite_applied) を返す。
                # make eval は rewrite_applied を使わない（既存e2e evalの範疇。ADR 0004）
                query_to_search, _ = condense(item.question, history_messages)
                print(f"  Condensed query: {query_to_search}")

            # 1. 検索。既定ではキャッシュを再生し、--no-cache 時だけ Voyage を呼ぶ。
            if no_cache:
                _pace_voyage_call()
                ret_result = cast(
                    Dict[str, List[Dict[str, Any]]],
                    retrieve_context(
                        db, query=query_to_search, strategy="hybrid_rerank", diagnostic_mode=True
                    ),
                )
                refreshed_entries[item.id] = ret_result
            else:
                ret_result = read_cached_rankings(cached_entries, item.id)
            fused_chunks = ret_result["fused_ranking"]
            reranked_chunks = ret_result["reranked_ranking"]

            fused_metrics = evaluate_retrieval(fused_chunks, item.relevant)
            reranked_metrics = evaluate_retrieval(reranked_chunks, item.relevant)

            # 2. 生成 & Judge
            faithfulness = 0.0
            answer_relevance = 0.0
            answer = ""

            # 回答を生成
            answer = get_answer(item.question, reranked_chunks[: settings.rerank_top_k])

            # Judge
            f_res = evaluate_faithfulness(
                item.question, answer, reranked_chunks[: settings.rerank_top_k]
            )
            faithfulness = f_res.get("score", 0)

            ar_res = evaluate_answer_relevance(item.question, answer, item.reference_answer)
            answer_relevance = ar_res.get("score", 0)

            # ネガティブケース: expect_no_answerがtrueの場合、検索指標は1.0になる（evaluate_retrieval内で処理）
            # Faithfulness/Relevanceは「回答を控えたか」に基づく。

            # M7 T4: 複合質問(一部corpus・一部一般論)は、生成された回答が「一般知識に基づく補足」
            # 書式(区切り線+分離セクション+引用マーカー不使用)を守れているかをLLM-as-judgeで検証する
            # (スペック rev.3 §7.1/§7.2)。judgeの判定は自動集計するのみで、真の合否は
            # 人手確認込みで別途判断する（brief作業項目7）。
            supplement_format_res = None
            if "compound" in item.tags:
                supplement_format_res = evaluate_supplement_format(item.question, answer)
                agg["supplement_format"]["total"] += 1
                if supplement_format_res.get("score", 0) == 1:
                    agg["supplement_format"]["passed"] += 1
                else:
                    agg["supplement_format"]["failed_ids"].append(item.id)

            # 積算
            for k in agg["fused"]:
                agg["fused"][k] += fused_metrics[k]
                agg["reranked"][k] += reranked_metrics[k]

            agg["generation"]["faithfulness"] += faithfulness
            agg["generation"]["answer_relevance"] += answer_relevance
            agg["generation"]["count"] += 1

            result_entry: Dict[str, Any] = {
                "id": item.id,
                "metrics": {
                    "fused": fused_metrics,
                    "reranked": reranked_metrics,
                    "generation": {
                        "faithfulness": faithfulness,
                        "answer_relevance": answer_relevance,
                    },
                },
                "answer": answer,
            }
            if supplement_format_res is not None:
                result_entry["metrics"]["supplement_format"] = supplement_format_res
            results.append(result_entry)

        # 平均値
        for k in agg["fused"]:
            agg["fused"][k] /= total
            agg["reranked"][k] /= total

        agg["generation"]["faithfulness"] /= total
        agg["generation"]["answer_relevance"] /= total

        # 実行時の来歴情報
        provenance: Dict[str, Any] = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "dataset_version": "m3_golden",
            "corpus_hash": get_corpus_hash(),
            "retrieval_params": {
                "eval_top_k": settings.eval_top_k,
                "eval_ef_search": settings.eval_ef_search,
                "candidate_k": settings.candidate_k,
                "rrf_k": settings.rrf_k,
                "fuse_k": settings.fuse_k,
                "rerank_top_k": settings.rerank_top_k,
            },
            "models": {
                "llm": settings.llm_model,
                "embed": settings.embed_model,
                "embed_dims": 1024,  # models/rag.py の Vector(1024) と一致。settings に次元フィールドが無いためハードコード
                "rerank": "rerank-2.5",  # retrieval/searcher.py のハードコード値と一致。settings に無いため同様
                "judge": settings.judge_model,
            },
        }

        report = {"provenance": provenance, "aggregate": agg, "results": results}

        if no_cache:
            write_retrieval_cache(cache_path, cache_provenance, refreshed_entries)
            print(f"Updated retrieval cache at {cache_path}")

        # baselineとの比較
        baseline_path = Path("evals/baselines/current.json")
        fail = False
        warnings = []

        if baseline_path.exists():
            with open(baseline_path, "r") as f:
                baseline = json.load(f)

            # 単純な許容誤差ロジック（検索はハードゲート、生成はソフトゲート）
            for k in agg["reranked"]:
                diff = baseline["aggregate"]["reranked"][k] - agg["reranked"][k]
                if diff > 0.05:  # 許容誤差 0.05
                    print(
                        f"FAIL: Retrieval metric {k} dropped by {diff:.3f} (baseline: {baseline['aggregate']['reranked'][k]:.3f})"
                    )
                    fail = True

            for k in ["faithfulness", "answer_relevance"]:
                diff = baseline["aggregate"]["generation"][k] - agg["generation"][k]
                if diff > 0.1:  # 許容誤差 0.1
                    print(f"WARN: Generation metric {k} dropped by {diff:.3f}")
                    warnings.append(f"{k} dropped by {diff:.3f}")

        else:
            print("No baseline found. Saving current run as baseline.")
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            with open(baseline_path, "w") as f:
                json.dump(report, f, indent=2)

        # M7 T4: 複合質問の補足書式チェック(ソフトゲート。judgeの自動判定のみでFAILにはしない。
        # 真の合否判定は人手確認込みで行う。スペック rev.3 §7.2)
        sf = agg["supplement_format"]
        if sf["total"] > 0:
            print(f"\nSupplement format check: {sf['passed']}/{sf['total']} passed")
            if sf["failed_ids"]:
                print(f"  judge violation ids (要人手確認): {sf['failed_ids']}")
                warnings.append(
                    f"supplement_format: {sf['passed']}/{sf['total']} passed "
                    f"(judge violations: {sf['failed_ids']}, 要人手確認)"
                )

        # Markdownレポートを出力
        md_report = f"""# M3 Eval Report

## Provenance
- **Date**: {provenance["timestamp"]}
- **Dataset Version**: {provenance["dataset_version"]}
- **Corpus Hash**: {provenance["corpus_hash"]}
- **Models**: LLM={provenance["models"]["llm"]}, Embed={provenance["models"]["embed"]} ({provenance["models"]["embed_dims"]}d), Rerank={provenance["models"]["rerank"]}, Judge={provenance["models"]["judge"]}
- **Retrieval**: EVAL_TOP_K={provenance["retrieval_params"]["eval_top_k"]}, EVAL_EF_SEARCH={provenance["retrieval_params"]["eval_ef_search"]}

## Aggregate Metrics

| Metric | RRF Fused | Reranked |
|---|---|---|
| Recall@5 | {agg["fused"]["recall_5"]:.3f} | {agg["reranked"]["recall_5"]:.3f} |
| Recall@10 | {agg["fused"]["recall_10"]:.3f} | {agg["reranked"]["recall_10"]:.3f} |
| nDCG@10 | {agg["fused"]["ndcg_10"]:.3f} | {agg["reranked"]["ndcg_10"]:.3f} |
| MRR | {agg["fused"]["mrr"]:.3f} | {agg["reranked"]["mrr"]:.3f} |

### Generation Metrics
- **Faithfulness**: {agg["generation"]["faithfulness"]:.3f}
- **Answer Relevance**: {agg["generation"]["answer_relevance"]:.3f}

### Supplement Format Check (M7, compound questions)
- **{sf["passed"]}/{sf["total"]} passed** (judge判定。真の合否は人手確認込み。violation ids: {sf["failed_ids"]})

## Result
"""
        if fail:
            md_report += "**FAILED**: Hard gate triggered on retrieval metrics.\n"
        elif warnings:
            md_report += "**PASSED (with warnings)**: Generation metrics degraded.\n"
        else:
            md_report += "**PASSED**: All metrics within tolerance.\n"

        out_dir = Path("evals/reports")
        out_dir.mkdir(parents=True, exist_ok=True)

        summary_path = out_dir / "latest_summary.md"
        summary_path.write_text(md_report)
        print(f"Wrote {summary_path}")

        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"m3_{timestamp}.json"

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        print(f"Saved JSON report to {out_path}")

        # [HOOK] Langfuse Datasets/Experiments連携
        # Langfuseのdataset機能が準備でき次第、コメントを外して実装する
        # _upload_to_langfuse(report, dataset)

        if fail:
            sys.exit(1)

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the M3 evaluation harness.")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Call Voyage and refresh the retrieval cache instead of replaying it.",
    )
    args = parser.parse_args()
    run_eval(no_cache=args.no_cache)
