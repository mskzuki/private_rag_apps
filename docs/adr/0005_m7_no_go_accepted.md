# ADR 0005: T4 grade閾値のNO-GO判定を受け入れる

- Status: Accepted
- Date: 2026-07-16
- 関連: `docs/adr/0001_m7_theta_threshold.md`（THETA決定と、その後のNO-GOへの反転）、`docs/specs/m7_adaptive_routing.md`（rev.3 §7.4 LLM grader昇格判断）

## Context

ADR 0001でTHETA=0.56をGO判定として決定したが（T2時点、holdout 37/39件で計算）、T4で`make eval-routing`を実行したところ、より完全なデータ（129/130件収集）でholdout判定が**NO-GO**に反転した（grounded見逃し0/21、direct誤り4/18で基準≤3を超過）。

正規の手順（calibrationのみでのgrid search）による再キャリブレーションでもTHETA=0.56が引き続き最適と確認され、holdoutへの再適用でも同じNO-GO結果が再現された。

調査の結果、holdoutのdirect誤り4件は、いずれもVoyage rerank APIの一時的な失敗により`grade()`が安全側デフォルト（迷ったらgrounded、スペック§3.1）でgrounded判定になったケースと一致することが判明した。この4件（および他の失敗分）のVoyageスコアを再取得すれば、判定が変わる可能性がある。

## Decision

**再取得は行わず、NO-GO判定をそのまま受け入れる。**（ユーザーの明示的な指示による）

- THETA=0.56は変更しない
- holdoutのNO-GO判定（direct誤り4/18）を、閾値方式の限界を示す正式な evalエビデンスとして扱う
- スペック§7.4の exit criteria（「閾値方式でholdout上の基準を同時に満たせないことが示された場合」）を満たしたと判断し、**グレーゾーン（THETA近傍のスコア帯）のみを対象としたLLM graderの追加を別スペックとして起票する**（本ADRのスコープ外。別途起票する）

## Consequences

- 現行の閾値方式のgradeは、Voyage APIの不安定性の影響を受けやすいという弱点を抱えたまま本番相当の実装として存在し続ける（フォールバック挙動自体は誤判定コストの非対称性原則に沿った安全側設計であり、これ自体は変更しない）
- **T5（rewrite）・T6（SSE追加イベント）・T7（可観測性）はLLM grader検討と並行して進める**（ユーザー承認済み）。これらはgradeの判定方式（閾値かLLM graderか）に依存しない独立した基盤整備であるため。LLM graderスペックの起票・実装は別途のタスクとして扱う
- 将来LLM graderスペックを実装する際、`_rerank()`のVoyage失敗時フォールバック挙動（全チャンクスコアNone→安全側kept扱い）と、grader追加の関係を整理する必要がある（Voyage失敗時にLLM graderも同時に機能しない可能性があるため、フォールバック設計自体の見直しも検討課題に含めるべき）
