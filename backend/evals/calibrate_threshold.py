"""M7 T2: calibration split 上で grid search により THETA を決定し、
holdout split に一度だけ適用して GO/NO-GO を判定するスクリプト。

`docs/specs/m7_adaptive_routing.md`(rev.3)§7.2 の基準に従う:

- calibration: 「grounded 見逃し率 <= 0.05」を制約に、direct 適中数を最大化する
  THETA を grid search で決定する(calibration split のみを使う)
- holdout: 決定した THETA を **一度だけ** 適用し、件数基準で合否を判定する
    - grounded 見逃し(expected=grounded が direct になった件数) <= 1件
    - direct 誤り(expected=direct が grounded になった件数)     <= 3件
  両方を満たせば GO(T3へ進む)。満たさなければ NO-GO(§7.4 のLLM graderスペック
  起票へ切り替え)。

tie-break: constraint を満たす候補が複数ある場合、direct 適中数が最大のものの
中から **最も低い theta** を選ぶ。spec §2 の「誤判定コストの非対称性により、
迷ったら低めに引く」という原則に従う(低いthetaほどgroundedに倒れやすく安全)。

**holdout の再利用は1回まで。** このスクリプトを再実行して同じ theta を
holdoutに再適用するのは冪等なので許容するが、holdout の結果を見て theta を
手動で変えて再度このスクリプトを走らせる(=holdoutを2回目の意思決定に使う)行為は
spec違反。theta を変える場合は calibration に戻ること(brief 作業項目5)。

入力: dataset/routing_scores.jsonl (analyze_score_distribution.py の出力)
出力: reports/m7-calibration-result.json (機械可読な決定記録)

実行方法:
    cd backend && uv run python evals/calibrate_threshold.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

SCORES_PATH = Path(__file__).resolve().parent / "dataset" / "routing_scores.jsonl"
REPORT_JSON_PATH = Path(__file__).resolve().parent / "reports" / "m7-calibration-result.json"

GROUNDED_MISS_RATE_CONSTRAINT = 0.05
HOLDOUT_GROUNDED_MISS_MAX = 1
HOLDOUT_DIRECT_WRONG_MAX = 3


def load_scores(path: Path) -> List[Dict[str, Any]]:
    """id ごとに最新のレコードを返す(再実行時の重複行は後勝ち)。"""
    latest: Dict[str, Dict[str, Any]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            latest[rec["id"]] = rec
    return list(latest.values())


def predict_route(top_score: Optional[float], theta: float) -> str:
    """grade の純関数ロジックの再現(kept = score>=theta のchunkが1件以上ならgrounded)。
    top_score は候補チャンク群のうち最高スコア = kept判定に使う唯一の値
    (kept>=1 <=> top_score>=theta)。candidatesが0件(top_score=None)ならdirect。"""
    if top_score is None:
        return "direct"
    return "grounded" if top_score >= theta else "direct"


def evaluate(records: List[Dict[str, Any]], theta: float) -> Dict[str, Any]:
    grounded_total = sum(1 for r in records if r["expected_route"] == "grounded")
    direct_total = sum(1 for r in records if r["expected_route"] == "direct")
    grounded_miss = 0
    direct_correct = 0
    direct_wrong = 0
    grounded_miss_ids = []
    direct_wrong_ids = []
    for r in records:
        pred = predict_route(r.get("top_score"), theta)
        if r["expected_route"] == "grounded" and pred == "direct":
            grounded_miss += 1
            grounded_miss_ids.append(r["id"])
        if r["expected_route"] == "direct":
            if pred == "direct":
                direct_correct += 1
            else:
                direct_wrong += 1
                direct_wrong_ids.append(r["id"])
    grounded_miss_rate = grounded_miss / grounded_total if grounded_total else 0.0
    return {
        "theta": theta,
        "grounded_total": grounded_total,
        "direct_total": direct_total,
        "grounded_miss": grounded_miss,
        "grounded_miss_rate": grounded_miss_rate,
        "grounded_miss_ids": grounded_miss_ids,
        "direct_correct": direct_correct,
        "direct_wrong": direct_wrong,
        "direct_wrong_ids": direct_wrong_ids,
    }


def grid_search(calibration: List[Dict[str, Any]], step: float) -> Dict[str, Any]:
    lo, hi = 0.0, 1.0
    n_steps = int(round((hi - lo) / step))
    candidates = [round(lo + i * step, 6) for i in range(n_steps + 1)]

    best: Optional[Dict[str, Any]] = None
    all_results = []
    for theta in candidates:
        result = evaluate(calibration, theta)
        all_results.append(result)
        if result["grounded_miss_rate"] <= GROUNDED_MISS_RATE_CONSTRAINT:
            if best is None or result["direct_correct"] > best["direct_correct"]:
                best = result
            elif result["direct_correct"] == best["direct_correct"] and theta < best["theta"]:
                best = result
    return {"best": best, "all_results": all_results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=float, default=0.01, help="grid searchの刻み幅")
    args = parser.parse_args()

    records = load_scores(SCORES_PATH)
    ok_records = [r for r in records if r.get("status") == "ok"]
    bad_records = [r for r in records if r.get("status") != "ok"]

    print(f"入力: {len(records)}件 (ok: {len(ok_records)}, error/未完了: {len(bad_records)})")
    if bad_records:
        print(f"  警告: 以下のidはスコアが無く判定から除外されます: {[r['id'] for r in bad_records]}")

    calibration = [r for r in ok_records if r["split"] == "calibration"]
    holdout = [r for r in ok_records if r["split"] == "holdout"]
    print(f"calibration: {len(calibration)}件, holdout: {len(holdout)}件")

    gs = grid_search(calibration, step=args.step)
    best = gs["best"]

    if best is None:
        print(
            "\nNO-GO: calibration上で「grounded見逃し率<=0.05」を満たすTHETAが"
            "存在しません(スコア分布が分離不能、あるいは閾値方式が原理的に成立しません)。"
        )
        result: Dict[str, Any] = {
            "theta": None,
            "calibration": None,
            "holdout": None,
            "verdict": "NO-GO",
            "reason": "no theta satisfies grounded_miss_rate<=0.05 constraint on calibration split",
        }
        REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"結果を {REPORT_JSON_PATH} に保存しました。")
        return

    theta = best["theta"]
    print(f"\n選定THETA (calibrationのみで決定): {theta}")
    print(
        f"  calibration上: grounded_miss={best['grounded_miss']}/{best['grounded_total']} "
        f"(rate={best['grounded_miss_rate']:.4f}, 制約<=0.05), "
        f"direct_correct={best['direct_correct']}/{best['direct_total']}"
    )
    if best["grounded_miss_ids"]:
        print(f"  calibration grounded見逃しid: {best['grounded_miss_ids']}")

    holdout_result = evaluate(holdout, theta)
    print("\nholdout適用結果 (1回のみ使用):")
    print(
        f"  grounded_miss={holdout_result['grounded_miss']}/{holdout_result['grounded_total']} "
        f"(基準: <=1件) id={holdout_result['grounded_miss_ids']}"
    )
    print(
        f"  direct_wrong={holdout_result['direct_wrong']}/{holdout_result['direct_total']} "
        f"(基準: <=3件) id={holdout_result['direct_wrong_ids']}"
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
        "calibration": best,
        "holdout": holdout_result,
        "verdict": verdict,
    }
    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n結果を {REPORT_JSON_PATH} に保存しました。")


if __name__ == "__main__":
    main()
