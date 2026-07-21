# M7 THETA再キャリブレーション結果

入力: `evals/dataset/routing_eval_results.jsonl`(T4の`make eval-routing`収集分、149件中148件[ok, __raw除く]+エラー1件[c038])

## calibration(grid search, step=0.01)

選定THETA: **0.56**

| 指標 | 実績 |
|---|---|
| grounded見逃し | 2/49(rate=0.0408, 制約<=0.05) |
| direct適中 | 32/41 |

## holdout(1回のみ適用)

| 指標 | 実績 | 基準 |
|---|---|---|
| grounded見逃し | 0/21(id=[]) | <=1 |
| direct誤り | 4/18(id=['g014', 'g037', 'a019', 'a028']) | <=3 |

## 判定

**NO-GO**

## 重要な注記: direct誤り4件はVoyageリランク失敗のアーティファクトの可能性

`routing_eval_results.jsonl`を精査した結果、129件中22件でVoyage rerankが失敗し
(`rerank_score`が全チャンクNone。`_rerank()`のフォールバック)、`grade()`の安全側
デフォルト(スコア欠落チャンクはkept扱い。スペック§3.1)によりTHETAの値に関わらず
必ずgrounded判定になっていた。holdoutのdirect誤り4件(g014, g037, a019, a028)は
**全件がこのパターンに一致**しており、真の閾値判定精度ではなくVoyage APIの
一時的な障害を測定している可能性が高い。
