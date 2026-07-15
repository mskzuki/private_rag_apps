"""M7 T4: 複合質問(m3_golden.jsonl の tags=["compound", ...] 5件)の
「一般知識に基づく補足」書式検証。

対応タスク: `.superpowers/sdd/task-T4-brief.md` 作業項目7。
参照スペック: `docs/specs/m7_adaptive_routing.md`(rev.3)§7.1/§7.2 補足書式の遵守。

## 経緯(重要): なぜ`make eval`ではなくこの一回性スクリプトで検証するか

本来この検証は `make eval`(`private_rag_apps.evals.__main__`)に組み込み済み
(compoundタグの項目に対し`evaluate_supplement_format`を自動実行する)。しかし
`docs/adr/0004_m7_make_eval_excluded.md`により、Voyage/OpenAIのレート制限で
`make eval`(36問全件、generate+judge込み)が完走できないため、**M7の完了条件から
`make eval`自体は除外された**。一方「補足書式検証(複合質問5/5)」はT4の完了条件として
引き続き必須(ADR 0004 Decision参照)であるため、本スクリプトで対象の5件(q32-q36)のみを
軽量に(Voyage呼び出しを5件分に限定して)検証する。

`evals/__main__.py`側の実装(compoundタグ検出→`evaluate_supplement_format`呼び出し)自体は
変更・削除しない(将来Voyage/OpenAIアカウント制約が解消され`make eval`が完走可能になれば、
そのまま両方から検証されることになる)。

## 判定方法

各対象問題について、本番と同じ関数(`retrieve_context(strategy="hybrid_rerank")` →
`generate_answer_stream`)で回答を生成し、`evaluate_supplement_format`(LLM-as-judge)で
書式遵守を評価する。judgeの判定はヒューリスティックであるため、**5件全件について
このスクリプトの実行者が実際に回答本文を読み、人手で書式遵守を確認する**
(brief作業項目7「LLM-as-judge + 人手確認」)。人手確認の結果は
`human_confirmed`(true/false)と`human_note`として記録する。

## 完了条件

複合質問5/5で「コーパスでカバーできる部分の引用付き回答」と「一般知識に基づく補足
(区切り線+分離+引用マーカー不使用)」の分離が確認できること(人手確認込み)。

## 実行方法

    cd backend && uv run python ../backend/evals/supplement_format_eval.py
    cd backend && uv run python ../backend/evals/supplement_format_eval.py --stats-only
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.evals.judge import evaluate_supplement_format
from private_rag_apps.evals.schema import load_dataset
from private_rag_apps.generation.generator import generate_answer_stream
from private_rag_apps.retrieval.searcher import retrieve_context

DATASET_PATH = Path(__file__).resolve().parent / "dataset" / "m3_golden.jsonl"
OUTPUT_PATH = Path(__file__).resolve().parent / "dataset" / "supplement_format_results.jsonl"
REPORT_PATH = Path(__file__).resolve().parent / "reports" / "m7-supplement-format.md"

_last_voyage_call_at: Optional[float] = None


def _pace_voyage_call() -> None:
    """retrieve_context内のVoyage呼び出し前にペーシングする
    (evals/routing.py::_pace_voyage_call と同じ実装。アプリコードは変更しない方針のため複製)。"""
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
    latest: Dict[str, Dict[str, Any]] = {}
    for rec in load_jsonl(path):
        latest[rec["id"]] = rec
    return latest


def append_record(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def generate_answer(query: str, chunks: List[Dict[str, Any]]) -> tuple[str, bool]:
    full_text = ""
    had_error = False
    for event in generate_answer_stream(query, chunks):
        if event["event"] == "token":
            full_text += event["data"]
        elif event["event"] == "error":
            had_error = True
    return full_text, had_error


def process(args: argparse.Namespace) -> None:
    dataset = load_dataset(DATASET_PATH)
    compound_items = [item for item in dataset if "compound" in item.tags]
    done = load_latest_by_id(OUTPUT_PATH)

    db = SessionLocal()
    try:
        for i, item in enumerate(compound_items):
            if done.get(item.id, {}).get("status") == "ok":
                print(f"[{i + 1}/{len(compound_items)}] {item.id}: skip (既に完了)")
                continue

            print(f"[{i + 1}/{len(compound_items)}] {item.id}: 実行中...")
            try:
                _pace_voyage_call()
                chunks = retrieve_context(db, query=item.question, strategy="hybrid_rerank")
                answer, had_error = generate_answer(item.question, chunks)  # type: ignore[arg-type]
                if had_error:
                    append_record(
                        OUTPUT_PATH,
                        {"id": item.id, "question": item.question, "status": "error",
                         "error": "generate_answer_stream emitted an error event"},
                    )
                    print("  !! generation error")
                    continue

                judge_result = evaluate_supplement_format(item.question, answer)
                record = {
                    "id": item.id,
                    "question": item.question,
                    "answer": answer,
                    "judge_score": judge_result.get("score", 0),
                    "judge_rationale": judge_result.get("rationale", ""),
                    "human_confirmed": None,
                    "human_note": None,
                    "status": "ok",
                }
                append_record(OUTPUT_PATH, record)
                print(f"  -> judge_score={record['judge_score']}")
            except Exception as e:  # noqa: BLE001 - eval一回性スクリプト。エラーは記録して続行する
                append_record(
                    OUTPUT_PATH,
                    {"id": item.id, "question": item.question, "status": "error", "error": str(e)},
                )
                print(f"  !! error: {e}")
    finally:
        db.close()


def print_summary() -> None:
    records = list(load_latest_by_id(OUTPUT_PATH).values())
    ok_records = [r for r in records if r.get("status") == "ok"]
    error_records = [r for r in records if r.get("status") != "ok"]
    human_confirmed = [r for r in ok_records if r.get("human_confirmed") is True]

    print("\n=== 補足書式検証 結果 ===")
    print(f"収集済み: {len(ok_records)}件 (エラー: {len(error_records)}件)")
    print(f"judge score=1: {sum(1 for r in ok_records if r.get('judge_score') == 1)}件")
    print(f"人手確認済み(分離確認OK): {len(human_confirmed)}件")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# M7 補足書式検証結果(複合質問5件)",
        "",
        f"収集済み {len(ok_records)}件（エラー {len(error_records)}件）。",
        "",
        "| id | judge_score | judge_rationale | human_confirmed | human_note |",
        "|---|---|---|---|---|",
    ]
    for r in ok_records:
        lines.append(
            f"| {r['id']} | {r['judge_score']} | "
            f"{str(r.get('judge_rationale', '')).replace(chr(10), ' ')} | "
            f"{r.get('human_confirmed')} | {r.get('human_note') or ''} |"
        )
    lines.append("")
    lines.append(f"**人手確認後、分離が確認できた件数: {len(human_confirmed)}/{len(ok_records)}**")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"レポートを {REPORT_PATH} に保存しました。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats-only", action="store_true")
    args = parser.parse_args()

    if not args.stats_only:
        process(args)

    print_summary()


if __name__ == "__main__":
    main()
