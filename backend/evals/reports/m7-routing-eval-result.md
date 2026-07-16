# M7 eval-routing 結果

THETA=0.56。収集済み149件、エラー1件。

## calibration

| 指標 | 実績 |
|---|---|
| grounded見逃し | 2/49 |
| direct誤り | 11/41 |

## holdout（合否判定はこちら）

| 指標 | 実績 | 基準 |
|---|---|---|
| grounded見逃し | 0/21 (id=[]) | <= 1 |
| direct誤り | 2/18 (id=['a019', 'a028']) | <= 3 |

## rewrite quality（followup、代理指標）

routing.jsonl には relevant docs が無いため厳密なhit@kではなく、経路予測の正解率で代用している。

比較対象20件中、rewrite有りで正解18件、
rewrite無しで正解14件。rewriteにより悪化したid: ['f004', 'f017']

## followup direct期待ケース（T5完了条件）

rewrite後もdirect経路になるべきfollowup項目: 2/4件が正解
（誤りid: ['f004', 'f017']）

## rewriteレイテンシ（T5完了条件）

実LLM呼び出し20件（history空・キャッシュヒット分は対象外）:

| 指標 | 実績 | 目安 |
|---|---|---|
| p50 | 1004ms | - |
| p95 | 1308ms | <= 800ms（超過時はT7へN削減検討事項として引き継ぐ） |

## 判定

**GO**
