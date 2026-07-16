# M7 eval-routing 結果

THETA=0.56。収集済み149件、エラー1件。

## calibration

| 指標 | 実績 |
|---|---|
| grounded見逃し | 2/49 |
| direct誤り | 9/41 |

## holdout（合否判定はこちら）

| 指標 | 実績 | 基準 |
|---|---|---|
| grounded見逃し | 0/21 (id=[]) | <= 1 |
| direct誤り | 4/18 (id=['g014', 'g037', 'a019', 'a028']) | <= 3 |

## rewrite quality（followup、代理指標）

routing.jsonl には relevant docs が無いため厳密なhit@kではなく、経路予測の正解率で代用している。

比較対象20件中、rewrite有りで正解20件、
rewrite無しで正解14件。rewriteにより悪化したid: []

## 判定

**NO-GO**
