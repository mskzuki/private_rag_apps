# M7 タスクリスト: Adaptive Routing (rev.3)

- Spec: `M7-adaptive-routing-spec.md` (rev.3)
- Status: Not Started
- 実行順序: T0 →（R-A 判定）→ T1 → T2 →（GO/NO-GO）→ T3 → T4 → T5 → T6 → T7
- 規約: 各タスクは「完了条件をすべて満たす」まで次に進まない。スコープ外の変更を行わない。判断に迷う点はタスク内の「実装ノート」の範囲でのみ裁量を認め、それ以外はスペックに差し戻す

---

## T0: 前提確認（ローカル LLM 環境の検証を含む）

**目的:** M7 が依存する実装・インフラの前提を着手前に確認する。**特に reranker の有無は grade 設計の成否を分ける最優先項目（スペック §9 R-A）。**

**作業項目:**
1. **【最優先】reranker の確認:** 現在の retrieval パイプラインが返すスコアが (a) cross-encoder reranker による絶対スコアか (b) RRF スコアのみか を特定する
   - **RRF スコアのみの場合 → R-A 判定を発動**（下記「R-A 判定」参照）。T1 以降に進まない
2. **Ollama の structured output 検証:** OpenAI 互換エンドポイント経由で `response_format: {"type": "json_schema"}` が使用中モデルで動作するか、簡単な JSON 出力で確認。不可なら Ollama ネイティブ API (`/api/chat` の `format`) で代替可能かを確認し、結果を記録（スペック §4.3 / R-C）
3. **TTFT ベースライン測定:** 現行の generate（context あり / なし）の TTFT と TPS を計測して記録。rewrite 導入後のレイテンシ相対評価の基準にする（スペック §5.1 / R-D）
4. **M2:** `/chat` streaming が既存イベント型で動作していることを確認
5. **M3:** 履歴のロード・保存と generate への履歴注入の実装箇所を特定し、パスを記録（T3 の移設対象）
6. **M4:** `make eval` が現在通過することを確認し、ベースラインスコアを記録

**R-A 判定（reranker 不在の場合）:**
- M7 を保留し、以下から選択して起票する:
  - (a) ローカル cross-encoder reranker の導入を別マイルストーンとして先行実装
  - (b) grade を LLM grader 方式に変更（スペック §8.5。ただし単一モデルでの prefill コスト増を許容できる場合のみ）
- **どちらを選ぶかの判断材料（現行スコアの分布・レイテンシ許容度）を添えて、この時点で一度立ち止まる**

**完了条件:**
- [ ] reranker の有無が確定し、RRF のみの場合は R-A 判定の結論が記録されている（GO の場合のみ T1 へ）
- [ ] Ollama の structured output 手段が確定している（json_schema or ネイティブ format）
- [ ] TTFT / TPS ベースラインが記録されている
- [ ] M2/M3/M4 の確認結果と該当コードパスが記録されている

---

## T1: routing eval データセット + コーパス固有語彙リスト作成

**目的:** 閾値キャリブレーションと決定的 eval チェックの基盤を作る。実装より先に完了させる。

**成果物:**
- `eval/datasets/routing.jsonl`（スペック §8.1 のスキーマ。`split` 込み）
- `eval/datasets/routing-README.md`（カテゴリ定義・ラベリング基準・followup 作成手順・分割 seed の記録）
- `eval/datasets/corpus_vocabulary.txt`（スペック §8.2）
- 既存 e2e eval セットへの複合質問 5 件の追加

**作業項目:**
1. スキーマ実装（スペック §8.1）。`split: "calibration" | "holdout"` を全行に付与
2. corpus 40 件 / general 40 件 / ambiguous 30 件 / followup 20 件を作成（rev.2 T1 の規約を踏襲）
   - corpus: 根拠ドキュメントの実在を 1 件ずつ確認しパスを README に記録
   - general: コーパスに関連記述がないことを検索で確認。ドメイン分散
   - ambiguous: 手作業。expected_route を記述の実在で機械的決定、根拠を全件記録
   - followup: history の assistant 応答は実アプリ生成の実物を使用。`expected_search_query` を記録。direct 期待を 3–5 件含める
3. **calibration / holdout 分割:** カテゴリ内層化 70/30。乱数 seed を README に記録
4. **`corpus_vocabulary.txt` 作成（スペック §8.2）:** コーパスにのみ現れる固有語彙を 30–50 語キュレーション。**一般技術用語（RRF, HNSW, pgvector 等）を除外**し、「コーパスを読んでいなければ書けない語」のみに絞る。除外・採用の判断基準を README に記録
5. 複合質問 5 件（一部 corpus・一部一般論）を作成し、既存 e2e eval セットに追加

**完了条件:**
- [ ] 全件が JSON Lines としてパース可能、必須フィールド欠損なし
- [ ] grounded 期待の全件について根拠ドキュメントのパスが記録されている
- [ ] direct 期待の全件について「コーパスに記述なし」の確認が済んでいる
- [ ] 件数: corpus ≥ 40, general ≥ 40, ambiguous ≥ 30, followup ≥ 20、各カテゴリ holdout 30% ± 1 件
- [ ] `corpus_vocabulary.txt` が 30–50 語で、一般語を含まないことをレビュー済み
- [ ] e2e セットに複合質問 5 件が追加されている

**スコープ外:** eval 実行スクリプトの実装（T2 / T4）。アプリケーションコードへの変更一切。

**実装ノート:** ambiguous の作成が最も時間を要する（30 件で 3〜4 時間目安）。`corpus_vocabulary.txt` の語彙選定は direct 捏造チェックの精度を直接左右するので、迷ったら**入れない**（false positive を避ける）方向に倒す。

---

## T2: 関連度スコア分布分析と THETA 初期値決定

**目的:** 閾値方式の成立可否を実装前に検証する（スペック §9 R-F）。

**前提:** T0 で reranker の存在が確認済みであること（RRF のみなら本タスクは実行しない）。

**成果物:**
- `eval/scripts/analyze_score_distribution.py`
- `eval/scripts/calibrate_threshold.py`
- `eval/reports/m7-score-distribution.md`

**作業項目:**
1. T1 の全クエリ（followup は `expected_search_query` を使用）を既存 retrieval に流し、スコアを全件記録
2. カテゴリ別スコア分布を可視化（grounded 期待 vs direct 期待の top-1 スコア分布の分離度）
3. grid search（**calibration split のみ**）: 「grounded 見逃し率 ≤ 0.05」制約、direct 適中最大化で THETA を決定
4. **決定した THETA を holdout に一度だけ適用**し、スペック §8.3 の件数基準（grounded 見逃し ≤ 1 件、direct 誤り ≤ 3 件）で評価。結果を記録
5. holdout を見て THETA を再調整しない（する場合は calibration に戻り、holdout 再使用は 1 回までとして明記）

**完了条件:**
- [ ] スコア分布レポートが作成され、分離度が確認できる
- [ ] THETA 初期値が calibration のみで決定され、決定過程が記録されている
- [ ] **GO/NO-GO 判定（holdout 上）:** grounded 見逃し ≤ 1 件 かつ direct 誤り ≤ 3 件 → GO（T3 へ）。満たさない → NO-GO（スペック §8.5 に従い LLM grader スペック起票に切り替え）

**スコープ外:** アプリケーションコードへの変更一切。THETA の config 化（T4）。

**実装ノート:** retrieval の呼び出しは既存サービス層の関数を import する。API 経由にしない。

---

## T3: LangGraph 最小導入（pass-through + SSE 構造検証）

**目的:** ストリーミング接合という最大のリスクを、機能追加ゼロで単独検証する。完了時点で外形的挙動は現行と同等であること。

**成果物:**
- `pyproject.toml`: `langgraph` 追加（**`langchain` / `langchain-openai` を追加しないこと**）
- `app/graph/state.py`: `GraphState` TypedDict（スペック §4.2）
- `app/graph/builder.py`: retrieve → generate の 2 ノードグラフ
- `app/graph/nodes/retrieve.py`, `app/graph/nodes/generate.py`
- `/chat` ハンドラのグラフ経由への切り替え
- stub モデルによる SSE 構造検証テスト

**作業項目:**
1. `GraphState` 定義。**Pydantic モデル・コネクション類を State に含めない**。`json.dumps(state)` が通るシリアライズ可能性テストを追加
2. retrieve ノード: 既存 retrieval サービスの薄いラッパー。`search_query = user_query`（rewrite は T5）
3. generate ノード: 既存の生成ロジックを移設。**既存 OpenAI 互換クライアント・prefix cache を意識したプロンプト順序・Langfuse 計装・履歴注入（T0 で特定したパス）をそのまま維持**。`get_stream_writer()` で `stream_mode="custom"` に流す。**この時点ではデリミタ処理を入れない**（T4 で追加）
4. FastAPI ハンドラ: `graph.astream(stream_mode="custom")` を消費し既存 SSE フォーマットに変換。履歴ロード・応答永続化はハンドラ層に残す
5. **構造検証（自動・決定的）:** stub クライアント（固定文字列）で SSE イベント型の系列・スキーマ・順序を現行キャプチャと比較。**実 LLM での diff 比較は行わない**
6. **実機検証（手動）:** 実モデルで TTFT・トークン順序・citation・done を確認。**T0 の TTFT ベースラインと比較し、グラフ化によるオーバーヘッドがないことを確認**
7. 未知イベント型を 1 つ流しフロントが壊れないことを確認。壊れる場合の default-ignore 追加は本タスクスコープに含める

**完了条件:**
- [ ] 既存の全テストが通過（リグレッションゼロ）
- [ ] `make eval` が T0 ベースラインと同等スコア
- [ ] stub 構造検証テストが通過し CI に組み込まれている
- [ ] 実機の TTFT がベースライン非劣化（グラフ化オーバーヘッドの確認）
- [ ] 未知イベント型に対するフロントの無視動作を確認済み
- [ ] langchain 系パッケージが依存ツリーに入っていないこと（`pip list` で確認）

**スコープ外:** grade / rewrite / 経路分岐 / 新規 SSE イベント / デリミタ処理。プロンプト変更一切。

---

## T4: grade ノードと 2 経路分岐 + 補足デリミタ処理

**目的:** M7 の中核。THETA による grade、2 経路プロンプト、補足デリミタの検出処理。

**成果物:**
- `app/graph/nodes/grade.py`（LLM 不使用の純関数）
- `app/config.py`: `ROUTING_THETA`（T2 の決定値）
- プロンプト 2 種: `grounded`（**デリミタ書式ルール込み**）/ `direct`（コーパス言及禁止ルール込み）
- generate へのデリミタ検出・`supplement_start` 発火処理（スペック §5.4）
- `eval/scripts/eval_routing.py` + `make eval-routing`（`--cached-rewrite` 付き。T5 まで rewrite パススルー）
- `eval/scripts/eval_direct.py` + `make eval-direct`（corpus_vocabulary による決定的捏造チェック）
- 複合質問 5 件のデリミタ検証の `make eval` 組み込み
- AGENTS.md 更新

**作業項目:**
1. grade ノード実装（スペック §5.3）。conditional edge で 2 経路分岐、generate は `state["route"]` でプロンプト切り替え
2. grounded プロンプト: 既存 RAG プロンプトにデリミタ書式ルールを追加（スペック §5.4）。**カバレッジ判定をプロンプトに委ねる設計であることをコードコメントに明記**（後任が grade に判定ロジックを足す逸脱を防ぐ）
3. direct プロンプト: スペック §5.4 の通り
4. **デリミタ処理:** streaming バッファで `---SUPPLEMENT---` の行を検出し `supplement_start` を発火、デリミタ行自体は送出しない。**改行を跨いでデリミタが分割されるケース、デリミタ 2 回出現のケースを処理**（スペック §5.4）
5. `make eval-routing`: rewrite（T5 までパススルー）→ retrieve → grade を実行し、calibration / holdout 別に §8.3 の route 指標を出力。**generate は実行しない**
6. `make eval-direct`: direct 経路を実際に生成し、回答本文に `corpus_vocabulary.txt` の語が出現するか文字列マッチ。**検出時は人手裁定手順（一般語混入なら語彙リスト修正）を README に規定**
7. 複合質問 5 件: `supplement_emitted` とデリミタ回数を検証（決定的）+ 補足内容の分離を人手確認

**完了条件:**
- [ ] `make eval-routing`（holdout）: grounded 見逃し ≤ 1 件、direct 誤り ≤ 3 件
- [ ] `make eval-direct`: corpus_vocabulary 検出 0 件（人手裁定後の真の捏造 0）
- [ ] 複合質問: デリミタ 5/5 で正しく発火（1 回のみ）、補足内容の分離 5/5
- [ ] デリミタのバッファ処理テスト（分割・2 回出現）が stub で通過
- [ ] 2 経路 + 補足発生の手動スモークテスト（grounded 2 / direct 2 / 複合 1、SSE 経由）
- [ ] `make eval`（e2e）が T0 ベースライン非劣化
- [ ] AGENTS.md 更新済み

**スコープ外:** rewrite の実装（T5）。SSE の `node_start` / `route_decided` / `rewrite_result` イベント（T6）。THETA 再チューニング。

**実装ノート:** 補足デリミタが安定して守れない場合（R-B）、プロンプト調整の無限ループに入らず、事実を記録して T4 を一旦完了とし、代替（例: 補足を別リクエストで生成、または grounded と direct の 2 段生成）を M7 追補スペックとして起票する。

---

## T5: rewrite ノード

**目的:** 会話履歴を考慮したクエリ書き換え。単一モデル・直列 GPU 前提のスキップ制御を含む。

**成果物:**
- `app/graph/nodes/rewrite.py`（**temperature=0 + seed 固定**、履歴空ならスキップ）
- rewrite プロンプト + JSON schema（T0 で確定した手段: json_schema or ネイティブ format）
- `eval_routing.py` の followup 評価と `--cached-rewrite` の実キャッシュ対応

**作業項目:**
1. rewrite ノード実装（スペック §5.1）。**generate と同一モデル**、temperature=0、seed 固定、N=4（config 化）
2. **スキップ制御:** `history` が空なら LLM を呼ばず即通過（`rewrite_applied=False`）。**語彙ヒューリスティックによるスキップは実装しない**
3. structured output: T0 で確定した手段で `{"search_query": str, "rewrite_applied": bool}` を取得
4. フォールバック: LLM 失敗・JSON パース失敗時は `search_query = user_query` で続行、警告ログ + Langfuse 記録
5. `--cached-rewrite`: rewrite 結果を jsonl キャッシュし、閾値チューニング時は retrieval 以降のみ再実行
6. followup 評価: `expected_search_query` との意味一致（embedding 類似度。**LLM-as-judge は使わない**）と retrieval hit@k を測定
7. 非 followup（corpus / general）での過剰書き換え率（`rewrite_applied` の false positive）を確認
8. レイテンシ計測: rewrite の追加分を Langfuse で記録し、**T0 の TTFT ベースラインとの相対値**で評価

**完了条件:**
- [ ] followup（holdout）: rewrite 後の retrieval hit@k が rewrite なし比 非劣化
- [ ] followup の direct 期待ケース（3–5 件）が rewrite 後も正しく direct になる
- [ ] corpus / general: `make eval-routing` 指標が T4 完了時から非劣化
- [ ] 履歴空のスキップ動作テスト（LLM が呼ばれないことを mock で確認）
- [ ] フォールバック動作テスト（LLM 呼び出しを mock で失敗させる）
- [ ] レイテンシ記録。**TTFT ベースライン超過が確認された場合は N 削減の検討事項として T7 に引き継ぐ**（M7 内では対応しない）

**スコープ外:** N の最適化（初期値 4 で固定）。履歴の事前要約化。モデル分割。

---

## T6: SSE 追加イベントとフロント最小表示

**目的:** グラフ実行の透明性をユーザーに提供する（最小実装）。

**成果物:**
- SSE イベント追加: `node_start`, `route_decided`, `rewrite_result`（`supplement_start` は T4 で実装済み）
- フロント: route バッジ表示（grounded / direct の 2 状態）+ 補足セクションの視覚的区切り

**作業項目:**
1. 各ノード先頭で `node_start` を送出。grade 完了時に `route_decided`（direct 時 `top_score: null`）、rewrite 完了時に `rewrite_result`（スキップ時も `applied: false` で送出）
2. M2 の SSE プロトコルドキュメントに追加イベントを追記（後方互換の明記）
3. フロント: `route_decided` でバッジ表示。**`supplement_start`（T4 実装）を受けて補足セクションを視覚的に区切る**。その他の新イベントは受信のみ
4. stub 構造検証テスト（T3）に新イベントの型・順序を追加

**完了条件:**
- [ ] 既存イベント型のペイロードに変更がないこと（stub 構造検証テストで担保）
- [ ] 2 経路それぞれでバッジが正しく表示される
- [ ] 補足セクションの視覚的区切りが表示される（複合質問で確認）
- [ ] SSE プロトコルドキュメント更新済み

**スコープ外:** 進捗のリッチ UI（スピナー・ノード別ステータス）→ M5 showcase の範疇。

---

## T7: 可観測性の仕上げとドキュメント確定

**目的:** 運用・閾値チューニング・レイテンシ分析の基盤を整え、スペックを実態に同期させる。

**成果物:**
- Langfuse trace metadata: `route`, `rewrite_applied`, `rewrite_skipped`, `theta`, `kept_count`, `top_score`, `supplement_emitted`（スペック §7）
- 各ノードの span 計装 + generate の TTFT 記録
- スペック確定版（Draft → Accepted、実装との差分反映）
- 未決事項（スペック §11）の決定値、T5 からのレイテンシ引き継ぎ事項の記録

**完了条件:**
- [ ] Langfuse で route 別のフィルタリング・レイテンシ比較ができることを確認
- [ ] rewrite / retrieve / generate の span 所要時間と TTFT が記録されていることを確認
- [ ] direct 経路の trace がダッシュボードで欠損扱いにならないことを確認
- [ ] スペックの Status が Accepted に更新され、§11 が解消または持ち越し記録されている
- [ ] `make eval-all` 全通過の最終確認

---

## 全体の完了定義（M7 クローズ条件）

- [ ] T0–T7 の全完了条件を満たす
- [ ] `make eval-all` 通過（holdout: grounded 見逃し ≤ 1 件 / direct 誤り ≤ 3 件 / direct 捏造 0 / デリミタ 5/5 / 補足分離 5/5 / e2e 非劣化）
- [ ] 依存ツリーに `langgraph` のみ追加され、langchain 系が含まれない
- [ ] LLM-as-judge が eval のどこにも使われていないこと（すべて決定的チェックまたは人手裁定）
- [ ] checkpointer 未使用のまま State のシリアライズ可能性が保たれている（M8 への引き継ぎ条件）
- [ ] M8 候補（clarify / HITL / LLM grader の要否）の判断材料が eval レポートとして残っている