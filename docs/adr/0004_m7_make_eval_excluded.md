# ADR 0004: M7の完了条件から`make eval`（既存e2e eval）を除外する

- Status: Accepted
- Date: 2026-07-15
- 関連: `docs/specs/m7_tasklist.md`（T4完了条件、T7全体完了定義）、`docs/adr/0003_m7_t3_eval_baseline_gap.md`

## Context

`docs/specs/m7_tasklist.md`の複数タスク（T3, T4, T7）は、生成品質の非劣化確認として`make eval`（既存e2e eval、36問。OpenAI/Voyage呼び出しを伴う）の実行・合格を完了条件に含めていた。

T3ではVoyageレート制限により2度実行を試みて完走できず、`generation/`/`retrieval/`/`prompts/`への差分ゼロを根拠にしたコードレビュー代替で受入した（ADR 0003）。T4ではgrounded/directプロンプトを変更するため、ADR 0003の但し書き通りコードレビュー代替は使えず、実際の実行が必要だった。試行の結果、22問前後で同じくVoyageレート制限により失敗した。

`make eval-routing`（T2, T4で新設。retrieve→gradeのみでgenerateを実行しない）はVoyage呼び出しのみで、ペーシングを入れれば完走できることが実証されている（T2, T4で実績あり）。一方`make eval`はOpenAI（generate + judge）とVoyage（embed + rerank）の両方を、ペーシング機構を持たない既存ハーネス（`evals/__main__.py`、M3由来）で36問分連続実行する必要があり、繰り返し失敗している。

## Decision

**M7の完了条件から`make eval`（既存e2e eval）を除外する。** ユーザーの明示的な指示による。

- `make eval-routing`は引き続き必須（THETA・grade・rewriteロジックの検証はこちらが担う）
- direct groundedness eval・補足書式検証（LLM-as-judge + 人手裁定）は引き続き必須（generate品質の検証は個別のjudgeベースの手段で行う）
- `make eval`（既存e2e、generate品質の総合スコア）のみを完了条件から外す

## Consequences

- T3, T4, T7の完了条件チェックリストのうち、`make eval`実行を求める項目は「対象外（ADR 0004）」として扱う。`make eval-all`（`make eval` + `make eval-routing`）を要求する項目も、実質`make eval-routing`のみの確認に読み替える
- generate品質（grounded/directプロンプトの生成そのものの品質）の総合的な非劣化は、e2e evalでは検証されない。direct groundedness・補足書式のjudgeベース検証と、手動スモークテストが実質的な品質担保手段になる
- `evals/__main__.py`にペーシング機構を追加すれば`make eval`が完走できる可能性が高いが、これは本ADRのスコープ外（M7外の改善課題として任意で起票可能）
- 今後Voyage/OpenAIアカウント側の制約（無支払い枠）が解消された場合、`make eval`を再度完了条件に含めるかは別途判断する
