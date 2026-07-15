# M7 T2: rerank score 分布分析と THETA 初期値決定

- 対応タスク: `.superpowers/sdd/task-T2-brief.md`（M7タスクリスト rev.3 T2）
- 参照スペック: `docs/specs/m7_adaptive_routing.md`(rev.3) §7.2, §8
- 実行日: 2026-07-15
- 実行者: T2実装エージェント（worktree `m7-adaptive-routing`）
- 対象コミット: `10beef93285ffb7ddbea8c559a6ef8a88a991805` 時点の `backend/evals/dataset/routing.jsonl`（T1成果物、130件）

## 0. 結論(先出し)

**GO**。holdout split(37/39件。後述の理由で2件除外)上で、選定 THETA=0.56 は
spec §7.2 の合格基準を両方満たした:

| 指標 | holdout実績 | 基準 | 判定 |
|---|---|---|---|
| grounded 見逃し | 1/20件 (f005) | <= 1件 | ✅ (基準ぎりぎり) |
| direct 誤り | 3/17件 (a019, a028, f012) | <= 3件 | ✅ (基準ぎりぎり) |

**両指標とも基準値ちょうどで、余裕はない。** マージンの無さについては §6 で
詳述する。T3 へ進めるが、T4 実装時にこの結果を軽視しないこと。

---

## 1. スコア取得方法(重要。T4実装者への申し送り)

本番の `retrieval/searcher.py::_rerank()` は Voyage のリランク結果でチャンクを
並べ替えるだけで、スコアをチャンクの dict に付与しない(rev.3 §4.3 grade の前提
で明記されている既知の制約。T4で `rerank_score` フィールドを追加予定)。
そのため `backend/evals/analyze_score_distribution.py` は `_rerank()` を経由せず、
以下の方式でスコアを直接取得した:

1. `retrieval.searcher._embed_query(query)` でクエリを埋め込む(本番と同一関数、無変更)
2. `retrieval.searcher._hybrid_search(db, query, emb, candidate_k, rrf_k, fuse_k)` で
   候補チャンクを取得する(本番と同一関数・同一設定値。RRF融合済み、rerank前の
   40件 [`fuse_k=40`])
3. `voyageai.Client(api_key=settings.voyage_api_key, max_retries=settings.voyage_max_retries).rerank(query=query, documents=[c["content"] for c in candidates], model="rerank-2.5", top_k=settings.rerank_top_k)`
   をスクリプトから直接呼び出す(`_rerank()` は使わない)
4. 返り値 `RerankingObject.results[i].relevance_score`(0〜1、降順)を各チャンクの
   スコアとして読み取る。`results[i].index` が `candidates` 配列内のインデックス

使用した設定値(`core.config.settings`、本番と同一):

| 設定 | 値 |
|---|---|
| `candidate_k`(ベクトル/全文それぞれの候補数) | 50 |
| `rrf_k`(RRF減衰パラメータ) | 60 |
| `fuse_k`(RRF融合後・rerank対象件数) | 40 |
| `rerank_top_k`(rerank後に残す件数) | 8 |
| `embed_model` | voyage-4 |
| rerank model | rerank-2.5(ハードコード。`_rerank()`と同じ) |

**本分析での「top_score」の定義:** 各クエリについて、rerank後に返る最大 8件の
`relevance_score` のうち最大値。grade のロジック(`kept = [c for c in retrieved
if c.rerank_score >= THETA]`, `route = "direct" if len(kept)==0 else
"grounded"`)は、`kept` が1件以上あるかどうかだけで route を決めるため、
**`top_score >= THETA` であることと `kept>=1` であることは同値**。そのため
本分析・キャリブレーションは top_score のみで route を機械的に再現できる
(`calibrate_threshold.py::predict_route()` 参照)。

**T4実装者へ:** `_rerank()` に `rerank_score` を追加する際は、上記の
`relevance_score` をそのままそのフィールドに格納すること。フィールド名・
値の意味論(Voyage rerank-2.5 の `relevance_score`、0〜1、降順)を本分析と
一致させないと、ここでキャリブレーションした THETA=0.56 が無意味になる。

---

## 2. レート制限対策と収集の実際の経過

Voyage AI 無支払い枠(3RPM/10K TPM)の制約により、`ingestion/indexer.py` の
既存ペーシング実装(`_pace_embed_call`)を踏襲し、embed・rerank 双方の Voyage
呼び出し前に `settings.ingest_embed_min_interval_sec`(21秒)以上の間隔を空けた
(`analyze_score_distribution.py::_pace_voyage_call`。アプリコードは変更禁止のため
同等ロジックをスクリプト内に複製)。

- 130件 × 最大2回(embed + rerank)の呼び出し。1回目の完走に約80分。
- 429エラー(`You have not yet added your payment method...` メッセージ)が
  散発的に発生。連続エラーではなかったため(`--max-consecutive-errors` 既定3の
  閾値に達しなかったため)実行は自動継続し、最後まで完走した。
- 完走後、`status != "ok"` のid(初回12件)のみを再実行(スクリプトの再開機構、
  既に`ok`のidは自動skip)。約9分で12件中8件が成功。
- 残り4件(`g033`, `a004`, `a016`, `a029`)は再実行後も429が解消せず、
  最終的に **未解決のまま**とした(コーディネーターの判断により、無理に粘らず
  126/130件で計算を進めることとした)。

**最終収集結果:** 130件中 **126件成功、4件失敗**(`routing_scores.jsonl` 142行
[延べ試行数] = 成功126 + エラー16[延べ]。同一idに複数回の試行記録があるため
「延べ」)。

失敗4件の内訳(既知の制約として記録。恣意的な除外ではなく、Voyage側の
レート制限が最後まで解消しなかったことによる機械的な結果):

| id | category | split | expected_route | query |
|---|---|---|---|---|
| g033 | general | calibration | direct | RAG評価のためのRAGASフレームワークとは何ですか? |
| a016 | ambiguous | calibration | direct | HNSWのefConstructionはどう決めるべきか? |
| a004 | ambiguous | holdout | grounded | 更新のあるデータセットに向いた、事前学習不要のベクトルインデックス手法は? |
| a029 | ambiguous | holdout | direct | ベクトルインデックスの再構築(reindex)は一般的にどのくらいの頻度で行うべきか? |

calibration側2件・holdout側2件で均等に抜けており、特定カテゴリ・特定経路に
偏った欠落ではない。calibration: 91件→89件(97.8%)、holdout: 39件→37件
(94.9%)。この程度の欠落が結果を大きく歪めるとは考えにくいが、T4以降で
余裕があれば有償プランへの切替後にこの4件を再取得し、THETAを再確認すること
を推奨する(本タスクのスコープ外)。

再現・生データ:
- `backend/evals/dataset/routing_scores.jsonl`(id毎の逐次追記ログ。再実行分も
  含めすべて保持。id毎に最新レコードが正とする設計 [`status=="ok"`優先ではなく
  「最後に書かれたレコード」優先。`calibrate_threshold.py::load_scores()` /
  `analyze_score_distribution.py::load_records()` 参照])
- 実行: `cd backend && uv run python evals/analyze_score_distribution.py`
  (中断・再実行してもresumeする。`--stats-only` で収集済みデータの統計のみ
  再表示できる)

---

## 3. スコア分布(全体像)

`analyze_score_distribution.py --stats-only` の出力(126件、失敗4件除く)。

### 3.1 expected_route 別 top_score 分布

**grounded (n=70): min=0.4082, max=0.9219, mean=0.7633, median=0.7754**

```
[0.40,0.45) ## 1
[0.45,0.50) ## 1
[0.50,0.55)  0
[0.55,0.60) ###### 3
[0.60,0.65) ###### 3
[0.65,0.70) ########## 5
[0.70,0.75) ##################### 10
[0.75,0.80) ################################### 17
[0.80,0.85) ######################################## 19
[0.85,0.90) ############## 7
[0.90,0.95) ######## 4
```

**direct (n=56): min=0.3008, max=0.8164, mean=0.4477, median=0.4209**

```
[0.30,0.35) ################################ 13
[0.35,0.40) ###################### 9
[0.40,0.45) ######################################## 16
[0.45,0.50) ####### 3
[0.50,0.55) ########## 4
[0.55,0.60) ############ 5
[0.60,0.65) ####### 3
[0.65,0.70) ## 1
[0.70,0.75) ## 1
[0.75,0.80)  0
[0.80,0.85) ## 1
```

### 3.2 category別 top_score 分布

| category | n | min | max | mean | median |
|---|---|---|---|---|---|
| corpus | 40 | 0.4082 | 0.9219 | 0.7557 | 0.7754 |
| followup | 20 | 0.3711 | 0.9062 | 0.7206 | 0.7656 |
| ambiguous | 27 | 0.3066 | 0.8945 | 0.6681 | 0.7070 |
| general | 39 | 0.3008 | 0.5859 | 0.4056 | 0.4043 |

### 3.3 分離度の評価

- grounded の中央値(0.7754) と direct の中央値(0.4209) は明確に分離しており、
  大半のクエリでは top_score だけで経路が容易に見分けられる。
- ただし **分布の両端は重なっている**: direct側最大 0.8164 (a019) >= grounded側
  最小 0.4082 (c011)。「スコア分布が想定より平坦で閾値が引けない」(spec §8の
  リスク)というほどの完全な重なりではないが、**閾値方式だけでは分離しきれない
  グレーゾーン(概ね0.41〜0.82)が一定数存在する**ことは明確。
- ambiguous カテゴリ(狙い通り)がこのグレーゾーンの主な発生源: mean 0.6681は
  grounded/directの中間に位置し、他カテゴリより分散が大きい。
- general(直接期待, 純粋な無関係質問)は 0.30〜0.59 に収まり、corpusは
  ほぼ0.55以上に収まる。**もし ambiguous/followup が存在しなければ、単純な閾値
  (例: 0.6付近)でほぼ完全に分離できていた。** 今回GOと判定できたのも、
  ambiguous/followupという意図的に作られた境界ケースを含めてなお基準を
  満たせたためであり、閾値方式の頑健性を示す結果と言える。

---

## 4. calibration: grid search による THETA 決定

`calibrate_threshold.py` を実行(`--step 0.01`、THETA候補は0.00〜1.00を
0.01刻みで101通り)。calibration split(89件 = 91件中2件が収集失敗のため除外)
のみを使用。

**制約:** grounded 見逃し率 <= 0.05
**目的関数:** 制約を満たす候補の中で direct 適中数を最大化
**tie-break:** 目的関数が同値の場合、より低い theta を採用
(spec §2「誤判定コストの非対称性により、迷ったら低めに引く」に従う)

**結果: THETA = 0.56**

| 指標 | calibration実績 |
|---|---|
| grounded 見逃し | 2/50件(rate=4.00%。制約<=5%を満たす) |
| grounded見逃しid | `c011`(「Langfuseがダウンしているとアプリは動かなくなる?」top_score=0.4082), `c012`(「EvalでCIをfailさせる条件は?」top_score=0.4531) |
| direct 適中 | 32/39件 |

`c011`/`c012` はいずれも「はい/いいえで答えられる仕様確認」型の質問で、
コーパス中の該当箇所への語彙的な重なりが薄く rerank score が低く出た
(定性的な考察。§6でも言及)。calibration上の見逃し率4.00%は5%の制約に
対してマージンが薄い(あと1件見逃すと5%制約に抵触する可能性がある、
50件中3件で6.00%になるため)。

---

## 5. holdout: GO/NO-GO 判定(1回のみ適用)

決定した THETA=0.56 を holdout split(37件 = 39件中2件が収集失敗のため除外)
に **一度だけ**適用した。spec §7.2 の件数基準で判定する。

| 指標 | 定義 | holdout実績 | 基準 | 判定 |
|---|---|---|---|---|
| grounded 見逃し | expected=grounded が direct になった件数 | **1/20件** | <= 1件(必達) | ✅ |
| direct 誤り | expected=direct が grounded になった件数 | **3/17件** | <= 3件 | ✅ |

### grounded 見逃し(1件): `f005`

- followup。history: 「埋め込みモデルと次元数を教えてください」→ query:
  「それのコストはどれくらいですか?」
- `expected_search_query`(T1で人手作成): 「Voyageの埋め込みAPIのコスト(無料枠)」
- top_score = 0.5547、THETA(0.56)との差は **わずか0.0053**。閾値のごく僅かな
  下で外れた、典型的な境界ケース。

### direct 誤り(3件): `a019`, `a028`, `f012`

| id | query | top_score | 備考(routing-README根拠) |
|---|---|---|---|
| a019 | リランクのtop_kをいくつに設定するのが適切かの一般的な判断基準は? | 0.7070 | 採用値(top_k=8)はdb_design.mdにあるが、一般的な判断基準の記述なし |
| a028 | 会話履歴を要約してプロンプトに含める際の一般的なベストプラクティスは? | 0.5742 | トークン予算内で切り詰める旨の記述はあるが、要約のベストプラクティス論は記述なし |
| f012 | (followup)それは何ターン分の履歴を見ますか? | 0.6406 | architecture.mdは「トークン予算内で切り詰め」と述べるのみでターン数Nの記述なし |

3件とも ambiguous/followup の「採用値・関連する周辺記述はコーパスにあるが、
質問が求める一般論・具体的な数値そのものではない」という設計上最も難しい
境界ケース(routing-README.md §5.2/§6.2の意図通り)。rerank
はクエリと語彙的・意味的に近い chunk(「top_k」「履歴」「トークン予算」等の
関連語を含む chunk)を高スコアにする性質上、これらを見誤りやすい。
**この種の誤りは spec設計原則(§3.1)上「軽微」に分類される**(direct でよい
質問を grounded に流す誤り)。

---

## 6. マージンについての注記(重要)

holdout の両指標は **基準値ちょうど**で合格した(grounded見逃し=1=上限、
direct誤り=3=上限)。以下の理由からこれを額面通りの「余裕のある合格」とは
見なさない:

1. **サンプルサイズが小さい**(holdout 37件)。1件の結果が変わるだけで
   判定が反転しうる(例: `f005` がもし0.006高いスコアだったら見逃しゼロに、
   逆に別の1件がもし僅かに閾値を跨いでいたら不合格になっていた)。
2. **未収集の4件**(`a004`, `a029` はholdout)が仮に収集できていたら、
   holdout件数・結果が変わっていた可能性がある(特に`a004`はgrounded期待、
   `a029`はdirect期待で、どちらの方向にも判定を動かしうる)。
3. calibration側の grounded見逃し率(4.00%)も5%制約に対して薄いマージン。

**結論としてはGOで問題ないが、T4実装時にTHETA=0.56を config
の初期値としてそのまま採用しつつ、`make eval-routing`
運用開始後は継続的に閾値近傍(概ね0.5〜0.65)のグレーゾーン事例を
モニタリングすることを強く推奨する。** spec §7.4のLLM grader昇格判断
(exit criteria)は「holdout上で基準を同時に満たせないことが示された場合」
だが、今回のように基準をぎりぎり満たすケースでは、実運用でのドリフト
(コーパス更新・埋め込みモデル変更等)により容易に基準割れしうる点に
留意が必要。

---

## 7. GO/NO-GO 判定

**GO。T3へ進めることを推奨する。**

- calibration: grounded見逃し率 4.00% <= 5%制約を満たしてTHETA=0.56を決定
- holdout: grounded見逃し 1/20(<=1)、direct誤り 3/17(<=3)を両方満たす
- 閾値方式の成立可否(spec §8のリスク「rerank scoreの分布が想定より平坦で
  閾値が引けない」)について: 完全な平坦ではなく、grounded/directの中央値は
  明確に分離している。ただし§3.3で述べた通りグレーゾーンは実在し、
  マージンも薄い(§6)。「閾値方式が原理的に成立する」ことは示せたが、
  「余裕を持って成立する」とまでは言えない。

この判定はholdoutに1回だけ適用した結果であり、spec §7.2/brief作業項目5の
規定通り、**holdoutを見てTHETAを再調整することはしていない**。

---

## 8. 既知の制約まとめ

- Voyage AI無支払い枠のレート制限により、130件中4件(`g033`, `a016`,
  `a004`, `a029`)のスコアを最終的に取得できなかった(§2)。GO/NO-GO判定は
  126件で計算した(calibration 89/91、holdout 37/39)。
- ambiguous/followupカテゴリのexpected_route判定はT1実行者による人手判定
  (コーパス4ファイルの直読・grep)に基づく(`routing-README.md`
  §8既知の制約に記載済み)。今回の分析で `c011`/`c012`/`f005`等、スコアが
  境界に近い項目が見つかったが、**T2のスコープはスクリプトとレポート作成の
  みであり、routing.jsonl・routing-README.mdのラベル自体は変更していない**
  (routing-README.md §8の「疑わしい項目が見つかった場合は本READMEの該当行を
  更新すること」への対応はT1データセットの改訂であり、本タスクの成果物には
  含めない。必要であれば別途合意の上で対応する)。

---

## 9. 再現方法

```bash
# 1. スコア収集(resumable。中断しても再実行で再開)
cd backend && uv run python evals/analyze_score_distribution.py

# 2. 既存データの分布統計のみ再表示(APIを叩かない)
cd backend && uv run python evals/analyze_score_distribution.py --stats-only

# 3. calibration split でgrid search → holdoutに1回適用 → GO/NO-GO判定
cd backend && uv run python evals/calibrate_threshold.py
```

生成物:
- `backend/evals/dataset/routing_scores.jsonl`(スコア生データ、id毎逐次追記)
- `backend/evals/reports/m7-calibration-result.json`(THETA決定・判定の機械可読記録)
- 本ファイル(`backend/evals/reports/m7-score-distribution.md`)
