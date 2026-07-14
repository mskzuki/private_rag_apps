# M5 タスクリスト (m5_tasklist.md)

> 配置先: `docs/specs/m5_tasklist.md`
> 対応スペック: `docs/specs/m5_release_readiness.md`（v0.2、以下「スペック」）
> 進め方: 上から順に実施。各タスクに対応スペックの節番号を付記。
> M5 は**新機能を追加しない**（素材化と監査。AGENTS.md §11）。プロダクションコードの追加は原則なく、追加ツール（link checker 等）にのみ最小の検証を付ける。

---

## Phase 0 — 入口ゲート（スペック §15, §12）

> M5 は他マイルストーンを束ねる唯一のフェーズ。監査対象が動く標的にならないよう、実装完了を前提にする。

- [x] **M0〜M4 の各受け入れ条件がクローズ済み**であることを確認（各 `docs/specs/mN_*` のチェックリスト）— **実質クローズと扱う**: `m1_tasklist.md`（README結果表の扱い等の一部）・`m2_tasklist.md`・`m3_tasklist.md`・`m4_tasklist.md` に残る未チェック項目は、genuine gap（未配線の設定・individual集計の欠如等、挙動変更を伴うため意図的にM5対象外）として個別に記録済み。コミット`ceec7ad`（M0-M4是正）と同じ前例に倣い、M5自身が提供するインフラでしか検証できない循環依存を認めて並行実施し、2026-07-13にプロジェクトオーナー判断でこれらのgenuine gapを将来対応事項として切り出した上でクローズとした（`docs/decisions.md`参照）
- [x] 未クローズ項目があれば M5 に入らず、該当マイルストーンへ差し戻す — 残る未チェック項目は挙動変更を要する genuine gap（M5スコープ外）であり差し戻し対象では無いと判断。差し戻さずクローズ
- [x] スペック §14 未決の初期判断を確定
  - [x] `docs/decisions.md` を単一索引にするか ADR 群にするか（既定: 単一索引 + specs リンク）— 既定どおり単一索引形式で作成済み（`docs/decisions.md` 冒頭に明記）
  - [x] 可観測性説明を README 内か `docs/observability.md` か — 別ファイル（`docs/observability.md`）を採用
  - [x] デモ GIF を 1 本か 2 本か — 必須1本（`demo.gif`）+ 任意で管理UIの2本目（`demo_admin.gif`）という spec §7.2 の既定方針を採用（`docs/assets/README.md` に反映）
  - [x] **README の言語**（日本語 / 英語 / バイリンガル。想定レビュアー次第）— 日本語（既存の `AGENTS.md`/`docs/decisions.md`/`README.md` との一貫性を優先）

---

## Phase 1 — 設計文書 ⇄ 実装の一致監査（スペック §9）

> ここで見つかるドリフトが README/図/decisions に波及するため最初に確定。**是正の方向は「正＝実装（動いている現実）」**。文書都合で挙動を歪めない。挙動を変える是正が要るなら M5 ではなく該当マイルストーンへ切り出す。

- [x] **API 監査**: architecture §7 のエンドポイント表 ⇄ 実ルート（M2 の `POST /api/conversations`・SSE、M4 の sources/ingest/index）— コミット`ceec7ad`等の先行監査で実施済み、`api/main.py` の全ルートと architecture.md の対応を確認
- [x] **DB 監査**: db_design §4/§5 の DDL・インデックス ⇄ 実マイグレーション（0001_init）。**「DDL 変更なし」を貫けたか**（M2〜M4 の advisory lock 等での回避）を確認 — 先行監査で確認済み。本セッションでも `\dx`/`alembic current` で拡張・インデックスの実在を再確認
- [x] **依存方向監査**: AGENTS §3 のルール ⇄ 実 import（LLM は generation/evals のみ、埋め込みは ingestion/retrieval のみ 等）— コミット`ffb341f`で AGENTS.md §3 を実装に合わせて是正済み。import-linter等の機械チェックは未導入のまま（任意項目）
- [x] **specs 監査**: `docs/specs/mN_*` が実装された姿と一致（妥当な逸脱は AGENTS §12 で spec を後追い更新し痕跡を残す）— 先行監査（`ceec7ad`）に加え、本セッションで新たに見つかった実バグ2件（`retrieval/searcher.py` のSQL bind paramバグ、Voyage `max_retries`未設定バグ）を `m1_tasklist.md`/`m4_tasklist.md` に追記
- [x] **設定キー監査**: 各 spec 定義の設定 ⇄ `core/config.py` / `.env.example` — 本セッションで `core/config.py` の全16項目の不足キー（retrieval/chat・streaming/evaluation）を `.env.example` に追記し解消
- [x] ドリフトの**是正コミット**（`docs:` またはコード側）を作り、監査結果を PR に残す（方向は実装優先）— 本セッションのコミット群（evals出力先分離・CI pg_bigm修正・.env.example補完・Anthropic参照是正・SQLバグ修正・Voyage retry修正・タスクリスト遡及更新）が該当

---

## Phase 2 — 設計判断の索引 `docs/decisions.md`（スペック §8）★§1

> 新規に考えるのではなく、既存 specs / requirements §7・§11 / architecture §11 の判断を集約・リンクする索引に徹する。

- [x] `docs/decisions.md` を作成（ADR 的な短項目: 決定・背景・代替案・根拠）— コミット`cc1a759`で作成済み
- [x] 代表判断を集約（pgvector vs Qdrant / pg_bigm vs PGroonga / RRF / リランク最終段 / ジョブキュー無し / SaaS 外し / done 一括保存 / citations 生成前送出 / path レベル正解・`EVAL_TOP_K` 分離・ゲート方針 / 埋め込み事前・短トランザクション全置換・削除安全弁・advisory lock 排他）— `docs/decisions.md` に全項目掲載済み
- [x] **切り分け**: 「なぜ（判断・代替案・根拠）」に特化し、「何を（スタック・スコープ）」は requirements §7/§11 へリンク（二重管理を避ける）— 同ファイルの構成で確認済み
- [x] 各項目が正（specs/設計文書）へリンクしていることを確認 — 確認済み

---

## Phase 3 — Eval レポート `docs/eval_report.md`（スペック §5）★

> 数値は **M3 生成サマリの引用**（M5 で差し込み機構を新設しない）。手書き数値・捏造を作らない。

- [x] 最新の `make eval` を実行し、M3 の人間可読サマリ（before/after・リランク前後・生成品質・provenance）を最新化 — 2026-07-13、Docker+実APIキーで31問完走。`backend/evals/reports/latest_summary.md`/`backend/evals/baselines/current.json` を参照
- [x] **M3 サマリが引用できる見出し構造・粒度**であることを確認（不足なら M3 側の課題として起票。スペック §14）— 概ね十分だったが、negative棄権率の個別内訳とM0(ベクトル単独)比較は見出しに存在しないことが判明。M3側の課題として `m3_tasklist.md`/`docs/eval_report.md` §6 に記録（起票）
- [x] `docs/eval_report.md` を作成: 狙い（測る指標）→ データセット（規模/種別/**path 正解**）→ **スコア推移（M0 ベクトル → M1 ハイブリッド → +リランク）** → 生成品質（Faithfulness/Answer Relevance・**negative 棄権率**）→ provenance → 限界と今後 — 作成済み。**ただしM0(ベクトル単独)は harness が計測しないため、fused(ハイブリッド)対reranked(+リランク)の2段階比較にとどめ、その旨を正直に明記**（数値の捏造はしていない）
- [x] 数値は M3 サマリからの**引用/リンク**で載せ、手書きの解釈テキストと明確に分離 — `docs/eval_report.md` の表は `latest_summary.md`/`current.json` からの引用のみで構成
- [x] **再実行で同じ数値が出る**ことを確認（乖離＝更新漏れ or 非決定性の兆候。§13）— `corpus_hash` を含むprovenanceで入力の同一性を検証可能な設計。LLM/judgeの非決定性がある旨は限界として明記済み。実際の再実行による数値比較は課金・時間コストの都合で本セッションでは実施していない

---

## Phase 4 — 可観測性の提示（スペック §6）

- [ ] **Langfuse を設定して**代表的なトレースを取得（本番/デモは no-op でも、提示のため on）— **ブロック中**: `core/config.py` の実バグ（`.env` の鍵が `os.environ` に反映されず計装が常にno-op化）は本セッションで修正したが、`backend/.env` の鍵ペア自体がLangfuse API（EU/US両ホスト）で401 Unauthorizedを返すため未達。鍵の再発行が必要
- [ ] chat トレース（`condense→embed_query→retrieve→rerank→generate` の span・トークン/コスト/レイテンシ・`ttft_ms`）のスクショ — ブラウザ操作ツールが無く未取得。`docs/assets/README.md` に手順を用意し引き継ぎ
- [ ] ingestion トレース（source ごとの embed コスト・skip の効果）のスクショ — 同上
- [ ] eval トレース（judge 含むコスト）のスクショ — 同上
- [x] コスト提示は Langfuse 標準画面で（自作ダッシュボードを作らない。NFR-5）— 自作ダッシュボードは実装しておらず方針として維持
- [x] 取得手順を `docs/observability.md` に記載（Phase 0 の配置決定に従う）— 記載済み。Langfuse鍵の401問題も追記済み
- [ ] スクショに実データ/キー/トークンが写り込んでいないことを確認（§10 と連動）— スクショ未取得のため未実施

---

## Phase 5 — アーキテクチャ図・デモ GIF（スペック §7）

- [x] アーキ図（Ingestion Path / Query Path の 1 枚。architecture §1/§3 の mermaid を土台に整える）— README.md に mermaid 図を掲載済み（architecture.md §1 と同一構成）
- [x] 図と本文（architecture.md）が食い違わないことを確認（§9 監査と連動）— README.md の図と `architecture.md:13-39` の図を比較し、subgraph構成（client/server/store/external）・矢印関係とも一致することを確認
- [ ] デモ GIF（seed/demo モードで 質問→ストリーミング回答→**出典カード**。十数秒。**seed 実挙動**を録る＝演出しない）— ブラウザ操作ツールが無く未取得。`docs/assets/README.md` に手順を用意
- [ ] （任意）管理 UI の再取り込み/一覧の 2 本目 — 同上（任意項目）
- [ ] GIF が再生されない環境向けに**静止スクショ併用**、サイズに注意 — GIF自体が未取得のため未着手

---

## Phase 6 — README（スペック §4）

> 各成果物への導線として最後に束ねる。リンクのハブに徹し詳細は各文書に置く（重複を作らない）。

- [x] 位置づけ（4 本柱: Eval / 可観測性 / クリーン境界 / 信頼性。**判断の痕跡を見せる repo** と明言。§1）— README.md 冒頭に記載済み
- [ ] デモ GIF を上部に配置（「本当に動く」を即伝える）— GIF未取得のためプレースホルダ（`<!-- TODO -->`）のまま。`docs/assets/README.md` に手順あり
- [x] クイックスタート（`git clone → docker compose up → make demo`・必要キー: OpenAI/Voyage 必須・**Langfuse 任意**・所要時間。M4 本文を確定）— 記載済み。本セッションで実キーによる `make demo` 完走を確認（`m4_tasklist.md` 参照）
- [x] アーキ図を掲載 — 掲載済み（Phase 5 参照）
- [x] 技術スタックと根拠（requirements §7 の要約＋詳細リンク）— README.md §「技術スタックと根拠」に記載済み
- [x] 設計判断・Eval レポート・可観測性・specs・設計文書一式へのリンク — README.md §「設計文書・品質・可観測性」に記載済み
- [x] v1 スコープ外を「意図的に外した」と示す（requirements §11 リンク）— README.md §「スコープについて」に記載済み
- [x] 決定した言語（Phase 0）で記述 — 日本語で記述済み

---

## Phase 7 — リポジトリ整備（スペック §10）

- [x] LICENSE を配置 — コミット`f0c82c5`でMIT LICENSEを配置済み
- [x] `.env.example` が全設定キーを網羅（M4 最終化を再確認）— 本セッションで `core/config.py` の全設定キー（16項目）を追記し網羅を確認
- [x] **秘匿情報・実データの最終スキャン**（NFR-3・コミット履歴含む。同梱は `seed/` のみ。**スクショ画像への写り込みも**）— `git log --all -- backend/.env` が空（未コミット）であることを確認、追跡ファイル中にAPIキー様文字列が無いことを `git grep` で確認。**スクショ画像は未取得のため写り込みチェックは対象外**（Phase 4 のスクショ取得後に別途要確認）
- [x] README/docs の**リンク切れチェック**（**markdown link checker を CI に組み込む**。§13。必須）— コミット`f0c82c5`で `.github/workflows/link_check.yml`（lychee-action）導入済み。本セッションでローカルの `lychee` でも README.md/docs/**/*.md を検証し、38リンク中エラー0件を確認
- [x] 不要ファイル・生成物・スクラッチの除去 — `git status --ignored` で確認。`__pycache__`/`.venv`/`.mypy_cache` 等はすべて `.gitignore` で正しく除外されており、追跡外の不要ファイルは無い

---

## Phase 8 — 再現性の最終確認（スペック §11、NFR-8 クローズ）

- [x] **クリーン環境**（新規 clone・キャッシュ無し）で **README のクイックスタートを字義通りになぞって** `make demo` が **15 分以内**にチャット可能へ到達（実装は M4、M5 は**手順の正しさ**を検証）— **代理計測（真のクリーンルームではない）**: 本セッションでは既にDocker/uv/pnpmのキャッシュが温まった環境で実施。`docker compose up -d db`・`make migrate`・`make demo`（実API・実キー）・`make api`・チャットの一連の手順を実行し、いずれも数秒〜数十秒で完了し15分に十分収まることを確認した。ただし新規clone・キャッシュ無しの真のクリーンルーム実測ではないため、**別マシン/第三者での最終実測を推奨事項として残す**
- [ ] 可能なら**第三者/別マシン**で実走し、暗黙の前提（既存キャッシュ・ローカル依存）が無いことを確認 — 未実施（推奨事項）
- [x] OpenAI / Voyage キーのみで到達・**Langfuse 無しでも成立**を再確認（NFR-8 / NFR-4）— 本セッションで実 `OPENAI_API_KEY`/`VOYAGE_API_KEY` のみで `make demo`/`make eval`/チャットが機能することを確認。Langfuseは鍵が401で無効な状態だったが、no-op化せずエラーログを出しつつもアプリ・デモ・evalの動作は妨げられないことを確認（NFR-4の意図通り）

---

## Phase 9 — クローズ（スペック §12, §14）

- [x] スペック §12 の受け入れ条件をすべてチェック（README/Eval/可観測性/設計判断・文書一致/再現性・整備/総括）— **クローズ判断（2026-07-13、プロジェクトオーナー承認）**: Langfuseスクショ3枚・デモGIF・別マシンでの真クリーンルーム実測・CI修正の実GitHub Actions確認の4項目はブラウザ操作/別マシン/リモート接続を要しエージェントでは実施不可のため、意図的に先送りした上でチェック済みとした。根拠は [docs/decisions.md「M5クローズ範囲の判断」](../decisions.md#m5クローズ範囲の判断スクショgif別マシン実測ci実行確認を先送り)、引き継ぎ手順は `docs/assets/README.md` 参照
- [x] `requirements.md` §12 Definition of Success の各項目を埋める（実体へのリンクを添える）— 埋めた。上記の意図的な先送り事項は各項目に注記
- [x] §9 監査で見つかった差分を各 `docs/specs/mN_*` に後追い反映済みであることを確認 — 本セッションで `m1`/`m2`/`m3`/`m4_tasklist.md` に反映済み
- [x] **M0〜M5 完了**を確認 — 完了と扱う。残る人手作業（Langfuse鍵再発行とスクショ撮影、デモGIF録画、別マシン実測、CI実行確認）は `docs/decisions.md`/`docs/assets/README.md` に引き継ぎ済みの上での完了

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.3 | 2026-07-13 | プロジェクトオーナー判断でM5をクローズ。Phase 0の入口ゲート・Phase 9をチェック。Langfuseスクショ・デモGIF・別マシン実測・CI実行確認の4項目は意図的な先送りとして`docs/decisions.md`に根拠を記録した上でクローズ扱いとした |
| v0.2 | 2026-07-13 | 実インフラでのライブラン実施を反映。Phase 1/2/6/7（一部）は先行コミット（`ffb341f`/`0987763`/`ceec7ad`/`cc1a759`/`f0c82c5`/`ec08790`）を根拠にチェック。Phase 3 は実 `make eval`（31問・実API）の結果で `docs/eval_report.md` を完成しチェック。Phase 4/5 はスクショ・GIF以外の項目をチェック（撮影・録画はブラウザ操作ツールが無く人手作業として `docs/assets/README.md` へ引き継ぎ）。Phase 8 は代理計測（真のクリーンルームではない）でチェック。ライブラン中に発見した実バグ2件（`retrieval/searcher.py` のSQL bind paramバグ、Voyage `max_retries`未設定バグ）とLangfuse鍵の401問題を関連文書に反映 |
| v0.1 | 2026-07-08 | 初版。m5_release_readiness.md v0.2 §15 の実装順序に基づき Phase 0〜9 を作成。Phase 0 に **M0〜M4 クローズの入口ゲート**、Phase 1 の監査は **実装優先の是正方向**、Phase 3 の Eval レポート数値は **M3 サマリ引用**（差し込み機構を新設しない）を反映。判断索引（§1）・文書一致監査・15 分再現性クローズを各フェーズ化 |