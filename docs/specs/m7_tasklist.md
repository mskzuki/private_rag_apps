# M7 タスクリスト: Adaptive Routing (rev.3)

- Spec: `m7_adaptive_routing.md` (rev.3)
- Status: In Progress（T0 完了）
- 実行順序: T0 → T1 → T2 →（GO/NO-GO 判定）→ T3 → T4 → T5 → T6 → T7
- 規約: 各タスクは「完了条件をすべて満たす」まで次に進まない。スコープ外の変更を行わない。判断に迷う点はタスク内の「実装ノート」の範囲でのみ裁量を認め、それ以外はスペックに差し戻す

**2026-07-14 実装前レビューでの決定事項（詳細はスペック rev.3 参照）:**
1. T5 rewrite は既存 `generation.condense()` を再利用・拡張する（新規 Anthropic クライアントは追加しない）
2. グラフ実装は新規トップレベルパッケージ `private_rag_apps/graph/` とする（`api/` 配下ではない）。AGENTS.md §3 改訂を T3 に追加
3. `retrieval/searcher.py::_rerank()` に `rerank_score` を追加する最小限の変更を T4 で行う（retrieval 内部無変更方針の唯一の例外）
4. OpenAI/Voyage/Langfuse のアカウントブロッカーが 2026-07-14 時点で未解消。実 API 呼び出しを伴うタスク（T1 followup 収集、T2 一括検索、T4/T5 の LLM 呼び出し）は着手前に都度状況を確認する

---

## T0: 前提確認

**目的:** M7 が依存する実装の完了状況を確認する（スペック群の存在 ≠ 実装完了）。

**作業項目:**
1. M2（SSE streaming）: `/chat` の streaming が本スペック §5.2 の既存イベント型で動作していることを確認
2. M3（conversation history）: 履歴のロード・保存と、generate への履歴注入の実装箇所を特定し、パスをタスクノートに記録（T3 の移設対象）
3. M4（evaluation）: `make eval` が現在通過することを確認し、ベースラインスコアを記録
4. retrieval パイプライン: rerank score が API/サービス層から取得可能であることを確認（grade の入力）

**完了条件:**
- [x] 上記 4 点の確認結果と該当コードパスがタスクノートに記録されている
- [x] 未完了の依存が見つかった場合: M7 を保留し、不足分の実装タスクを別途起票（M7 のスコープに取り込まない）→ 該当なし。retrieval のみ小さな追加実装が必要と判明したが、M7（T4）のスコープ内で対応可能と判断（下記タスクノート）

**タスクノート（2026-07-14 記録）:**
1. **M2 SSE streaming:** `event_generator()`（`api/main.py`）が `token` / `citations` / `error` / `done` を送出済み。スペックの表記は `citation`（単数）だったが実装は `citations`（複数）のため、スペック §5.2 を rev.3 で修正した
2. **M3 history:** 履歴は `api/main.py` で DB からロードされ、`generation.condense(query, history_messages)`（`generation/generator.py`）にのみ渡される。**`generate_answer_stream(query, context_chunks)` には履歴が渡らない**（generate は履歴を見ない実装のまま）。スペック rev.1/rev.2 は「generate も履歴を注入される」という誤った前提だったため rev.3 で訂正済み。この発見を受け、**T5 は新規実装ではなく既存 `condense()` の再利用・拡張とする**（rev.3 §4.3。ユーザー承認済み）
3. **M4 eval:** OpenAI/Voyage/Langfuse のアカウント側ブロッカー（2026-07-13〜14発生、2026-07-14朝時点で未解消）のため `make eval` の再実行は見送り。既存の記録済みベースライン（`backend/evals/reports/latest_summary.md` / `backend/evals/baselines/current.json`、2026-07-13T08:49:51 UTC、**PASSED**: Recall@5=0.935 / Recall@10=0.968 / nDCG@10=0.742 / MRR=0.666（reranked）、Faithfulness=0.935 / Answer Relevance=0.774、`llm_provider=openai` 既定設定）を暫定ベースラインとして採用する。ブロッカー解消後、T3/T4 完了条件の非劣化確認で改めて実測する
4. **retrieval rerank score:** **現状 `retrieve_context()` は chunk ごとの rerank score を返していない**。`_rerank()`（`retrieval/searcher.py`）は Voyage の結果でチャンクを並べ替えるのみで、`_format_chunks()` の出力にスコアを含めない。**T4 で `rerank_score` フィールドを追加する最小限の変更を行う**（ランキングロジック自体は変更しない。ユーザー承認済み。rev.3 §4.3 grade）

---

## T1: routing eval データセット作成

**目的:** 閾値キャリブレーションと grade 精度検証の基盤を作る。実装より先に完了させる。

**成果物:**
- `backend/evals/dataset/routing.jsonl`（スペック §7.1 のスキーマ。`split` フィールド込み。既存 `m3_golden.jsonl` と同じ置き場）
- `backend/evals/dataset/routing-README.md`（カテゴリ定義・ラベリング基準・followup 作成手順の記録)
- 既存 e2e eval セットへの複合質問 5 件の追加

**作業項目:**
1. スキーマ実装: スペック §7.1 の通り。`split: "calibration" | "holdout"` を全行に付与
2. corpus 40 件: 既存 eval セットから流用・改変。根拠ドキュメントの実在を 1 件ずつ確認し、パスを README に記録
3. general 40 件: 一般知識質問を新規作成。ドメイン分散（プログラミング一般 / 統計 / インフラ一般 / RAG 一般論）。**コーパスに関連記述がないことを検索で確認**
4. ambiguous 30 件: 手作業で作成。expected_route は「コーパスに関連記述が実在するか」で機械的に決定し、判断根拠を全件 README に記録
5. followup 20 件: history 付き。**history の assistant 応答は実アプリで生成した実物を記録して使う**（作成手順を README に規定）。`expected_search_query` を記録。expected_route は記述の実在で決定し、**direct 期待のフォローアップも 3–5 件含める**（rewrite の副作用検出用）
6. **calibration / holdout 分割:** カテゴリ内層化で 70/30。乱数 seed を README に記録（再現可能な分割）
7. 複合質問 5 件（一部 corpus・一部一般論）を作成し、**routing.jsonl ではなく既存 e2e eval セットに追加**（補足書式の検証用。スペック §7.2）

**完了条件:**
- [ ] 全件が JSON Lines としてパース可能、必須フィールド欠損なし
- [ ] grounded 期待の全件について根拠ドキュメントのパスが README に記録されている
- [ ] direct 期待の全件について「コーパスに記述なし」の確認が済んでいる
- [ ] 件数: corpus ≥ 40, general ≥ 40, ambiguous ≥ 30, followup ≥ 20、各カテゴリの holdout が 30% ± 1 件
- [ ] e2e セットに複合質問 5 件が追加されている

**スコープ外:** eval 実行スクリプトの実装（T2 / T4）。アプリケーションコードへの変更一切。

**実装ノート:** ambiguous の作成が最も時間を要する（30 件で 3〜4 時間目安）。corpus 質問から固有名詞を落として一般化する操作で境界ケースを量産できる。

---

## T2: rerank score 分布分析と THETA 初期値決定

**目的:** 閾値方式の成立可否を実装前に検証する（スペック §8 リスクの早期検証）。

**成果物:**
- `backend/evals/analyze_score_distribution.py`（既存の `backend/evals/generate_dataset.py` と同じ置き場: importable パッケージ外の一回性スクリプト）
- `backend/evals/calibrate_threshold.py`
- `backend/evals/reports/m7-score-distribution.md`（分析結果と THETA の決定記録。既存 `reports/` と同じ置き場）

**作業項目:**
1. T1 の全クエリ（followup は `expected_search_query` を使用。rewrite はまだ存在しないため）を既存 retrieval パイプラインに流し、rerank score を全件記録
2. カテゴリ別スコア分布を可視化（grounded 期待 vs direct 期待の top-1 score 分布の分離度を確認）
3. grid search（**calibration split のみ使用**）: 「grounded 見逃し率 ≤ 0.05」を制約条件、direct 適中の最大化を目的関数として THETA を決定
4. **決定した THETA を holdout に一度だけ適用**し、スペック §7.2 の件数基準（grounded 見逃し ≤ 1 件、direct 誤り ≤ 3 件）で評価。結果と THETA をレポートに記録
5. holdout の結果を見て THETA を再調整しない（する場合は calibration に戻り、holdout 再使用は 1 回までとしてレポートに明記する）

**完了条件:**
- [ ] スコア分布レポートが作成され、分離度が確認できる
- [ ] THETA 初期値が calibration のみで決定され、決定過程がレポートに記録されている
- [ ] **GO/NO-GO 判定（holdout 上）:** grounded 見逃し ≤ 1 件 かつ direct 誤り ≤ 3 件 → GO（T3 へ）。満たさない → NO-GO（T3 以降を保留し、スペック §7.4 に従い LLM grader スペックの起票に切り替える）

**スコープ外:** アプリケーションコードへの変更一切。THETA の config 化（T4）。

**実装ノート:** retrieval の呼び出しは既存サービス層の関数を import して使う。API 経由にしない（generate を走らせないため）。

---

## T3: LangGraph 最小導入（pass-through グラフ + SSE 互換検証）

**目的:** ストリーミング接合という最大の技術リスクを、機能追加ゼロの状態で単独検証する。**このタスク完了時点で外形的挙動は現行と同等であること。**

**成果物:**
- `backend/pyproject.toml`: `langgraph` 追加（**`langchain` / `langchain-anthropic` を追加しないこと**）
- `backend/src/private_rag_apps/graph/state.py`: `GraphState` TypedDict（スペック §4.2）
- `backend/src/private_rag_apps/graph/builder.py`: retrieve → generate のみの 2 ノードグラフ
- `backend/src/private_rag_apps/graph/nodes/retrieve.py`, `.../graph/nodes/generate.py`
- `/chat` ハンドラのグラフ経由への切り替え
- stub モデルによる SSE 構造検証テスト
- **AGENTS.md §3 改訂**: 新設する `graph` パッケージの依存方向ルールを明文化（`graph` は `generation`/`retrieval` を独立に import してよい／`api` は `graph` 経由でこれらを呼ぶ／履歴のロード・永続化は `api` に残る。スペック rev.3 §4.1）

**作業項目:**
1. `GraphState` をスペック §4.2 の通り定義。**Pydantic モデル・コネクション類を State に含めない**（スペック §3.4）。`json.dumps(state)` が通ることのシリアライズ可能性テストを追加
2. retrieve ノード: 既存 retrieval サービスの薄いラッパー。`search_query` には `user_query` をそのまま入れる（rewrite は T5）
3. generate ノード: 既存の生成ロジックを移設。`get_stream_writer()` でトークンを `stream_mode="custom"` に流す。**既存 SDK ラッパー・prompt cache 制御・Langfuse 計装・履歴注入（T0 で特定したパス）をそのまま維持する**
4. FastAPI ハンドラ: `graph.astream(stream_mode="custom")` を消費し、既存 SSE フォーマットに変換。履歴ロードと応答永続化はハンドラ層に残す（グラフ外、スペック §3.4）
5. **構造検証（自動・決定的、スペック §5.3）:** 生成を stub クライアント（固定文字列）に差し替えた統合テストで、SSE イベント型の系列・JSON スキーマ・順序を現行実装のキャプチャと比較。**LLM 生成は非決定的であるため実 LLM でのペイロード diff 比較は行わない**
6. **実機検証（手動）:** 実 LLM で TTFT・トークン順序・citation・done を assistant-ui 上で確認
7. 未知イベント型を 1 つ流し、フロントが壊れないことを確認（T6 の前提検証）。壊れる場合、SSE ハンドラへの default-ignore 追加は本タスクのスコープに含める

**完了条件:**
- [ ] 既存の全テストが通過（リグレッションゼロ）
- [ ] `make eval` が T0 で記録したベースラインと同等スコア（generation 品質の非劣化）
- [ ] stub 構造検証テストが通過し、CI に組み込まれている
- [ ] 実機検証の結果（TTFT 目視比較含む）がタスクノートに記録されている
- [ ] 未知イベント型に対するフロントの無視動作を確認済み
- [ ] langchain 系パッケージが依存ツリーに入っていないこと（`pip list` で確認）

**スコープ外:** grade / rewrite / 経路分岐 / 新規 SSE イベント。プロンプト変更一切（`make eval` 対象変更を発生させない）。

---

## T4: grade ノードと 2 経路分岐

**目的:** M7 の中核。THETA による grade と、grounded / direct のプロンプト実装。

**成果物:**
- `backend/src/private_rag_apps/graph/nodes/grade.py`（LLM を使わない純関数）
- `backend/src/private_rag_apps/retrieval/searcher.py`: `_rerank()` の返り値に `rerank_score` フィールドを追加（最小限の追加。ランキングロジック自体は無変更。T0 タスクノート・スペック rev.3 §4.3 grade 参照）
- `backend/src/private_rag_apps/core/config.py`: `routing_theta`（T2 の決定値をデフォルトに）
- プロンプト 2 種: `grounded`（**「一般知識に基づく補足」書式ルール込み**。スペック §4.3 generate）/ `direct`（コーパス言及禁止ルール込み）
- `backend/src/private_rag_apps/evals/` に routing eval 実行コード（`make eval-routing` ターゲット。`--cached-rewrite` オプション付き。T5 までは rewrite パススルー）
- direct groundedness eval（LLM-as-judge + 人手裁定手順）
- 複合質問 5 件の補足書式検証の `make eval` への組み込み
- AGENTS.md 更新: THETA・rewrite プロンプト・grade ロジック変更時の `make eval-routing` 必須化、grounded / direct プロンプト変更時の `make eval` 必須化

**作業項目:**
1. `retrieval/searcher.py::_rerank()` の返り値の各チャンク dict に `rerank_score`（Voyage の relevance score）を追加する（T0 タスクノート参照。ランキング順序・検索ロジックは無変更）
2. grade ノード実装（スペック §4.3）。conditional edge で 2 経路分岐、generate は `state["route"]` でプロンプト切り替え
3. grounded プロンプト: 既存 RAG プロンプトに補足書式ルールを追加。**カバレッジ判定（どこまで context で答えられるか）はプロンプト指示に委ねる設計であることをコードコメントに明記**（後任が grade に判定ロジックを足そうとするのを防ぐ）
4. direct プロンプト: スペック §4.3 の通り
5. `make eval-routing`: rewrite（T5 まではパススルー）→ retrieve → grade を実行し、calibration / holdout 別にスペック §7.2 の指標を出力。**generate は実行しない**
6. direct groundedness eval: direct 経路の回答（general カテゴリ holdout から 10 件 + calibration から 10 件）への LLM-as-judge。**judge が違反と判定した件は人手で裁定し、真の違反のみカウントする手順をスクリプトの README に規定**
7. 補足書式検証: e2e eval の複合質問 5 件で、context 外の内容が補足セクションに分離されていることを判定

**完了条件:**
- [ ] `make eval-routing`（holdout）: grounded 見逃し ≤ 1 件、direct 誤り ≤ 3 件（T2 の GO 判定の再現）
- [ ] direct groundedness: 人手裁定後の真の違反 0
- [ ] 補足書式: 複合質問 5/5 で分離が確認できる（判定結果の人手確認込み）
- [ ] 2 経路 + 補足発生ケースの手動スモークテスト（grounded 2 件 / direct 2 件 / 複合 1 件、SSE 経由）
- [ ] `make eval`（既存 e2e）が T0 ベースライン非劣化 — grounded プロンプト変更の影響を特に注視
- [ ] AGENTS.md 更新済み

**スコープ外:** rewrite の実装（T5）。SSE 新イベント（T6）。THETA の再チューニング（T2 の値を使う。満たせない場合は T2 に差し戻し、holdout 再使用の記録規約に従う）。

**実装ノート:** 補足書式が LLM-as-judge で安定して守れない場合、その事実を記録して T4 を一旦完了とし、構造化出力化（補足を別フィールドで返させる）を M7 追補スペックとして起票する（スペック §8 のリスク対応）。プロンプト調整の無限ループに入らない。

---

## T5: rewrite ノード

**目的:** 会話履歴を考慮したクエリ書き換えで followup 質問の retrieval 品質を確保する。

**成果物:**
- `backend/src/private_rag_apps/generation/generator.py`: `condense()` を拡張し `rewrite_applied` を追加で返す（**temperature=0 を明示指定**。スペック §3.5 / §4.3 rev.3。新規 Anthropic クライアントは追加しない）
- `backend/src/private_rag_apps/graph/nodes/rewrite.py`: 拡張後の `condense()` を呼ぶグラフノードの薄いラッパー
- `eval_routing` の followup 評価と `--cached-rewrite` の実キャッシュ対応

**作業項目:**
1. `condense()` の拡張（スペック §4.3 rev.3）。新規クライアント・新規モデルは追加せず、既存 `get_llm_client()` / `settings.llm_provider` をそのまま使う。**temperature=0** を明示指定。履歴は既存の `settings.condense_history_turns`（直近 N ターン）をそのまま使う
2. グラフの rewrite ノードは拡張後の `condense()` を呼ぶ薄いラッパーとして実装（新規ロジックを持たない）
3. フォールバック確認: LLM 失敗時 `search_query = user_query` で続行、警告ログ + Langfuse へ記録（`condense()` の既存フォールバックを踏襲）
4. `--cached-rewrite`: rewrite 結果を jsonl キャッシュし、閾値チューニング時は retrieval 以降のみ再実行できるようにする
5. followup 評価: `expected_search_query` との意味一致と、rewrite 後クエリでの retrieval hit@k を測定
6. 非 followup クエリ（corpus / general）での過剰書き換え（`rewrite_applied` の false positive）率を確認
7. レイテンシ計測: rewrite の追加分を Langfuse で p50 / p95 記録

**完了条件:**
- [ ] followup（holdout）: rewrite 後の retrieval hit@k が rewrite なし比で非劣化
- [ ] followup の direct 期待ケース（3–5 件）が rewrite 後も正しく direct になる
- [ ] corpus / general: `make eval-routing` の指標が T4 完了時から非劣化（rewrite の副作用がないこと）
- [ ] フォールバック動作のテスト通過（LLM 呼び出しを mock で失敗させる）
- [ ] レイテンシ p95 の記録。**+800ms 超過が確認された場合は N 削減の検討事項として T7 のドキュメントに引き継ぐ**（M7 内では対応しない。スペック §4.3）

**スコープ外:** N の最適化（初期値 6 で固定）。履歴の事前要約化。新規 LLM プロバイダの追加。

---

## T6: SSE 追加イベントとフロント最小表示

**目的:** グラフ実行の透明性をユーザーに提供する（最小実装）。

**成果物:**
- SSE イベント追加: `node_start`, `route_decided`, `rewrite_result`（スペック §5.2）
- フロント: route バッジ表示（**grounded / direct の 2 状態**）

**作業項目:**
1. 各ノードの先頭で writer に `node_start` を送出。grade 完了時に `route_decided`（direct 時は `top_score: null` を許容）、rewrite 完了時に `rewrite_result`
2. M2 の SSE プロトコルドキュメントに追加イベントを追記（後方互換の明記）
3. フロント: `route_decided` を受けてバッジ表示。それ以外の新イベントは受信のみ（表示なし）
4. stub 構造検証テスト（T3）に新イベントの型・順序を追加

**完了条件:**
- [ ] 既存イベント型のペイロードに変更がないこと（stub 構造検証テストで担保）
- [ ] 2 経路それぞれでバッジが正しく表示される
- [ ] SSE プロトコルドキュメント更新済み

**スコープ外:** 進捗のリッチ UI（スピナー・ノード別ステータス表示）→ M5 showcase の範疇。補足セクション有無のバッジ表示（route ではないため。必要なら M5 で検討）。

---

## T7: 可観測性の仕上げとドキュメント確定

**目的:** 運用・閾値チューニングの分析基盤を整え、スペックを実態に同期させる。

**成果物:**
- Langfuse trace metadata: `route`, `rewrite_applied`, `theta`, `kept_count`, `top_score`（スペック §6）
- 各ノードの span 計装（既存 Langfuse クライアント直呼び）
- スペック確定版への更新（Draft → Accepted、実装との差分を反映）
- 未決事項（スペック §10）の決定値、および T5 からの引き継ぎ事項（レイテンシ対応要否）の記録

**完了条件:**
- [ ] Langfuse 上で route 別のフィルタリング・レイテンシ比較ができることを確認
- [ ] direct 経路の trace がダッシュボードで欠損扱いにならないことを確認
- [ ] スペックの Status が Accepted に更新され、§10 が解消または明示的に持ち越し記録されている
- [ ] `make eval-all` 全通過の最終確認

---

## 全体の完了定義（M7 クローズ条件）

- [ ] T0–T7 の全完了条件を満たす
- [ ] `make eval-all` 通過（holdout: grounded 見逃し ≤ 1 件 / direct 誤り ≤ 3 件 / groundedness 真の違反 0 / 補足書式 5/5 / 既存 e2e 非劣化）
- [ ] 依存ツリーに `langgraph` のみ追加され、langchain 系が含まれない
- [ ] checkpointer 未使用のまま State のシリアライズ可能性が保たれている（M8 への引き継ぎ条件）
- [ ] M8 候補（clarify / HITL / LLM grader の要否）の判断材料が eval レポートとして残っている