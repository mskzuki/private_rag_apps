"""M7 T4: THETAの再キャリブレーション。

## 経緯

ADR 0001(THETA=0.56)はT2時点でVoyageレート制限により130件中4件
(g033, a016, a004, a029)が未収集のまま、calibration 89/91件・holdout 37/39件で
決定された。この4件のうちholdout側の2件(a004, a029)は「今回追加で収集できたら
判定が変わりうる」とADR 0001 Consequences節で明記されていた既知のリスクだった。

T4の`make eval-routing`(`evals/routing.py`)実行で130件中149件
(元データ130件のうちfollowupのrewrite有り分の"__raw"比較用データを含むため149件。
"__raw"を除く本来の130件相当のうちエラーは1件[c038]のみ)を収集した結果、
THETA=0.56ではholdout上で direct誤り 4/18件(基準<=3件を1件超過)となりNO-GO判定と
なった。ユーザーとコントローラーの判断により、calibrationデータがより完備したことを
理由に、正規の手順(calibrationのみでgrid search → holdoutに1回だけ適用)で
THETAを再キャリブレーションする。

## 方法

T2の`calibrate_threshold.py`と同じロジック(constraint: grounded見逃し率<=0.05、
目的関数: direct適中数最大化、tie-break: より低いtheta)を、入力データを
`evals/dataset/routing_eval_results.jsonl`(T4の`make eval-routing`が収集した149件、
"__raw"比較用データは除外)に差し替えて再実行する。

## 再現方法

    cd backend && uv run python ../backend/evals/recalibrate_theta.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

RESULTS_PATH = Path(__file__).resolve().parent / "dataset" / "routing_eval_results.jsonl"
REPORT_JSON_PATH = Path(__file__).resolve().parent / "reports" / "m7-recalibration-result.json"
REPORT_MD_PATH = Path(__file__).resolve().parent / "reports" / "m7-recalibration-result.md"

GROUNDED_MISS_RATE_CONSTRAINT = 0.05
HOLDOUT_GROUNDED_MISS_MAX = 1
HOLDOUT_DIRECT_WRONG_MAX = 3


def load_latest_by_id(path: Path) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            latest[rec["id"]] = rec
    return latest


def predict_route(scores: Optional[List[Optional[float]]], theta: float) -> str:
    """graph/nodes/grade.py::grade() の純関数ロジックの再現。
    **重要(T2のcalibrate_threshold.py::predict_routeからの変更点):** T2時点では
    `top_score is None` を単純に「candidatesが0件」とみなしdirect扱いしていたが、
    T4で`_rerank()`にrerank_score付与を実装した結果、「candidatesはあったが
    Voyageリランクが失敗しスコアが取得できなかった」ケース(例: c004。scoresが
    全件None、kept_count=8)も`top_score=None`になりうることが判明した。
    grade()はスコア欠落チャンクを安全側デフォルトでkept扱いにするため
    (誤判定コストの非対称性。スペック§3.1)、本関数もscoresリスト全体を見て
    grade()と同じ判定(kept = [s for s in scores if s is None or s>=theta])を行う。
    これによりtop_scoreだけを見る簡略化ロジックとの乖離(誤ったdirect誤判定)を防ぐ。
    scoresが空リスト(candidatesが0件で本当にトップスコアが存在しない)ならdirect。"""
    if not scores:
        return "direct"
    kept = [s for s in scores if s is None or s >= theta]
    return "direct" if not kept else "grounded"


def evaluate(records: List[Dict[str, Any]], theta: float) -> Dict[str, Any]:
    grounded_total = sum(1 for r in records if r["expected_route"] == "grounded")
    direct_total = sum(1 for r in records if r["expected_route"] == "direct")
    grounded_miss_ids = []
    direct_wrong_ids = []
    direct_correct = 0
    for r in records:
        pred = predict_route(r.get("scores"), theta)
        if r["expected_route"] == "grounded" and pred == "direct":
            grounded_miss_ids.append(r["id"])
        if r["expected_route"] == "direct":
            if pred == "direct":
                direct_correct += 1
            else:
                direct_wrong_ids.append(r["id"])
    grounded_miss = len(grounded_miss_ids)
    grounded_miss_rate = grounded_miss / grounded_total if grounded_total else 0.0
    return {
        "theta": theta,
        "grounded_total": grounded_total,
        "direct_total": direct_total,
        "grounded_miss": grounded_miss,
        "grounded_miss_rate": grounded_miss_rate,
        "grounded_miss_ids": grounded_miss_ids,
        "direct_correct": direct_correct,
        "direct_wrong": len(direct_wrong_ids),
        "direct_wrong_ids": direct_wrong_ids,
    }


def grid_search(calibration: List[Dict[str, Any]], step: float) -> Optional[Dict[str, Any]]:
    lo, hi = 0.0, 1.0
    n_steps = int(round((hi - lo) / step))
    candidates = [round(lo + i * step, 6) for i in range(n_steps + 1)]

    best: Optional[Dict[str, Any]] = None
    for theta in candidates:
        result = evaluate(calibration, theta)
        if result["grounded_miss_rate"] <= GROUNDED_MISS_RATE_CONSTRAINT:
            if best is None or result["direct_correct"] > best["direct_correct"]:
                best = result
            elif result["direct_correct"] == best["direct_correct"] and theta < best["theta"]:
                best = result
    return best


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=float, default=0.01)
    args = parser.parse_args()

    all_records = list(load_latest_by_id(RESULTS_PATH).values())
    ok_records = [r for r in all_records if r.get("status") == "ok" and "raw_of" not in r]
    error_records = [r for r in all_records if r.get("status") != "ok"]

    print(f"入力: {len(ok_records)}件(ok, __raw除く), エラー: {len(error_records)}件")
    if error_records:
        print(f"  エラーid: {[r['id'] for r in error_records]}")

    calibration = [r for r in ok_records if r["split"] == "calibration"]
    holdout = [r for r in ok_records if r["split"] == "holdout"]
    print(f"calibration: {len(calibration)}件, holdout: {len(holdout)}件")

    best = grid_search(calibration, step=args.step)
    if best is None:
        print("\nNO-GO: calibration上で「grounded見逃し率<=0.05」を満たすTHETAが存在しません。")
        result: Dict[str, Any] = {
            "theta": None,
            "verdict": "NO-GO",
            "reason": "no theta satisfies constraint",
        }
        REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        return

    theta = best["theta"]
    print(f"\n選定THETA(calibrationのみで決定): {theta}")
    print(
        f"  calibration上: grounded_miss={best['grounded_miss']}/{best['grounded_total']} "
        f"(rate={best['grounded_miss_rate']:.4f}, 制約<=0.05), "
        f"direct_correct={best['direct_correct']}/{best['direct_total']}"
    )
    if best["grounded_miss_ids"]:
        print(f"  calibration grounded見逃しid: {best['grounded_miss_ids']}")

    holdout_result = evaluate(holdout, theta)
    print("\nholdout適用結果(1回のみ使用):")
    print(
        f"  grounded_miss={holdout_result['grounded_miss']}/{holdout_result['grounded_total']} "
        f"(基準<=1) id={holdout_result['grounded_miss_ids']}"
    )
    print(
        f"  direct_wrong={holdout_result['direct_wrong']}/{holdout_result['direct_total']} "
        f"(基準<=3) id={holdout_result['direct_wrong_ids']}"
    )

    go = (
        holdout_result["grounded_miss"] <= HOLDOUT_GROUNDED_MISS_MAX
        and holdout_result["direct_wrong"] <= HOLDOUT_DIRECT_WRONG_MAX
    )
    verdict = "GO" if go else "NO-GO"
    print(f"\n=== 判定: {verdict} ===")

    result = {
        "theta": theta,
        "grid_search_step": args.step,
        "collected": len(ok_records),
        "errors": len(error_records),
        "error_ids": [r["id"] for r in error_records],
        "calibration": best,
        "holdout": holdout_result,
        "verdict": verdict,
    }
    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    md = f"""# M7 THETA再キャリブレーション結果

入力: `evals/dataset/routing_eval_results.jsonl`(T4の`make eval-routing`収集分、149件中148件[ok, __raw除く]+エラー1件[{error_records[0]['id'] if error_records else 'なし'}])

## calibration(grid search, step={args.step})

選定THETA: **{theta}**

| 指標 | 実績 |
|---|---|
| grounded見逃し | {best['grounded_miss']}/{best['grounded_total']}(rate={best['grounded_miss_rate']:.4f}, 制約<=0.05) |
| direct適中 | {best['direct_correct']}/{best['direct_total']} |

## holdout(1回のみ適用)

| 指標 | 実績 | 基準 |
|---|---|---|
| grounded見逃し | {holdout_result['grounded_miss']}/{holdout_result['grounded_total']}(id={holdout_result['grounded_miss_ids']}) | <=1 |
| direct誤り | {holdout_result['direct_wrong']}/{holdout_result['direct_total']}(id={holdout_result['direct_wrong_ids']}) | <=3 |

## 判定

**{verdict}**
"""
    REPORT_MD_PATH.write_text(md, encoding="utf-8")
    print(f"\n結果を {REPORT_JSON_PATH} / {REPORT_MD_PATH} に保存しました。")


if __name__ == "__main__":
    main()
