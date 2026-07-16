"""M7 routing eval: rewrite → retrieve → grade を評価する（`make eval-routing`）。

docs/specs/m7_adaptive_routing.md（rev.3）§7.1/§7.2/§7.3、
.superpowers/sdd/task-T4-brief.md 作業項目5 に基づく。

generate（LLM回答生成）は実行しない（高速。スペック §7.3）。retrieve（Voyage embed +
rerank）と、履歴付き(followup)クエリの rewrite（既存 generation.condense() 呼び出し）
のみを行う。

## 「rewrite」段階について（重要な設計判断）

rewrite ノード自体（`graph/nodes/rewrite.py`。既存 `generation.condense()` を呼ぶだけの
薄いラッパー）は T5 で実装済み。本スクリプトはグラフ全体（`build_graph()`）を経由せず、
retrieve/grade 同様 `condense()` を直接呼ぶ（ペーシング制御のため、他のVoyage呼び出しと
同じ方式で内部関数を直接呼ぶ設計。モジュールdocstring「Voyageレート制限対策」参照）。
rewriteノードは `condense()` を呼ぶだけで新規ロジックを持たないため、両者は等価。

`history` が非空の項目（followupカテゴリ）については実際に `condense()` を呼び出す。
history が空の項目（corpus/general/ambiguous）は search_query = query のまま
（この場合 condense() を呼んでも query がそのまま返るだけで実質的に同じだが、
無駄なLLM呼び出しを避けるため呼ばない）。

condense() は T5 で temperature=0 を明示指定するようになった（スペック §3.5 eval再現性）。
そのため followup項目の rewrite 結果は決定的になる（キャッシュの意味論とも整合する）。
condense() 失敗時は自動的に元のqueryにフォールバックする（condense()自身の既存
フォールバック）ため、LLMが一切使えない環境でも本スクリプトはpass-through相当の
動作で完走できる。

`--cached-rewrite` はこの rewrite 結果(search_query, rewrite_applied)を jsonl に
キャッシュし、再実行時（閾値変更後の再集計など）に condense() の再呼び出しを避ける
オプション。

## rewrite quality（付随指標。ハードゲートではない）

routing.jsonl には正解の関連ドキュメント一覧（relevant docs）が無く、
m3_golden.jsonl のような厳密な hit@k は計算できない（既知の制約。
backend/evals/dataset/routing-README.md §1 参照）。そのため、rewrite quality は
「followup項目について、rewrite有り/無しでの経路予測(predicted_route)が
expected_routeと一致する件数」を代理指標として使う。厳密なhit@kではない点に注意。

## rewrite レイテンシ（T5完了条件: p95の記録）

condense() には `@observe(as_type="generation")` が付与されており、Langfuse有効時は
自動的にレイテンシがtraceに記録される（スペック §6）。本スクリプトはLangfuseダッシュボード
に依存せず結果を報告できるよう、`resolve_search_query()` が実際にLLM呼び出しを行った
場合（キャッシュヒット・history空を除く）のwall-clock時間を `rewrite_latency_ms` として
各レコードに記録し、`build_report()` で p50/p95 を集計する。

## Voyageレート制限対策

`ingestion/indexer.py::_pace_embed_call` / `backend/evals/analyze_score_distribution.py`
と同じ方式（Voyage呼び出し前に settings.ingest_embed_min_interval_sec 秒以上空ける）を
本スクリプト内に複製する（アプリコードは変更しないため）。

## 再開性

出力（routing_eval_results.jsonl）は1件処理するごとに逐次追記する。既に処理済みのidは
再実行時にスキップする（analyze_score_distribution.pyと同じ設計）。

## 実行方法

    cd backend && uv run python -m private_rag_apps.evals.routing
    cd backend && uv run python -m private_rag_apps.evals.routing --cached-rewrite
    cd backend && uv run python -m private_rag_apps.evals.routing --stats-only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from sqlalchemy.orm import Session

from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.generation.generator import condense
from private_rag_apps.graph.nodes.grade import grade
from private_rag_apps.graph.state import ScoredChunk
from private_rag_apps.retrieval.searcher import _embed_query, _hybrid_search, _rerank

DATASET_PATH = Path("evals/dataset/routing.jsonl")
RESULTS_PATH = Path("evals/dataset/routing_eval_results.jsonl")
REWRITE_CACHE_PATH = Path("evals/dataset/routing_rewrite_cache.jsonl")
REPORT_JSON_PATH = Path("evals/reports/m7-routing-eval-result.json")
REPORT_MD_PATH = Path("evals/reports/m7-routing-eval-result.md")

HOLDOUT_GROUNDED_MISS_MAX = 1
HOLDOUT_DIRECT_WRONG_MAX = 3

_last_voyage_call_at: Optional[float] = None


def _pace_voyage_call() -> None:
    """Voyage呼び出し(embed/rerank共通)の間隔が settings.ingest_embed_min_interval_sec
    未満にならないよう待機する。ingestion/indexer.py::_pace_embed_call /
    evals/analyze_score_distribution.py::_pace_voyage_call と同じ実装を複製している
    （アプリコード[retrieval/]は変更しない方針のため）。"""
    global _last_voyage_call_at
    now = time.monotonic()
    if _last_voyage_call_at is not None:
        wait = settings.ingest_embed_min_interval_sec - (now - _last_voyage_call_at)
        if wait > 0:
            time.sleep(wait)
    _last_voyage_call_at = time.monotonic()


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not path.exists():
        return items
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def load_latest_by_id(path: Path) -> Dict[str, Dict[str, Any]]:
    """idごとに最新のレコードを返す(再実行時の重複行は後勝ち)。"""
    latest: Dict[str, Dict[str, Any]] = {}
    for rec in load_jsonl(path):
        latest[rec["id"]] = rec
    return latest


def append_record(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def resolve_search_query(
    item: Dict[str, Any], rewrite_cache: Dict[str, Dict[str, Any]], use_cache: bool
) -> tuple[str, bool, Optional[float]]:
    """rewrite段階: historyが非空ならcondense()を呼ぶ(グラフのrewriteノードと等価。
    モジュールdocstring参照)。--cached-rewrite指定時はキャッシュがあればそれを使う。
    戻り値: (search_query, rewrite_applied, rewrite_latency_ms)。
    rewrite_latency_ms は実際にLLM呼び出しを行った場合のみ値を持ち、history空・
    キャッシュヒットの場合は None（レイテンシ集計から除外するため。T5完了条件の
    p95計測は「rewriteの追加分」＝実LLM呼び出しの時間のみを対象とする）"""
    history = item.get("history") or []
    if not history:
        return item["query"], False, None

    if use_cache and item["id"] in rewrite_cache:
        cached = rewrite_cache[item["id"]]
        return cached["search_query"], cached["rewrite_applied"], None

    start = time.monotonic()
    search_query, rewrite_applied = condense(item["query"], history)
    rewrite_latency_ms = (time.monotonic() - start) * 1000

    if use_cache:
        append_record(
            REWRITE_CACHE_PATH,
            {"id": item["id"], "search_query": search_query, "rewrite_applied": rewrite_applied},
        )

    return search_query, rewrite_applied, rewrite_latency_ms


def retrieve_and_grade(db: Session, search_query: str) -> Dict[str, Any]:
    """本番のretrieval関数(_embed_query/_hybrid_search/_rerank)とgrade純関数を
    そのまま呼ぶ(retrieve_context()は使わず、Voyage呼び出し間にペーシングを挟むため
    内部関数を直接呼ぶ。T2のanalyze_score_distribution.pyと同じ方式)。"""
    _pace_voyage_call()
    emb = _embed_query(search_query)
    fused = _hybrid_search(
        db, search_query, emb, settings.candidate_k, settings.rrf_k, settings.fuse_k
    )

    retrieved: List[Dict[str, Any]] = []
    if fused:
        _pace_voyage_call()
        retrieved = _rerank(search_query, fused, settings.rerank_top_k)

    # retrieved は既存 retrieve_context 系関数と同じ plain dict のList[Dict[str, Any]]だが、
    # grade()はGraphState(ScoredChunkのList、invariant)を期待するため型上one castが必要
    # (graph/nodes/generate.pyのcast(List[Dict[str, Any]], ...)と対称の変換)
    grade_result = grade({"retrieved": cast(List[ScoredChunk], retrieved)})
    scores = [c.get("rerank_score") for c in retrieved]
    return {
        "route": grade_result["route"],
        "kept_count": len(grade_result["kept"]),
        "num_candidates": len(fused),
        "top_score": scores[0] if scores else None,
        "scores": scores,
    }


def percentile(values: List[float], pct: float) -> float:
    """values の pct(0-100)パーセンタイルを最近傍法で計算する(LLMを使わない純関数)。
    サンプル数が小さい(routing.jsonlのfollowupは最大20件)ため、線形補間ではなく
    単純な順位法を使う。T5完了条件「レイテンシp95の記録」で使用する"""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[idx]


def summarize_followup_direct_expected(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """followupカテゴリでexpected_route=="direct"の項目について、rewrite後もdirect経路の
    ままであることを確認する(LLMを使わない純関数)。T5完了条件「followup の direct 期待
    ケース（3-5件）が rewrite 後も正しく direct になる」の集計用(スペック §7.1)"""
    targets = [
        r for r in records if r.get("category") == "followup" and r["expected_route"] == "direct"
    ]
    wrong_ids = [r["id"] for r in targets if r["predicted_route"] != "direct"]
    return {
        "total": len(targets),
        "correct": len(targets) - len(wrong_ids),
        "wrong_ids": wrong_ids,
    }


def summarize_route_predictions(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """recordsは各itemの{id, expected_route, predicted_route}を含むdictのリスト。
    grounded見逃し・direct誤りの件数とid一覧を返す(スペック §7.2の指標定義)。
    LLMを使わない純関数（テスト対象）。"""
    grounded_total = sum(1 for r in records if r["expected_route"] == "grounded")
    direct_total = sum(1 for r in records if r["expected_route"] == "direct")
    grounded_miss_ids = [
        r["id"]
        for r in records
        if r["expected_route"] == "grounded" and r["predicted_route"] == "direct"
    ]
    direct_wrong_ids = [
        r["id"]
        for r in records
        if r["expected_route"] == "direct" and r["predicted_route"] == "grounded"
    ]
    return {
        "grounded_total": grounded_total,
        "direct_total": direct_total,
        "grounded_miss": len(grounded_miss_ids),
        "grounded_miss_ids": grounded_miss_ids,
        "direct_wrong": len(direct_wrong_ids),
        "direct_wrong_ids": direct_wrong_ids,
    }


def process_dataset(args: argparse.Namespace) -> None:
    dataset = load_jsonl(DATASET_PATH)
    if args.limit:
        dataset = dataset[: args.limit]

    done = load_latest_by_id(RESULTS_PATH)
    rewrite_cache = load_latest_by_id(REWRITE_CACHE_PATH) if args.cached_rewrite else {}

    db = SessionLocal()
    consecutive_errors = 0
    processed = 0
    try:
        for i, item in enumerate(dataset):
            if done.get(item["id"], {}).get("status") == "ok":
                print(f"[{i + 1}/{len(dataset)}] {item['id']}: skip (既に完了)")
                continue

            print(
                f"[{i + 1}/{len(dataset)}] {item['id']} ({item['category']}/{item['split']}): 実行中..."
            )
            try:
                search_query, rewrite_applied, rewrite_latency_ms = resolve_search_query(
                    item, rewrite_cache, args.cached_rewrite
                )
                retrieval = retrieve_and_grade(db, search_query)

                record: Dict[str, Any] = {
                    "id": item["id"],
                    "category": item["category"],
                    "split": item["split"],
                    "expected_route": item["expected_route"],
                    "query": item["query"],
                    "search_query": search_query,
                    "rewrite_applied": rewrite_applied,
                    "predicted_route": retrieval["route"],
                    "kept_count": retrieval["kept_count"],
                    "top_score": retrieval["top_score"],
                    "scores": retrieval["scores"],
                    "status": "ok",
                }
                if rewrite_latency_ms is not None:
                    record["rewrite_latency_ms"] = rewrite_latency_ms
                append_record(RESULTS_PATH, record)

                # rewrite quality比較用: followupでrewriteが実際に発生した項目は、
                # 生クエリ(rewrite無し)でも追加でretrieve+gradeし、"__raw"サフィックス付きidで記録する
                if item["category"] == "followup" and rewrite_applied:
                    raw_id = item["id"] + "__raw"
                    if done.get(raw_id, {}).get("status") != "ok":
                        raw_retrieval = retrieve_and_grade(db, item["query"])
                        raw_record: Dict[str, Any] = {
                            "id": raw_id,
                            "raw_of": item["id"],
                            "category": item["category"],
                            "split": item["split"],
                            "expected_route": item["expected_route"],
                            "query": item["query"],
                            "search_query": item["query"],
                            "rewrite_applied": False,
                            "predicted_route": raw_retrieval["route"],
                            "kept_count": raw_retrieval["kept_count"],
                            "top_score": raw_retrieval["top_score"],
                            "scores": raw_retrieval["scores"],
                            "status": "ok",
                        }
                        append_record(RESULTS_PATH, raw_record)

                consecutive_errors = 0
                processed += 1
                print(f"  -> route={record['predicted_route']} top_score={record['top_score']}")
            except Exception as e:  # noqa: BLE001 - eval実行スクリプト。エラーは記録して続行する
                consecutive_errors += 1
                append_record(
                    RESULTS_PATH,
                    {
                        "id": item["id"],
                        "category": item["category"],
                        "split": item["split"],
                        "expected_route": item["expected_route"],
                        "status": "error",
                        "error": str(e),
                    },
                )
                print(f"  !! error: {e}", file=sys.stderr)
                if consecutive_errors >= args.max_consecutive_errors:
                    print(
                        f"連続{consecutive_errors}件失敗のため中断します。"
                        f"再実行すれば完了分はスキップして再開します。",
                        file=sys.stderr,
                    )
                    sys.exit(1)
    finally:
        db.close()

    print(f"\n完了: 今回 {processed}件処理しました。")


def build_report() -> Dict[str, Any]:
    all_records = list(load_latest_by_id(RESULTS_PATH).values())
    ok_records = [r for r in all_records if r.get("status") == "ok"]
    error_records = [r for r in all_records if r.get("status") != "ok"]

    # __raw companion レコードは grounded_miss/direct_wrong の主指標には含めない
    # (routing.jsonlの本来の項目のみが対象。__rawはrewrite品質比較の補助データ)
    main_records = [r for r in ok_records if "raw_of" not in r]
    raw_records = {r["raw_of"]: r for r in ok_records if "raw_of" in r}

    calibration = [r for r in main_records if r["split"] == "calibration"]
    holdout = [r for r in main_records if r["split"] == "holdout"]

    calibration_summary = summarize_route_predictions(calibration)
    holdout_summary = summarize_route_predictions(holdout)

    # rewrite quality: followupでrewriteが発生した項目について、
    # rewrite有りの route予測がexpected_routeと一致した件数 vs rewrite無し(生クエリ)での一致件数
    rewrite_compared = []
    for r in main_records:
        if r["id"] not in raw_records:
            continue
        raw = raw_records[r["id"]]
        rewrite_compared.append(
            {
                "id": r["id"],
                "expected_route": r["expected_route"],
                "with_rewrite_correct": r["predicted_route"] == r["expected_route"],
                "without_rewrite_correct": raw["predicted_route"] == raw["expected_route"],
                "with_rewrite_top_score": r["top_score"],
                "without_rewrite_top_score": raw["top_score"],
            }
        )
    rewrite_quality = {
        "compared_count": len(rewrite_compared),
        "with_rewrite_correct": sum(1 for c in rewrite_compared if c["with_rewrite_correct"]),
        "without_rewrite_correct": sum(1 for c in rewrite_compared if c["without_rewrite_correct"]),
        "degraded_ids": [
            c["id"]
            for c in rewrite_compared
            if c["without_rewrite_correct"] and not c["with_rewrite_correct"]
        ],
        "details": rewrite_compared,
    }

    # T5完了条件: followupのdirect期待ケースがrewrite後も正しくdirectになること
    # (calibration/holdout両方合算。母数が3-5件と小さいため split別には割らない)
    followup_direct_expected = summarize_followup_direct_expected(main_records)

    # T5完了条件: rewrite(condense呼び出し)のレイテンシ p50/p95 記録
    rewrite_latencies = [
        r["rewrite_latency_ms"] for r in main_records if "rewrite_latency_ms" in r
    ]
    rewrite_latency = {
        "sample_count": len(rewrite_latencies),
        "p50_ms": percentile(rewrite_latencies, 50),
        "p95_ms": percentile(rewrite_latencies, 95),
    }

    verdict_go = (
        holdout_summary["grounded_miss"] <= HOLDOUT_GROUNDED_MISS_MAX
        and holdout_summary["direct_wrong"] <= HOLDOUT_DIRECT_WRONG_MAX
    )

    report = {
        "theta": settings.routing_theta,
        "collected": len(ok_records),
        "errors": len(error_records),
        "error_ids": [r["id"] for r in error_records],
        "calibration": calibration_summary,
        "holdout": holdout_summary,
        "rewrite_quality": rewrite_quality,
        "followup_direct_expected": followup_direct_expected,
        "rewrite_latency": rewrite_latency,
        "verdict": "GO" if verdict_go else "NO-GO",
    }
    return report


def print_and_save_report(report: Dict[str, Any]) -> None:
    print(f"\n=== M7 eval-routing 結果 (THETA={report['theta']}) ===")
    print(f"収集済み: {report['collected']}件 (エラー: {report['errors']}件)")
    if report["error_ids"]:
        print(f"  エラーid: {report['error_ids']}")

    cal = report["calibration"]
    print(
        f"\ncalibration: grounded_miss={cal['grounded_miss']}/{cal['grounded_total']} "
        f"direct_wrong={cal['direct_wrong']}/{cal['direct_total']}"
    )

    hold = report["holdout"]
    print(
        f"holdout:     grounded_miss={hold['grounded_miss']}/{hold['grounded_total']} "
        f"(基準<= {HOLDOUT_GROUNDED_MISS_MAX}) id={hold['grounded_miss_ids']}"
    )
    print(
        f"             direct_wrong={hold['direct_wrong']}/{hold['direct_total']} "
        f"(基準<= {HOLDOUT_DIRECT_WRONG_MAX}) id={hold['direct_wrong_ids']}"
    )

    rq = report["rewrite_quality"]
    print(
        "\nrewrite quality (followup, 代理指標。routing.jsonlにhit@k用のrelevant docsが"
        "無いため経路一致率で代用。backend/evals/dataset/routing-README.md §1参照):"
    )
    print(
        f"  比較対象: {rq['compared_count']}件, "
        f"rewrite有りで正解={rq['with_rewrite_correct']}件, "
        f"rewrite無しで正解={rq['without_rewrite_correct']}件"
    )
    if rq["degraded_ids"]:
        print(f"  rewriteにより悪化したid: {rq['degraded_ids']}")

    fde = report["followup_direct_expected"]
    print(
        f"\nfollowup direct期待ケース: {fde['correct']}/{fde['total']}件が正しくdirectに"
        f"(スコープ外id: {fde['wrong_ids']})"
    )

    lat = report["rewrite_latency"]
    print(
        f"\nrewriteレイテンシ(実LLM呼び出し{lat['sample_count']}件): "
        f"p50={lat['p50_ms']:.0f}ms p95={lat['p95_ms']:.0f}ms "
        f"(目安: p95 <= 800ms。超過時はT7へN削減検討事項として引き継ぐ。スペック §4.3)"
    )

    print(f"\n=== 判定: {report['verdict']} ===")

    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    md = f"""# M7 eval-routing 結果

THETA={report["theta"]}。収集済み{report["collected"]}件、エラー{report["errors"]}件。

## calibration

| 指標 | 実績 |
|---|---|
| grounded見逃し | {cal["grounded_miss"]}/{cal["grounded_total"]} |
| direct誤り | {cal["direct_wrong"]}/{cal["direct_total"]} |

## holdout（合否判定はこちら）

| 指標 | 実績 | 基準 |
|---|---|---|
| grounded見逃し | {hold["grounded_miss"]}/{hold["grounded_total"]} (id={hold["grounded_miss_ids"]}) | <= {HOLDOUT_GROUNDED_MISS_MAX} |
| direct誤り | {hold["direct_wrong"]}/{hold["direct_total"]} (id={hold["direct_wrong_ids"]}) | <= {HOLDOUT_DIRECT_WRONG_MAX} |

## rewrite quality（followup、代理指標）

routing.jsonl には relevant docs が無いため厳密なhit@kではなく、経路予測の正解率で代用している。

比較対象{rq["compared_count"]}件中、rewrite有りで正解{rq["with_rewrite_correct"]}件、
rewrite無しで正解{rq["without_rewrite_correct"]}件。rewriteにより悪化したid: {rq["degraded_ids"]}

## followup direct期待ケース（T5完了条件）

rewrite後もdirect経路になるべきfollowup項目: {fde["correct"]}/{fde["total"]}件が正解
（誤りid: {fde["wrong_ids"]}）

## rewriteレイテンシ（T5完了条件）

実LLM呼び出し{lat["sample_count"]}件（history空・キャッシュヒット分は対象外）:

| 指標 | 実績 | 目安 |
|---|---|---|
| p50 | {lat["p50_ms"]:.0f}ms | - |
| p95 | {lat["p95_ms"]:.0f}ms | <= 800ms（超過時はT7へN削減検討事項として引き継ぐ） |

## 判定

**{report["verdict"]}**
"""
    REPORT_MD_PATH.write_text(md, encoding="utf-8")
    print(f"\n結果を {REPORT_JSON_PATH} / {REPORT_MD_PATH} に保存しました。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="先頭N件のみ処理(動作確認用)")
    parser.add_argument("--max-consecutive-errors", type=int, default=3)
    parser.add_argument(
        "--cached-rewrite",
        action="store_true",
        help="rewrite結果(search_query)をjsonlにキャッシュし、再実行時はcondense()の再呼び出しを避ける",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="APIを叩かず既存 routing_eval_results.jsonl の集計のみ表示",
    )
    args = parser.parse_args()

    if not args.stats_only:
        process_dataset(args)

    report = build_report()
    print_and_save_report(report)

    if report["verdict"] != "GO":
        sys.exit(1)


if __name__ == "__main__":
    main()
