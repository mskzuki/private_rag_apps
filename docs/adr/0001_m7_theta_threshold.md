# ADR 0001: M7 adaptive routing の grade 閾値（THETA）を 0.56 に決定

- Status: Accepted
- Date: 2026-07-15
- 関連: `docs/specs/m7_adaptive_routing.md`（rev.3 §2, §4.3 grade, §7.2, §8）、`docs/specs/m7_tasklist.md`（T2）
- 根拠データ: `backend/evals/reports/m7-score-distribution.md`、`backend/evals/reports/m7-calibration-result.json`（コミット `618cfdb`）

## Context

M7（Adaptive Routing）では、質問ごとに「コーパスに根拠がある（grounded）」か「一般知識のみで回答すべき（direct）」かを、既存 retrieval パイプラインの関連度スコア（Voyage rerank-2.5 の `relevance_score`）に対する単一の閾値 THETA で機械的に判定する設計を採用している（スペック §2, §4.3 grade）。この閾値方式が実際に成立するか（スコア分布が閾値を引けるほど分離しているか）、成立するなら閾値をいくつにすべきかは、実装前に eval データで検証することがスペック §8 のリスク対応として求められていた。

## Decision

THETA = **0.56** を採用する。

決定方法（詳細は `m7-score-distribution.md` 参照）:

1. routing eval データセット（130問、`backend/evals/dataset/routing.jsonl`）のうち calibration split（91問中89問。2問はVoyage APIのレート制限で収集不能につき除外）で、THETA候補を0.00〜1.00まで0.01刻みでgrid search
2. 制約「grounded見逃し率 ≤ 0.05」を満たす候補の中から、direct適中数を最大化する値を選択（同点の場合はより低い値を採用。スペック §2「迷ったら低めに引く」原則に従う）
3. 決定したTHETAを、calibrationとは別に確保していたholdout split（39問中37問）に**1回だけ**適用し、最終判定

## 結果（holdout上の検証）

| 指標 | 実績 | 基準 |
|---|---|---|
| grounded見逃し | 1/20件 | ≤ 1件（必達） |
| direct誤り | 3/17件 | ≤ 3件 |

**GO**（T3以降への進行を承認）。ただし両指標とも基準値ちょうどでの合格であり、統計的な余裕はない（サンプルサイズが小さいholdoutでは、1件の結果が変わるだけで判定が反転しうる）。

## Consequences

- T4でTHETAを `core/config.py` にconfig化する際、この値（0.56）を初期値とする
- スコア分布の分離自体は明確（grounded中央値0.775、direct中央値0.421）だが、ambiguous/followupカテゴリを中心にグレーゾーン（概ね0.41〜0.82）が実在するため、`make eval-routing` 運用開始後も閾値近傍の事例を継続的にモニタリングすることを推奨する
- 130問中4問（`g033`, `a016`, `a004`, `a029`）はVoyage AI無支払い枠のレート制限により最終的にスコア取得できず、calibration/holdoutから除外されている。有償プラン切替後の再取得とTHETA再確認は本ADRのスコープ外の任意対応として残されている
- 本キャリブレーションは、現状 `retrieval/searcher.py::_rerank()` がスコアをチャンクに付与しないため、同関数を経由せず本番と同一設定でVoyage rerank APIを直接呼び出す方式で行った。T4で `_rerank()` に `rerank_score` フィールドを追加する際は、ここで使った `relevance_score`（0〜1、降順）と意味論を一致させる必要がある（一致しない場合、本ADRのTHETA=0.56は無意味になる）

---

## 追記（2026-07-16、T4: `make eval-routing`実行によるNO-GO判定と再キャリブレーション）

### 経緯

T4で`_rerank()`に`rerank_score`を実装後（本ADRの意味論と一致することを単体テストで確認済み）、`make eval-routing`（`backend/src/private_rag_apps/evals/routing.py`）を実行し、THETA=0.56をholdoutに適用したところ、**NO-GO**判定となった。

| 指標 | 実績 | 基準 |
|---|---|---|
| grounded見逃し | 0/21件 | ≤ 1件 |
| direct誤り | **4/18件**（該当id: g014, g037, a019, a028） | ≤ 3件（1件超過） |

原因: 決定時点(T2)ではVoyageレート制限により130問中4問（`g033`, `a016`, `a004`, `a029`）が未収集のままcalibration 89/91・holdout 37/39で判定していた。T4の`make eval-routing`実行では130問中129問（1問`c038`のみ収集失敗）とほぼ完全なデータが得られ、以前欠けていたholdout側2件（`a004`, `a029`）が新たに評価対象に加わった。これは本ADR「Consequences」節で「未収集分が収集できていたら判定が変わっていた可能性がある」と明記していたリスクが顕在化したものである。

### 再キャリブレーション（ユーザー承認済みの対応）

holdoutの結果を見て逆算するのではなく、正規の手順（calibrationのみでgrid search）でTHETAを再決定した（`backend/evals/recalibrate_theta.py`、入力: `backend/evals/dataset/routing_eval_results.jsonl`）。

- calibration（90問、`c038`除く）でgrid search（step=0.01、制約: grounded見逃し率≤0.05、目的関数: direct適中最大化、tie-break: より低いtheta）を実施した結果、**THETA=0.56が引き続き一意に最適**（direct_correct=32/41で全候補中最大。全101候補のうち制約を満たす57候補を全数確認し、tie無し）。
- 決定したTHETA=0.56を、あらためてholdout（39問、1回のみ）に適用したところ、**T4実行時と全く同じ結果（grounded見逃し0/21、direct誤り4/18、該当id同一）**が再現され、**NO-GOが確定**した。

**THETAの値は変更しない（0.56のまま。`core/config.py`の変更なし）**。再キャリブレーションの結果、0.56自体がより完全なデータの下でも引き続き最適値であることが確認されたため。

### 重要な追加所見: direct誤り4件はVoyageリランク失敗のアーティファクトである可能性が高い

`routing_eval_results.jsonl`を精査した結果、**129問中22問（17%）でVoyage rerankが失敗し、`rerank_score`が全チャンクでNone（フォールバック: RRF順のまま返す。`retrieval/searcher.py::_rerank()`参照）になっていた**ことが判明した。`grade()`はスコア欠落チャンクを安全側デフォルトでkept扱いにする設計（誤判定コストの非対称性。スペック§3.1「迷ったらgroundedに倒す」）のため、**rerankが失敗した項目は、THETAの値に関わらず必ずgrounded判定になる**。

holdoutのdirect誤り4件（`g014`, `g037`, `a019`, `a028`）はいずれも該当し、**全件がこのrerank失敗パターンに一致していた**（expected_route=directなのにrerankが失敗しgrounded判定になったことでdirect誤りとしてカウントされた）。calibrationのdirect誤り9件のうち5件（`g009`, `g012`, `g033`, `g039`, `a022`）も同様のパターン。

この所見は、**THETA=0.56自体の妥当性を疑う根拠にはならない**（grid searchはこのフォールバックの影響を受けたデータも含めて実施しており、それでも0.56が最適と出ている）が、**holdoutの「direct誤り4/18」という数字自体は、真の閾値判定精度ではなくVoyage APIの一時的な障害を測定している可能性が高い**ことを意味する。この4件（および他の18件の欠落データ）についてVoyage rerankを再試行し、実スコアを取得した上で判定をやり直すことが、より正確な評価につながる可能性が高い。この対応を実施するかはコントローラー判断とし、本ADRでは実施していない（`.superpowers/sdd/task-T4-report.md`に詳細と提案を記録）。

### 結論（本追記時点）

- THETA=0.56を維持（変更なし）
- `make eval-routing`のholdout判定は**NO-GO**（direct誤り4/18が基準を1件超過）
- ただし上記の通り、この超過分はVoyageレート制限によるデータ欠落が主因である可能性が高く、額面通りの「grade精度の問題」ではない可能性がある
- 本件の最終判断（欠落データの再取得を試みるか、NO-GOをそのまま受け入れM8のLLM grader検討[スペック§7.4]に進むか）はコントローラーに委ねる

## 追記（2026-07-17）

ユーザー指示により、holdoutのdirect誤り4件を含む未検証None件をVoyage APIに再取得した結果、**holdout direct誤りが4/18→2/18に減少し、判定はGOに反転した**（THETA=0.56は変更なし）。`g014`/`g037`はVoyage失敗の推定通り誤判定だったが、`a019`/`a028`は実スコアが取れた上で依然direct誤り（正当なグレーゾーン境界値）であることが判明した。詳細は `docs/adr/0006_m7_holdout_partial_reretry_go.md` を参照（ADR 0005はSuperseded）。
