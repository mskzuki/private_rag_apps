"""M8 T2: THETA_HIGH のキャリブレーション(GO/NO-GO判定)。

`generate_dataset.py` / `analyze_score_distribution.py` / `recalibrate_theta.py` と同じ
位置づけの importable パッケージ外の一回性スクリプト
(docs/specs/26071715-m8_clarify_route/spec.md §6.1, tasklist T2 参照)。

## 入力データの結合方針(重要)

`expected_route` は **`evals/dataset/routing.jsonl`(M8 T1 で改訂した3値ラベル)を
信頼できる情報源とする**。`evals/dataset/routing_eval_results.jsonl` に記録済みの
`expected_route` フィールドは、各レコードの収集当時(T1改訂前)にコピーされた
値のまま凍結されており、T1 で `clarify` に変更した8件については古い2値
(`direct`)のままである(`evals/routing.py::build_report()`が現状このフィールドを
そのまま信頼しているのは既知の制約であり、T6で解消する。tasklist T2 実装ノート
参照)。本スクリプトは `routing_eval_results.jsonl` からは `scores`(rerank score
全件の生配列)のみを使い、`expected_route`/`category`/`split` は必ず
`routing.jsonl` から引く。

## grade() の3値判定ロジックの再現(スペック §4.3)

```python
kept = [s for s in scores if s is None or s >= theta]
top_score = kept[0] if kept else None  # scoresは既存実装同様retrievedの降順を維持
if not kept:
    route = "direct"
elif top_score is not None and top_score >= theta_high:
    route = "grounded"
else:
    route = "clarify"
```

THETA(0.56)は不変。THETA_HIGHの候補値をgrid searchし、calibration splitのみで
決定し、holdoutには1回だけ適用する(M7 T2/T4と同じ正規の手順)。

## 再現方法

    cd backend && uv run python evals/analyze_theta_high.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

THETA = 0.56

DATASET_PATH = Path(__file__).resolve().parent / "dataset" / "routing.jsonl"
RESULTS_PATH = Path(__file__).resolve().parent / "dataset" / "routing_eval_results.jsonl"
REPORT_JSON_PATH = Path(__file__).resolve().parent / "reports" / "m8-theta-high-result.json"
REPORT_MD_PATH = Path(__file__).resolve().parent / "reports" / "m8-theta-high-result.md"

# GO/NO-GO 基準(tasklist T2 作業項目4。スコア分布の実データを見た上でこのタスク内で確定)。
# grounded/direct の既存基準(M7 §7.2)は不変(THETA自体は変えないため)。
# clarify 固有の基準は §9.2 のグリッドサーチ結果を見て決定し、以下の値として確定する。
HOLDOUT_GROUNDED_MISS_MAX = 1  # M7から不変(THETA自体は変更しないため)
HOLDOUT_DIRECT_WRONG_MAX = 3  # M7から不変
HOLDOUT_CLARIFY_RECALL_MIN = 0.5  # holdoutのclarify期待2件中1件以上を正しくclarifyと予測できること


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def load_latest_by_id(path: Path) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for rec in load_jsonl(path):
        latest[rec["id"]] = rec
    return latest


def predict_route3(
    scores: Optional[List[Optional[float]]], theta: float, theta_high: float
) -> str:
    """grade()(スペック §4.3)の3値判定ロジックの純関数再現。"""
    if not scores:
        return "direct"
    kept = [s for s in scores if s is None or s >= theta]
    if not kept:
        return "direct"
    top_score = kept[0]
    if top_score is not None and top_score >= theta_high:
        return "grounded"
    return "clarify"


def build_joined_records() -> tuple[List[Dict[str, Any]], List[str]]:
    """routing.jsonl(expected_route等の情報源) と routing_eval_results.jsonl(scores)
    をidで結合する。__raw比較用レコードは対象外(routing.jsonl本来の項目のみ)。"""
    dataset = load_jsonl(DATASET_PATH)
    results = load_latest_by_id(RESULTS_PATH)

    joined = []
    error_ids = []
    for item in dataset:
        rec = results.get(item["id"])
        if rec is None or rec.get("status") != "ok":
            error_ids.append(item["id"])
            continue
        joined.append(
            {
                "id": item["id"],
                "category": item["category"],
                "split": item["split"],
                "expected_route": item["expected_route"],
                "scores": rec.get("scores"),
            }
        )
    return joined, error_ids


def confusion_matrix(records: List[Dict[str, Any]], theta_high: float) -> Dict[str, Any]:
    grounded_total = sum(1 for r in records if r["expected_route"] == "grounded")
    direct_total = sum(1 for r in records if r["expected_route"] == "direct")
    clarify_total = sum(1 for r in records if r["expected_route"] == "clarify")

    grounded_miss_ids = []  # expected grounded, predicted direct(M7から不変の安全指標)
    direct_wrong_ids = []  # expected direct, predicted grounded(M7から不変の指標)
    clarify_recall_hit_ids = []  # expected clarify, predicted clarify
    clarify_recall_miss_ids = []  # expected clarify, predicted != clarify
    clarify_precision_fp_ids = []  # expected grounded/direct, predicted clarify(過剰発火)

    for r in records:
        pred = predict_route3(r["scores"], THETA, theta_high)
        exp = r["expected_route"]
        if exp == "grounded" and pred == "direct":
            grounded_miss_ids.append(r["id"])
        if exp == "direct" and pred == "grounded":
            direct_wrong_ids.append(r["id"])
        if exp == "clarify":
            if pred == "clarify":
                clarify_recall_hit_ids.append(r["id"])
            else:
                clarify_recall_miss_ids.append(r["id"])
        elif pred == "clarify":
            clarify_precision_fp_ids.append(r["id"])

    clarify_recall = (
        len(clarify_recall_hit_ids) / clarify_total if clarify_total else None
    )
    clarify_precision_denom = len(clarify_recall_hit_ids) + len(clarify_precision_fp_ids)
    clarify_precision = (
        len(clarify_recall_hit_ids) / clarify_precision_denom
        if clarify_precision_denom
        else None
    )

    return {
        "theta_high": theta_high,
        "grounded_total": grounded_total,
        "direct_total": direct_total,
        "clarify_total": clarify_total,
        "grounded_miss": len(grounded_miss_ids),
        "grounded_miss_ids": grounded_miss_ids,
        "direct_wrong": len(direct_wrong_ids),
        "direct_wrong_ids": direct_wrong_ids,
        "clarify_recall": clarify_recall,
        "clarify_recall_hit_ids": clarify_recall_hit_ids,
        "clarify_recall_miss_ids": clarify_recall_miss_ids,
        "clarify_precision": clarify_precision,
        "clarify_precision_fp_ids": clarify_precision_fp_ids,
    }


def grid_search(calibration: List[Dict[str, Any]], step: float) -> List[Dict[str, Any]]:
    """THETA(0.56)からTHETA_HIGHとして意味を持つ上限1.0まで候補を走査し、
    各候補の3値混同行列を返す(全候補を報告に残す。M7のgrid_searchと異なり
    「1つの最良値だけ返す」のではなく分布全体を可視化する。理由: clarifyの
    件数が少なく(§9.5)、単一の制約式による自動選定よりも分布を見た人手判断が
    適切なため)。"""
    lo, hi = THETA, 1.0
    n_steps = int(round((hi - lo) / step))
    candidates = [round(lo + i * step, 6) for i in range(n_steps + 1)]
    return [confusion_matrix(calibration, c) for c in candidates]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=float, default=0.01)
    parser.add_argument(
        "--theta-high",
        type=float,
        default=None,
        help="指定した場合、grid searchをスキップしこの値でcalibration/holdout両方を評価する",
    )
    args = parser.parse_args()

    joined, error_ids = build_joined_records()
    print(f"結合: {len(joined)}件(routing.jsonl基準), 未収集/エラー: {len(error_ids)}件")
    if error_ids:
        print(f"  未収集/エラーid: {error_ids}")

    calibration = [r for r in joined if r["split"] == "calibration"]
    holdout = [r for r in joined if r["split"] == "holdout"]
    print(f"calibration: {len(calibration)}件, holdout: {len(holdout)}件")

    if args.theta_high is not None:
        theta_high = args.theta_high
        grid: List[Dict[str, Any]] = []
    else:
        grid = grid_search(calibration, step=args.step)
        print(f"\n=== calibration grid search(step={args.step}) ===")
        for row in grid:
            print(
                f"  theta_high={row['theta_high']:.2f}: "
                f"clarify_recall={row['clarify_recall']}, "
                f"clarify_precision={row['clarify_precision']}, "
                f"grounded_miss={row['grounded_miss']}/{row['grounded_total']}, "
                f"direct_wrong={row['direct_wrong']}/{row['direct_total']}, "
                f"clarify_fp(from grounded)={row['clarify_precision_fp_ids']}"
            )
        return

    calibration_result = confusion_matrix(calibration, theta_high)
    holdout_result = confusion_matrix(holdout, theta_high)

    print(f"\n選定 THETA_HIGH = {theta_high}")
    print("calibration:", json.dumps(calibration_result, ensure_ascii=False))
    print("holdout:", json.dumps(holdout_result, ensure_ascii=False))

    go = (
        holdout_result["grounded_miss"] <= HOLDOUT_GROUNDED_MISS_MAX
        and holdout_result["direct_wrong"] <= HOLDOUT_DIRECT_WRONG_MAX
        and (
            holdout_result["clarify_recall"] is None
            or holdout_result["clarify_recall"] >= HOLDOUT_CLARIFY_RECALL_MIN
        )
    )
    verdict = "GO" if go else "NO-GO"
    print(f"\n=== 判定: {verdict} ===")

    result = {
        "theta": THETA,
        "theta_high": theta_high,
        "collected": len(joined),
        "error_ids": error_ids,
        "calibration": calibration_result,
        "holdout": holdout_result,
        "criteria": {
            "holdout_grounded_miss_max": HOLDOUT_GROUNDED_MISS_MAX,
            "holdout_direct_wrong_max": HOLDOUT_DIRECT_WRONG_MAX,
            "holdout_clarify_recall_min": HOLDOUT_CLARIFY_RECALL_MIN,
        },
        "verdict": verdict,
    }
    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    md = f"""# M8 THETA_HIGH キャリブレーション結果

入力: `routing.jsonl`(expected_route, T1改訂済み) x `routing_eval_results.jsonl`(scores)
THETA(不変): {THETA}
選定 THETA_HIGH: **{theta_high}**

## calibration

| 指標 | 実績 |
|---|---|
| grounded見逃し | {calibration_result['grounded_miss']}/{calibration_result['grounded_total']} |
| direct誤り | {calibration_result['direct_wrong']}/{calibration_result['direct_total']} |
| clarify再現率 | {calibration_result['clarify_recall']}({calibration_result['clarify_total']}件中) |
| clarify適合率 | {calibration_result['clarify_precision']} |

## holdout(1回のみ適用)

| 指標 | 実績 | 基準 |
|---|---|---|
| grounded見逃し | {holdout_result['grounded_miss']}/{holdout_result['grounded_total']}(id={holdout_result['grounded_miss_ids']}) | <= {HOLDOUT_GROUNDED_MISS_MAX} |
| direct誤り | {holdout_result['direct_wrong']}/{holdout_result['direct_total']}(id={holdout_result['direct_wrong_ids']}) | <= {HOLDOUT_DIRECT_WRONG_MAX} |
| clarify再現率 | {holdout_result['clarify_recall']}(id hit={holdout_result['clarify_recall_hit_ids']}, miss={holdout_result['clarify_recall_miss_ids']}) | >= {HOLDOUT_CLARIFY_RECALL_MIN} |

## 判定

**{verdict}**
"""
    REPORT_MD_PATH.write_text(md, encoding="utf-8")
    print(f"\n結果を {REPORT_JSON_PATH} / {REPORT_MD_PATH} に保存しました。")


if __name__ == "__main__":
    main()
