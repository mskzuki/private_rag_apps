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
