"""M7 T4: direct経路のgroundedness eval(コーパス固有内容の捏造検出)。

対応タスク: `.superpowers/sdd/task-T4-brief.md` 作業項目6。
参照スペック: `docs/specs/m7_adaptive_routing.md`(rev.3)§7.2 direct groundedness。

`generate_dataset.py` / `generate_routing_dataset.py` / `analyze_score_distribution.py` と
同じ位置づけの、importableパッケージ外の一回性スクリプト(`backend/evals/`配下)。

## 対象データの選定

`routing.jsonl` の `category == "general"` の項目(全40件。T1により、コーパス4ファイルへの
grep+目視でコーパスに無関係であることを確認済み。routing-README.md §4参照)から、
holdout split 10件 + calibration split 10件 = 20件を、id昇順で決定的に選ぶ
(乱数を使わない。再現性のため)。

general カテゴリは定義上 expected_route=="direct" であるため、grade(retrieval+閾値判定)を
経由せず、`generate_direct_answer_stream(query)` を直接呼び出す。これは
「direct経路のプロンプト/生成が、コーパス固有の内容を捏造しないか」という
本evalの関心そのものであり、経路判定(grade)自体の正確性は別途 `make eval-routing` の
grounded見逃し/direct誤り指標でカバーされているため、ここでは対象外とする。

## judgeと人手裁定の手順(重要。ブリーフ作業項目6で規定を求められている運用規約)

1. 各回答を `evaluate_direct_groundedness(question, answer)` (LLM-as-judge、
   prompts/judge.py::JUDGE_DIRECT_GROUNDEDNESS_PROMPT)で評価する。judgeは
   コーパスの実データを見ずに、回答テキストのみから「コーパス固有の内容を捏造して
   いないか」をヒューリスティックに判定する(スコア1=問題なし、0=違反疑い)。
2. judgeがscore=0(違反疑い)と判定した項目は、**このスクリプトの実行者(人間または
   人手裁定を担当するエージェント)が実際に回答本文を読み、真に「コーパス固有の
   内容を捏造しているか」を判断する**。judgeのヒューリスティックは偽陽性を含みうる
   (例: 一般的なソフトウェア設計の話をしているだけなのに「システムでは」という
   言い回しに反応する等)。
3. 人手裁定の結果は `dataset/direct_groundedness_results.jsonl` の各レコードに
   `human_verdict`("true_violation" | "false_positive")と `human_rationale` を
   追記する形で記録する(このスクリプト自体は自動追記しない。裁定担当者が
   `record_human_verdict()` を呼ぶか、jsonlを直接編集する)。
4. 完了条件(brief): 人手裁定後の**真の違反0件**。judgeの偽陽性のみでブロックしない。

## Voyageレート制限について

本evalはretrieval(Voyage)を一切使わない(direct経路はcontext注入なしのため)。
LLM呼び出し(生成+judge)のみ。OpenAI/Ollamaのレート制限・quota問題に遭遇した場合は
既存のfallback(condense/generate_answer_streamと同様、例外はerrorイベントとして
記録される)に従う。

## 再開性

出力(`dataset/direct_groundedness_results.jsonl`)は1件処理するごとに逐次追記する。
既に処理済みのidは再実行時にスキップする。

## 実行方法

    cd backend && uv run python ../backend/evals/direct_groundedness_eval.py
    cd backend && uv run python ../backend/evals/direct_groundedness_eval.py --stats-only
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from private_rag_apps.evals.judge import evaluate_direct_groundedness
from private_rag_apps.generation.generator import generate_direct_answer_stream

ROUTING_DATASET_PATH = Path(__file__).resolve().parent / "dataset" / "routing.jsonl"
OUTPUT_PATH = Path(__file__).resolve().parent / "dataset" / "direct_groundedness_results.jsonl"
REPORT_PATH = Path(__file__).resolve().parent / "reports" / "m7-direct-groundedness.md"

SAMPLE_SIZE_PER_SPLIT = 10


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
    latest: Dict[str, Dict[str, Any]] = {}
    for rec in load_jsonl(path):
        latest[rec["id"]] = rec
    return latest


def append_record(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def select_items() -> List[Dict[str, Any]]:
    dataset = load_jsonl(ROUTING_DATASET_PATH)
    general = [item for item in dataset if item["category"] == "general"]
    holdout = sorted((i for i in general if i["split"] == "holdout"), key=lambda i: i["id"])
    calibration = sorted((i for i in general if i["split"] == "calibration"), key=lambda i: i["id"])
    return holdout[:SAMPLE_SIZE_PER_SPLIT] + calibration[:SAMPLE_SIZE_PER_SPLIT]


def generate_direct_answer(query: str) -> tuple[str, bool]:
    """generate_direct_answer_stream を実行し、full answerテキストとerror有無を返す"""
    full_text = ""
    had_error = False
    for event in generate_direct_answer_stream(query):
        if event["event"] == "token":
            full_text += event["data"]
        elif event["event"] == "error":
            had_error = True
    return full_text, had_error


def process(args: argparse.Namespace) -> None:
    items = select_items()
    done = load_latest_by_id(OUTPUT_PATH)

    for i, item in enumerate(items):
        if done.get(item["id"], {}).get("status") == "ok":
            print(f"[{i + 1}/{len(items)}] {item['id']}: skip (既に完了)")
            continue

        print(f"[{i + 1}/{len(items)}] {item['id']} ({item['split']}): 実行中...")
        try:
            answer, had_error = generate_direct_answer(item["query"])
            if had_error:
                append_record(
                    OUTPUT_PATH,
                    {
                        "id": item["id"],
                        "split": item["split"],
                        "query": item["query"],
                        "status": "error",
                        "error": "generate_direct_answer_stream emitted an error event",
                    },
                )
                print("  !! generation error")
                continue

            judge_result = evaluate_direct_groundedness(item["query"], answer)
            record = {
                "id": item["id"],
                "split": item["split"],
                "query": item["query"],
                "answer": answer,
                "judge_score": judge_result.get("score", 0),
                "judge_rationale": judge_result.get("rationale", ""),
                # 人手裁定はこのスクリプト実行後に別途追記する(モジュールdocstring参照)
                "human_verdict": None,
                "human_rationale": None,
                "status": "ok",
            }
            append_record(OUTPUT_PATH, record)
            print(f"  -> judge_score={record['judge_score']}")
        except Exception as e:  # noqa: BLE001 - eval一回性スクリプト。エラーは記録して続行する
            append_record(
                OUTPUT_PATH,
                {
                    "id": item["id"],
                    "split": item["split"],
                    "query": item["query"],
                    "status": "error",
                    "error": str(e),
                },
            )
            print(f"  !! error: {e}")


def print_summary() -> None:
    records = list(load_latest_by_id(OUTPUT_PATH).values())
    ok_records = [r for r in records if r.get("status") == "ok"]
    error_records = [r for r in records if r.get("status") != "ok"]

    violations = [r for r in ok_records if r.get("judge_score") == 0]
    true_violations = [r for r in violations if r.get("human_verdict") == "true_violation"]
    false_positives = [r for r in violations if r.get("human_verdict") == "false_positive"]
    unadjudicated = [r for r in violations if r.get("human_verdict") is None]

    print("\n=== direct groundedness eval 結果 ===")
    print(f"収集済み: {len(ok_records)}件 (エラー: {len(error_records)}件)")
    print(f"judge違反疑い: {len(violations)}件 (id={[r['id'] for r in violations]})")
    print(
        f"  人手裁定: 真の違反={len(true_violations)}件, 偽陽性={len(false_positives)}件, "
        f"未裁定={len(unadjudicated)}件"
    )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# M7 direct groundedness eval 結果",
        "",
        f"収集済み {len(ok_records)}件（エラー {len(error_records)}件）。"
        f"judge違反疑い {len(violations)}件。",
        "",
        "| id | split | judge_score | judge_rationale | human_verdict | human_rationale |",
        "|---|---|---|---|---|---|",
    ]
    for r in ok_records:
        lines.append(
            f"| {r['id']} | {r['split']} | {r['judge_score']} | "
            f"{str(r.get('judge_rationale', '')).replace(chr(10), ' ')} | "
            f"{r.get('human_verdict') or ''} | {r.get('human_rationale') or ''} |"
        )
    lines.append("")
    lines.append(f"**真の違反(人手裁定後): {len(true_violations)}件**")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"レポートを {REPORT_PATH} に保存しました。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stats-only", action="store_true", help="生成/judgeを実行せず既存結果の集計のみ表示"
    )
    args = parser.parse_args()

    if not args.stats_only:
        process(args)

    print_summary()


if __name__ == "__main__":
    main()
