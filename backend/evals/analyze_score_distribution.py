"""M7 T2: routing eval データセット(T1, 130件)を既存 retrieval パイプラインに流し、
rerank score を全件収集・カテゴリ別に可視化するスクリプト。

`generate_dataset.py` / `generate_routing_dataset.py` と同じ位置づけの
importable パッケージ外の一回性スクリプト(`docs/specs/m7_adaptive_routing.md`
rev.3 §7.2, `.superpowers/sdd/task-T2-brief.md` 参照)。

## スコア取得方法(重要。T4実装者への申し送り)

本番の `retrieval/searcher.py::_rerank()` は Voyage のリランク結果でチャンクを
並べ替えるだけで、スコアをチャンクの dict に付与しない(T4 で `rerank_score`
フィールドを追加予定。M7スペック rev.3 §4.3 grade 参照)。したがって本スクリプト
は `_rerank()` を経由せず、以下の方式でスコアを直接取得する:

1. `retrieval.searcher._embed_query(query)` でクエリを埋め込む(本番と同一関数)
2. `retrieval.searcher._hybrid_search(db, query, emb, candidate_k, rrf_k, fuse_k)`
   で候補チャンクを取得する(本番と同一関数・同一設定値。RRF融合済み、rerank前)
3. `voyageai.Client().rerank(query=query, documents=[c["content"] for c in
   candidates], model="rerank-2.5", top_k=settings.rerank_top_k)` を
   本スクリプトから直接呼び出す(`_rerank()` は使わない)
4. 返り値 `RerankingObject.results[i].relevance_score` を各チャンクのスコアと
   して読み取る(`results[i].index` が candidates 配列内のインデックスに対応し、
   `results` は relevance_score 降順)

使用する設定値は `core.config.settings` から本番と同じ値を使う
(`candidate_k`, `rrf_k`, `fuse_k`, `rerank_top_k`, `embed_model`)。これにより
本番の `retrieve_context(strategy="hybrid_rerank")` と同一の candidate 集合・
同一のリランク対象件数でスコアを取得している。

**T4 実装者へ:** `_rerank()` に `rerank_score` を追加する際は、上記の
`relevance_score` をそのままそのフィールドに格納すること。フィールド名・
値の意味論(0〜1のVoyage relevance_score、降順)を本スクリプトと一致させないと、
ここでキャリブレーションした THETA が無意味になる。

## レート制限対策

Voyage AI 無支払い枠は 3RPM 程度に制限され 429 が起きやすい
(`ingestion/indexer.py` の `_pace_embed_call` と同じ既知の問題)。本スクリプトは
同実装を踏襲し、embed・rerank 双方の Voyage 呼び出しの前に
`settings.ingest_embed_min_interval_sec`(既定21秒)以上の間隔を空ける。
130件 × 最大2回(embed+rerank)の呼び出しがあるため、全件完走には数十分規模の
時間を要する(許容。焦って間隔を詰めない)。

## 再開性

出力(`dataset/routing_scores.jsonl`)は1件処理するごとに逐次追記する。
既に `status: "ok"` で記録済みの id は再実行時にスキップするため、途中で
中断しても再開できる。連続してエラーが続く場合は `--max-consecutive-errors`
(既定3)で打ち切り、進捗を報告する。

## 実行方法

    cd backend && uv run python evals/analyze_score_distribution.py
    # 既存結果の分布統計のみ再表示したい場合(APIを叩かない):
    cd backend && uv run python evals/analyze_score_distribution.py --stats-only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional

import voyageai
from sqlalchemy.orm import Session

from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.retrieval.searcher import _embed_query, _hybrid_search

DATASET_PATH = Path(__file__).resolve().parent / "dataset" / "routing.jsonl"
OUTPUT_PATH = Path(__file__).resolve().parent / "dataset" / "routing_scores.jsonl"

_last_voyage_call_at: Optional[float] = None


def _pace_voyage_call() -> None:
    """Voyage呼び出し(embed/rerank共通)の間隔が
    settings.ingest_embed_min_interval_sec 未満にならないよう待機する。
    ingestion/indexer.py::_pace_embed_call の実装を踏襲(アプリコードは変更しない
    ため、このスクリプト内に複製している)。"""
    global _last_voyage_call_at
    now = time.monotonic()
    if _last_voyage_call_at is not None:
        wait = settings.ingest_embed_min_interval_sec - (now - _last_voyage_call_at)
        if wait > 0:
            time.sleep(wait)
    _last_voyage_call_at = time.monotonic()


def load_dataset(path: Path) -> List[Dict[str, Any]]:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def load_records(path: Path) -> Dict[str, Dict[str, Any]]:
    """既存出力を読み、id ごとに最新のレコードを返す(再実行時の重複行は後勝ち)。"""
    latest: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return latest
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            latest[rec["id"]] = rec
    return latest


def append_record(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def search_query_for(item: Dict[str, Any]) -> str:
    """followup は rewrite(condense拡張)がまだ存在しないため、T1で人手作成した
    expected_search_query を使う(T2ブリーフ 作業項目1)。それ以外は query を使う。"""
    if item["category"] == "followup" and item.get("expected_search_query"):
        return str(item["expected_search_query"])
    return str(item["query"])


def collect_scores(
    item: Dict[str, Any], db: Session, voyage_client: voyageai.Client
) -> Dict[str, Any]:
    query = search_query_for(item)

    _pace_voyage_call()
    emb = _embed_query(query)
    candidates = _hybrid_search(
        db, query, emb, settings.candidate_k, settings.rrf_k, settings.fuse_k,
    )

    scores: List[float] = []
    if candidates:
        _pace_voyage_call()
        documents = [c["content"] for c in candidates]
        result = voyage_client.rerank(
            query=query,
            documents=documents,
            model="rerank-2.5",
            top_k=settings.rerank_top_k,
        )
        scores = [r.relevance_score for r in result.results]

    return {
        "id": item["id"],
        "category": item["category"],
        "split": item["split"],
        "expected_route": item["expected_route"],
        "search_query": query,
        "num_fused_candidates": len(candidates),
        "scores": scores,
        "top_score": scores[0] if scores else None,
        "status": "ok",
    }


def _histogram(values: List[float], bins: int = 20, lo: float = 0.0, hi: float = 1.0) -> str:
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = int((v - lo) / width)
        idx = max(0, min(bins - 1, idx))
        counts[idx] += 1
    max_count = max(counts) if counts else 0
    lines = []
    for i, c in enumerate(counts):
        lo_b = lo + i * width
        hi_b = lo_b + width
        bar_len = int(40 * c / max_count) if max_count else 0
        lines.append(f"  [{lo_b:.2f},{hi_b:.2f}) {'#' * bar_len} {c}")
    return "\n".join(lines)


def _stats_block(values: List[float]) -> str:
    if not values:
        return "  (データなし)"
    return (
        f"  n={len(values)} min={min(values):.4f} max={max(values):.4f} "
        f"mean={mean(values):.4f} median={median(values):.4f}"
    )


def print_stats(records: List[Dict[str, Any]]) -> None:
    ok_records = [r for r in records if r.get("status") == "ok"]
    error_records = [r for r in records if r.get("status") != "ok"]
    print(f"\n=== 収集済み: {len(ok_records)}件 (エラー: {len(error_records)}件) ===")
    if error_records:
        print("エラーid一覧:", [r["id"] for r in error_records])

    by_route: Dict[str, List[float]] = {"grounded": [], "direct": []}
    by_category: Dict[str, List[float]] = {}
    for r in ok_records:
        top = r.get("top_score")
        if top is None:
            continue
        by_route.setdefault(r["expected_route"], []).append(top)
        by_category.setdefault(r["category"], []).append(top)

    print("\n--- expected_route 別 top_score 分布 ---")
    for route in ("grounded", "direct"):
        print(f"\n[{route}]")
        print(_stats_block(by_route.get(route, [])))
        print(_histogram(by_route.get(route, [])))

    print("\n--- category 別 top_score 分布 ---")
    for cat in sorted(by_category.keys()):
        print(f"\n[{cat}]")
        print(_stats_block(by_category[cat]))

    grounded_scores = by_route.get("grounded", [])
    direct_scores = by_route.get("direct", [])
    if grounded_scores and direct_scores:
        print("\n--- 分離度の目安 ---")
        print(f"  grounded top_score min={min(grounded_scores):.4f}")
        print(f"  direct   top_score max={max(direct_scores):.4f}")
        overlap = min(grounded_scores) <= max(direct_scores)
        print(f"  重なり(direct max >= grounded min): {overlap}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="先頭N件のみ処理(動作確認用)")
    parser.add_argument("--max-consecutive-errors", type=int, default=3)
    parser.add_argument(
        "--stats-only", action="store_true", help="APIを叩かず既存 routing_scores.jsonl の統計のみ表示"
    )
    args = parser.parse_args()

    if args.stats_only:
        existing = load_records(OUTPUT_PATH)
        print_stats(list(existing.values()))
        return

    dataset = load_dataset(DATASET_PATH)
    if args.limit:
        dataset = dataset[: args.limit]

    done = load_records(OUTPUT_PATH)
    voyage_client = voyageai.Client(
        api_key=settings.voyage_api_key, max_retries=settings.voyage_max_retries
    )
    db = SessionLocal()
    consecutive_errors = 0
    processed = 0
    try:
        for i, item in enumerate(dataset):
            if done.get(item["id"], {}).get("status") == "ok":
                print(f"[{i + 1}/{len(dataset)}] {item['id']}: skip (既に完了)")
                continue
            print(f"[{i + 1}/{len(dataset)}] {item['id']} ({item['category']}/{item['split']}): 実行中...")
            try:
                record = collect_scores(item, db, voyage_client)
                append_record(OUTPUT_PATH, record)
                consecutive_errors = 0
                processed += 1
                print(f"  -> top_score={record['top_score']} (candidates={record['num_fused_candidates']})")
            except Exception as e:  # noqa: BLE001 - eval一回性スクリプト。エラーは記録して続行する
                consecutive_errors += 1
                err_record = {
                    "id": item["id"],
                    "category": item["category"],
                    "split": item["split"],
                    "expected_route": item["expected_route"],
                    "status": "error",
                    "error": str(e),
                }
                append_record(OUTPUT_PATH, err_record)
                print(f"  !! error: {e}", file=sys.stderr)
                if consecutive_errors >= args.max_consecutive_errors:
                    print(
                        f"連続{consecutive_errors}件失敗のため中断します。"
                        f"進捗: 今回処理{processed}件(累計は再実行時のskip件数で確認)。"
                        f"再実行すれば完了分はスキップして再開します。",
                        file=sys.stderr,
                    )
                    sys.exit(1)
    finally:
        db.close()

    print(f"\n完了: 今回 {processed}件処理しました。")
    print_stats(list(load_records(OUTPUT_PATH).values()))


if __name__ == "__main__":
    main()
