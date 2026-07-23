# M8 タスクリスト: Clarify Route (rev.1)

- Spec: `docs/specs/26071715-m8_clarify_route/spec.md` (v0.1)
- Status: In progress（T0 完了、T1 完了〈clarify件数の目安未達1件はT2へ申し送り〉、T2 着手前）
- 実行順序: T0 → T1 → T2 →（GO/NO-GO 判定）→ T3 → T4 → T5 → T6
- 規約: 各タスクは「完了条件をすべて満たす」まで次に進まない。スコープ外の変更を行わない。判断に迷う点はタスク内の「実装ノート」の範囲でのみ裁量を認め、それ以外はスペックに差し戻す

---

## T0: 前提バグ修正と前提確認

**目的:** M8 の実装・eval 作業の前提となるブロッカーを解消する（スペック §6.3、Blocked by）。

**実装ノート（タスクリスト作成後のレビューで判明。重要）:** `backend/evals/dataset/routing_eval_results.jsonl` を確認したところ、130件中 129件は既に `scores`（rerank score 全件の生配列）が記録済みで、エラーは `c038` の1件のみ（Voyage レート制限起因のエラーであり、本バグとは無関係）。すなわちこのバグは **T1・T2 の実行そのものは妨げない**（T1/T2 は既存の記録済み `scores` に対する後処理分析のみで完結する。§T2 実装ノート参照）。このバグが実際に問題になるのは **T6**（T3 で変更した新しい `grade()` を、未処理/エラー扱いの id に対してライブ実行する場面）である。T0 を先に片付けること自体は妥当（安価な修正であり、後回しにする理由がない）だが、「T1/T2 の前提」という当初の位置づけは誤りだったため、ここで訂正する。

**作業項目:**
1. `graph/nodes/grade.py` の `get_stream_writer()` 呼び出しを、実行コンテキスト外（`evals/routing.py::retrieve_and_grade()` からの直接呼び出し）でも `RuntimeError` を送出しないよう修正する（スペック §6.3 の修正方針: `try/except RuntimeError` で no-op ライターにフォールバック）
2. `make eval-routing --stats-only`（Voyage/OpenAI 呼び出し無し）で既存の記録済み結果に影響がないことを確認する
3. `make eval-routing` で `c038`（Voyage レート制限エラーで未収集のまま残っている既存の1件。本バグとは無関係だが、ついでに再取得しておくと T2 の分析対象が完全になる）を再取得できることを確認する。これが本バグ修正後に `grade()` がライブ実行できることの実地確認を兼ねる
4. M7 の実装完了状況を確認する（スペック群の存在 ≠ 実装完了。M7 タスクリスト T0 と同じ確認パターン）: `grade`/`generate`/`builder`/`state` が現行スペック（rev.5）通りに実装されていること

**完了条件:**
- [x] `grade()` をグラフ実行コンテキスト外から直接呼んでも `RuntimeError` が発生しない（回帰テストを追加）
- [x] `make eval-routing --stats-only` の出力が修正前と一致する(既存の集計結果に影響がないこと)
- [x] `c038` を再取得でき、`routing_eval_results.jsonl` の該当レコードが `status: "ok"` になる
- [x] M7 実装状況の確認結果がタスクノートに記録されている

**実装結果（2026-07-22）:**
- `graph/nodes/grade.py::grade()` の `get_stream_writer()` 呼び出しを `try/except RuntimeError` で no-op writer にフォールバックするよう修正（スペック §6.3 の修正方針通り）
- 回帰テスト `tests/test_graph_nodes.py::TestGrade::test_callable_outside_langgraph_runnable_context` を追加。`get_stream_writer` を意図的に patch せず `grade()` を直接呼ぶ（`evals/routing.py::retrieve_and_grade()` と同じ呼び出し形）。修正前に `RuntimeError` で red、修正後 green を確認済み
- `--stats-only` は `process_dataset()`（grade呼び出し経路）を経由しないため、今回の修正の影響を受けないことをコード上確認（`evals/routing.py::main()` 参照）。実行結果も一致
- `c038` 再取得の過程で **本バグとは無関係の別ブロッカー**を発見: 開発DB(`rag_dev`)が Alembic `0003` のままで、M9で追加された `0004`(`drive_source_fields`)が未適用だった（`UndefinedColumn: sources.source_type`）。`make migrate` で解消。`c038` は再取得後 `status: "ok"`（`route=grounded`, `top_score=0.7695...`, 期待値`grounded`と一致）となり、`make eval-routing` は150件中エラー0件・判定GOで完走した
- M7実装状況確認: `graph/nodes/grade.py`・`graph/nodes/generate.py`・`graph/state.py`・`graph/builder.py` を確認。いずれもM7スペック(rev.5)通り、2値(grounded/direct)ルーティング・conditional edge・checkpointerなしのステートレス設計が実装済みであることを確認した（M8での変更はこれらに対する追加・拡張として進めてよい）
- `make lint`（backend: ruff+mypy 0件 / frontend: 既存の無関係な警告2件のみ、biome format差分なし）・`make test`（208 passed）を実行し、回帰がないことを確認

**スコープ外:** `get_stream_writer()` 問題の恒久的な設計解決（writer 注入用の別シームの新設等）。今回は局所的なフォールバックのみで対応する（スペック §6.3）。

---

## T1: routing eval データセットの clarify ラベリング見直し

**目的:** THETA_HIGH キャリブレーションと3値化 eval の基盤を作る。実装より先に完了させる（M7 T1 と同じ順序上の位置づけ）。

**成果物:**
- `backend/evals/dataset/routing.jsonl` の更新（`expected_route` に `"clarify"` を追加）
- `backend/evals/dataset/routing-README.md` への clarify カテゴリ節の追記

**作業項目:**
1. 既存 130 件のうち `category: ambiguous`（30件）を中心に全件を読み直し、「コーパスに関連記述が実在するかで機械的に grounded/direct に倒す」という既存ラベリング方針（`routing-README.md` §2）を見直す。真に聞き返しが妥当なケース（質問がどのトピックを指しているか複数解釈が成り立つ、または根拠が弱く断定も否定もできない）を `expected_route: "clarify"` に変更する
2. `category: followup`（20件）についても同様に見直す（フォローアップの言い換えが曖昧で、履歴を踏まえても意図が一意に定まらないケースがあれば `clarify` 候補とする）
3. `corpus`（40件）・`general`（40件）は変更しない（clarify 候補になり得ない固定ラベルのカテゴリ）
4. 見直した各件について、なぜ `clarify` が grounded/direct より妥当と判断したかの根拠を README に記録する（M7 の corpus/general/ambiguous と同じ記録水準）
5. calibration / holdout 分割は既存の乱数 seed・層化方式を維持する（re-shuffle しない。既存件数の分割比率が変わらないようにする）

**完了条件:**
- [x] `expected_route` が `"grounded" | "direct" | "clarify"` の3値になり、全件がスキーマ通りにパース可能
- [x] `clarify` に変更した全件について判断根拠が README に記録されている
- [ ] clarify 件数が calibration/holdout それぞれに一定数（THETA_HIGH のスコア分布分析に足る件数。目安 10 件以上/split）含まれている **→ 未達（下記実装結果参照。T2への申し送り事項として明示的に残す）**
- [x] 既存の grounded/direct ラベルに対する変更が、根拠の伴わない移動になっていない（README のレビューで確認）

**実装結果（2026-07-22）:**
- `ambiguous`(30件)・`followup`(20件)全件を、コーパス4ファイルの読み直し + `routing_eval_results.jsonl` に記録済みの実rerank scoreの両方を突き合わせて再判定した（判断方針の詳細は `routing-README.md` §9.1）。`corpus`/`general`(各40件)は変更していない
- 実スコア判定の前提として、リランク呼び出し自体が失敗し `top_score: null` のまま `status: "ok"` 記録されていた6件（`a002`, `a007`, `a022`, `f004`, `f007`, `f014`）を、既存の `search_query` を再利用する一度きりのスクリプトで再取得し、実スコアに更新した（`routing-README.md` §9.2）
- `expected_route: "direct"` → `"clarify"` に変更したのは8件: `a016`, `a017`, `a018`, `a019`, `a020`, `a022`, `a028`, `f017`（根拠は `routing-README.md` §9.3。全件、旧THETA(0.56)を実際に上回っていた=旧2値制で誤ってgroundedと予測されていた実例であることを確認済み）
- calibration/holdout の分割比率・乱数seedは変更していない（既存レコードの `split` フィールドをそのまま維持し、re-shuffleしていない）
- clarify件数はcalibration 6件（`a016`,`a017`,`a018`,`a020`,`a022`,`f017`）・holdout 2件（`a019`,`a028`）。目安の「10件以上/split」には届かなかった（特にholdoutが少ない）。既存データセットの再ラベリングのみで判定方針を緩めずに達成できる上限であり、新規質問の追加はスコープ外（本タスクの「スコープ外」参照）。この件数でT2のキャリブレーションが十分かはT2で判断し、不足時は新規`clarify`期待質問の追加要否をT2で検討する（`routing-README.md` §9.5）
- `a018`(実スコア0.816)は他のclarify候補(0.57〜0.71)より明確に高く、`grounded`側の低スコア実例(`a010`,`a022`とも0.640625)と範囲が重なることを確認した。スコアだけではclarifyとgroundedを完全に分離できない領域が存在する実例として`routing-README.md` §9.4に記録し、T2のTHETA_HIGH検討時に扱いを再検討するよう申し送った
- `make test`（208 passed。データセット変更はテストに影響しないことを確認）

**スコープ外:** 新規質問の追加作成（既存データセットの再ラベリングのみ。件数が不足する場合のみ T2 のキャリブレーション作業内で追加要否を判断する）。

---

## T2: THETA_HIGH キャリブレーション（GO/NO-GO 判定）

**目的:** THETA_HIGH の初期値を、スコア分布の実データに基づいて決定する（M7 T2 と同じ手順）。

**実装ノート:** 本タスクは `grade.py` の実装（T3）を必要としない。`routing_eval_results.jsonl` の各レコードには `scores`（rerank score 全件の生配列、降順）が既に記録されているため、`route = "grounded" if scores[0] >= theta_high else ("clarify" if scores[0] >= theta else "direct")` を候補の THETA_HIGH 値ごとに当てはめる**後処理の分析スクリプト**で完結する（`analyze_score_distribution.py` と同じ「importable パッケージ外の一回性スクリプト」の位置づけで新規作成してよい）。T1 で `expected_route` を改訂した後のデータセットと突き合わせるため、**T1 完了後**に実施する。

**作業項目:**
1. T1 で改訂した `expected_route`（3値）と、`routing_eval_results.jsonl` に記録済みの `scores` を突き合わせる分析スクリプトを作成する
2. `expected_route` ごとの rerank score 分布（特に `clarify` と `grounded`/`direct` の境界）を可視化・分析する
3. THETA（0.56、不変）を下限、分布分析から導いた値を上限として THETA_HIGH の候補値を複数出し、それぞれについて calibration split の3値混同行列を計算する（`scores` からの再計算のみで、ライブの `grade()` 呼び出しは不要）
4. GO/NO-GO 判定基準を定める（M7 §7.2 相当。例: clarify の再現率・適合率の下限値）。基準はこのタスク内でスコア分布の実データを見た上で具体的な数値として確定し、決定の経緯・根拠を本タスクの完了条件注記として本ファイルに直接記録する（独立ADRファイルは作成しない）
5. 最有力候補の THETA_HIGH について holdout split で最終検証し、GO であれば T3 以降に進む。NO-GO であれば THETA_HIGH の再調整、またはデータセットの見直し（T1 差し戻し）を検討する

**完了条件:**
- [ ] THETA_HIGH の初期値が ADR として文書化されている（分布分析の根拠込み）
- [ ] calibration split での3値混同行列が合格基準を満たす、または NO-GO の場合はその判断と対応方針が記録されている
- [ ] holdout split での最終確認結果が記録されている

**スコープ外:** LLM grader の導入によるグレーゾーン救済（スペックで非スコープと明記済み）。

---

## T3: grade ノードの3値化実装

**目的:** スペック §4.3（grade）を実装する。

**成果物:**
- `core/config.py` に `routing_theta_high: float`（T2 で決定した値をデフォルトに）
- `graph/nodes/grade.py` の3値判定ロジック
- `graph/state.py::GraphState.route` の型拡張（`Literal["grounded", "clarify", "direct"]`）
- `graph/builder.py` の conditional edge に `"clarify": "generate"` を追加
- `graph/state.py` モジュール docstring の checkpointer 言及の訂正（スペック §4.2 の実装ノート）

**作業項目:**
1. スペック §4.3 の擬似コード通りに `grade()` を実装（`kept`/`top_score` の計算方法は変更せず、判定ロジックのみ3分岐にする）
2. Langfuse trace レベル metadata に `theta_high` を追加
3. `route_decided` イベントのペイロードに変更が必要か確認する（route 値が3種になるだけで、既存フィールド構造は変えない）
4. `graph/state.py` の checkpointer 言及箇所を訂正する

**完了条件:**
- [ ] 3値それぞれ（grounded/clarify/direct）を発生させる単体テストが通過する
- [ ] rerank score 欠落時（RRF フォールバック経路）の安全側デフォルト（kept 扱い）が維持されている
- [ ] `graph/state.py` の docstring 訂正が完了している
- [ ] 既存の grounded/direct 単体テストのうち、スコアが新しい THETA_HIGH 未満（= clarify に該当）になるケースは期待値を `clarify` に更新し、THETA_HIGH 以上または THETA 未満のケースは無変更で通過する（「全テスト無変更で通過」を完了条件にしない。境界の再分類は仕様通りの変更であり回帰ではない）

---

## T4: clarify プロンプトと生成実装

**目的:** スペック §5（生成）を実装する。

**成果物:**
- `prompts/routing.py` に `CLARIFY_SYSTEM_PROMPT`
- `generation/generator.py` に `generate_clarify_answer_stream(query, weak_candidates)`
- `graph/nodes/generate.py` の `clarify` 分岐

**作業項目:**
1. `CLARIFY_SYSTEM_PROMPT` を作成する。`kept` チャンクのタイトル・見出しパスのみを渡し、本文をそのまま断定的な context として使わせない（grounded との違いを明確にする指示文にする）
2. `generate_clarify_answer_stream` を実装。既存の `_stream_llm_tokens` ヘルパー・Langfuse `@observe` パターンを再利用する
3. `generate` ノードに `clarify` 分岐を追加。`citations` イベントは送出しない
4. Langfuse trace で `clarify` 経路が `direct`/`grounded` と同様にネストされることを確認する

**完了条件:**
- [ ] `clarify` 経路で聞き返し文が生成され、SSE で正しくストリーミングされる（手動スモークテスト）
- [ ] `clarify` 経路で `citations` イベントが送出されないことを単体テストで確認
- [ ] `kept` が複数トピックにまたがる場合とほぼ同一トピックの場合の両方で、生成文が不自然でないことを手動確認する（LLM-as-judge は導入しない。M7 の補足書式検証ほど厳密な自動評価は本タスクのスコープ外）

---

## T5: フロントエンド3状態対応

**目的:** スペック §7 を実装する。

**成果物:**
- `frontend/src/lib/chat-adapter.ts` の route 型拡張
- `frontend/src/components/RouteBadge.tsx` の3状態表示

**作業項目:**
1. `chat-adapter.ts` の `route_decided` パース処理に `"clarify"` を許容値として追加
2. `RouteBadge.tsx` に3つ目の見た目を追加（配色・アイコン・tooltip 文言はスペック §7.2 の例を踏襲してよいが、UI レビューで調整可）
3. `chat-adapter.test.ts` に `route: "clarify"` のテストケースを追加

**完了条件:**
- [ ] 3経路それぞれでバッジが正しく表示される（フロントの単体テストで検証）
- [ ] 既存 grounded/direct のテスト・表示に回帰がない

---

## T6: eval 合格基準の拡張とドキュメント確定

**目的:** `make eval-routing` を3値対応させ、スペックを実装に同期させる。

**実装ノート（重要）:** `routing_eval_results.jsonl` に既に記録されている `predicted_route` は **T3 より前の旧2値 `grade()` が出した値**であり、`"clarify"` を一切含まない。かつ `process_dataset()` は `status: "ok"` の id をスキップするため、T3 実装後に `make eval-routing` をそのまま再実行しても、既存 129 件超の `predicted_route` は自動更新されない。したがって `evals/routing.py` の集計ロジックは、記録済み `predicted_route` をそのまま信頼するのではなく、各レコードの `scores`（生配列）に対して T3 で実装した3値判定と同じロジック（THETA/THETA_HIGH 比較）を適用して `predicted_route` を**その場で再計算**するように変更する（T2 の分析スクリプトと同じ計算式を `evals/routing.py` 側にも実装として反映する形になる。二重実装を避けるため、可能であれば T2 の分析スクリプトのロジックを共通化して再利用する）。

**作業項目:**
1. `evals/routing.py` の集計ロジックを、記録済み `scores` から THETA/THETA_HIGH で `predicted_route` を再計算する3値混同行列に拡張する（上記実装ノート）
2. T2 の ADR に基づく合格基準を `make eval-routing` の GO/NO-GO 判定に反映する
3. 再計算した3値混同行列で holdout split の合否を確認する。`c038` 以外のライブ再実行（Voyage/OpenAI 呼び出し）は不要（既存 `scores` の再利用で完結するため）
4. `docs/specs/26071715-m8_clarify_route/spec.md` の Status を Draft → Accepted に更新し、実装との差分があれば反映する
5. `docs/decisions.md` に THETA_HIGH の決定を追記する（M7 の記載パターンに合わせる）

**完了条件:**
- [ ] `make eval-routing` が3値の合否結果を出力する
- [ ] holdout split での最終合否が記録されている（NO-GO の場合は受理判断の経緯・根拠を本タスクリストに直接記録する。M7 T4/T7 の記録パターンを踏襲）
- [ ] スペックの Status が Accepted に更新されている
- [ ] `docs/decisions.md` の更新が完了している

---

## 全体の完了定義（M8 クローズ条件）

- [ ] T0–T6 の全完了条件を満たす
- [ ] `make eval-routing` の3値合否結果が記録されている（PASSED/NO-GO いずれの場合も受理判断が明示されている）
- [ ] `make lint` / `make test` が通る
- [ ] グラフが checkpointer なしのステートレス設計のまま維持されている（clarify 経路を含め、新規の永続化機構を追加していない）
- [ ] `docs/specs/26071715-m8_clarify_route/spec.md` の Status が Accepted
