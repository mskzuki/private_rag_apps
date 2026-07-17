# ADR 0006: holdout direct誤りの部分再取得によりGO判定に復帰する

- Status: Accepted
- Date: 2026-07-17
- 関連: `docs/adr/0001_m7_theta_threshold.md`（THETA決定と、その後のNO-GOへの反転）、`docs/adr/0005_m7_no_go_accepted.md`（本ADRにより Superseded）、`docs/specs/m7_adaptive_routing.md`（§7.2, §7.4）

## Context

ADR 0005は「holdoutのdirect誤り4件（`g014`, `g037`, `a019`, `a028`）はいずれもVoyage rerank APIの一時的な失敗が原因と推定されるが、再取得は行わずNO-GO判定をそのまま受け入れる」と決定していた（ユーザーの明示的な指示による）。

その後の会話で、ユーザーはこの方針を変更し、失敗した4件および他のholdout未検証None件（`a004`, `a010`, `c008`）をVoyage APIに対して21秒間隔（無支払い枠のレート制限3RPMを守るペーシング）で再実行するよう指示した。

## Decision

以下の結果を正式なholdout実績として採用する（`backend/evals/dataset/routing_eval_results.jsonl`、`backend/evals/reports/m7-routing-eval-result.json/md`、コミット `8171d97`）。

| id | 旧top_score | 新top_score | 旧predicted | 新predicted | expected |
|---|---|---|---|---|---|
| `g014` | null（Voyage失敗） | 0.4277 | grounded（誤） | direct（**正解**） | direct |
| `g037` | null（Voyage失敗） | 0.5156 | grounded（誤） | direct（**正解**） | direct |
| `a019` | null（Voyage失敗） | 0.7070 | grounded（誤） | grounded（誤のまま） | direct |
| `a028` | null（Voyage失敗、3回連続） | 0.5742（4回目で成功） | grounded（誤） | grounded（誤のまま） | direct |
| `a004` | null | 0.7109 | grounded（safe-default経由で偶然正解） | grounded（実スコアで正解確定） | grounded |
| `a010` | null | 0.6406 | grounded（同上） | grounded（実スコアで正解確定） | grounded |
| `c008` | null | 0.8203 | grounded（同上） | grounded（実スコアで正解確定） | grounded |

再集計結果:

| 指標 | 旧実績（ADR 0005時点） | 新実績 | 基準 | 判定 |
|---|---|---|---|---|
| grounded見逃し | 0/21 | 0/21（変化なし） | ≤ 1件 | 達成 |
| direct誤り | 4/18（`g014`,`g037`,`a019`,`a028`） | **2/18**（`a019`,`a028`のみ） | ≤ 3件 | 達成 |

**判定: NO-GO → GO。THETA=0.56は変更しない。**

### 重要な訂正: ADR 0005の原因推定は部分的にのみ正しかった

ADR 0005は「4件全てVoyage失敗が原因」と推定していたが、実際に再取得した結果:

- `g014`, `g037`: 推定通り、真のスコアは閾値を下回っており（0.4277, 0.5156）、Voyage失敗による誤判定だったことが裏付けられた
- `a019`, `a028`: 真のスコアが取得でき（0.7070, 0.5742）、いずれもTHETA=0.56付近〜やや上の**正当なグレーゾーン境界値**だった。Voyage失敗による誤判定ではなく、閾値方式そのものの精度限界による誤判定である

`a028`は4回目の試行でようやく成功した（3回連続失敗）。候補チャンク数・文書長に異常はなく、無支払い枠のレート制限（3RPM）ぎりぎりのペーシング（21秒間隔）における確率的な衝突が原因と考えられる（データ固有の問題ではない）。

### `f014`（followup）は今回の対象外のまま

`f014`もholdoutの未検証None件だったため再実行を試みたが、`.env`の`LLM_MODEL`が存在しないモデル名（`gpt-5.4-nano`、404エラー）に誤設定されているため`condense()`のrewriteが失敗し、書き換えなしクエリでの代替評価という信頼できない結果になった。ユーザー判断によりこの結果は破棄し、`f014`は未検証状態（`top_score=null`、safe-default経由でgrounded、expected=groundedと一致）に復元した。この状態は今回のGO判定に影響しない。`LLM_MODEL`誤設定自体は既知の別問題（メモリ`project_llm_model_misconfigured.md`参照）であり本ADRのスコープ外。

## Consequences

- **スペック§7.4のLLM grader昇格 exit criteria はもはや満たされていない**。exit criteriaは「閾値方式でholdout上の基準を同時に満たせないことが示された場合」だが、GO達成によりこの条件は成立しなくなった。ADR 0005が承認した「グレーゾーン限定LLM graderを別スペックとして起票する」という方針は、**必須の対応ではなく任意の将来改善検討**に格下げする。ただし`a019`(0.707)/`a028`(0.574)という具体的なグレーゾーン誤判定事例が実在することは変わらないため、検討の価値自体は残る（`docs/specs/m7_adaptive_routing.md` §7.2の実績注記を参照）
- ADR 0005は本ADRにより Superseded とする（Context/Decision/Consequencesの本文は、その時点の正しい記録として書き換えない。ADR 0005自身の「Voyage失敗が原因」という推定は`g014`/`g037`については正しかったが`a019`/`a028`については誤りだった、という事実は本ADRで訂正済み）
- **副次的に発見した回帰**: 今回の再取得作業中、`evals/routing.py`の`retrieve_and_grade()`が呼ぶ`grade()`に対し、T6で追加された`get_stream_writer()`（グラフ実行中でないと`RuntimeError`になる）が衝突することが判明した。`make eval-routing`は既に収集済み（`status: ok`）のidをスキップするため通常は気づきにくいが、**新規データセット項目の追加や未収集idの再実行では必ず失敗する状態**になっている。今回は診断スクリプト側で`get_stream_writer`を一時的にno-opへpatchして回避したが、`evals/routing.py`本体は未修正のまま。THETA再キャリブレーション等で本ツールを再度使う際は要修正（別タスクとして追跡）
