# 設計判断の索引 (decisions.md)

> このファイルは「なぜその設計にしたか（判断・代替案・根拠）」の索引に徹する。
> 「何を採用したか（技術スタック一覧・スコープ定義）」は `requirements.md` §7/§11 が正であり、ここでは重複させない。
> 各項目の詳細・実装への反映は、リンク先の一次ソース（`docs/architecture.md`、`docs/db_design.md`、`docs/specs/mN_*.md`）を参照。
> 単一索引ファイル形式（1決定1ファイルのADR群にはしない。M5スペック§14の既定どおり）。

---

## データストア

### pgvector 単一 DB（vs Qdrant）

- **決定**: ベクトル検索・全文検索・リレーショナルデータを PostgreSQL 1 箇所に集約する。
- **背景/代替案**: 専用ベクトル DB（Qdrant 等）との比較検討を経た上での選択。
- **根拠**: 日本語ハイブリッド検索が1クエリ（RRF融合SQL）で完結し、整合性をFK/トランザクションに委ねられる。運用対象が1つで済み、クリーン環境15分クイックスタート（NFR-8）に寄与する。スケール時は外部ベクトルDBへの切り出し余地を残す。
- **詳細**: [architecture.md §11](architecture.md#11-主要な設計判断要点)

### pg_bigm（vs PGroonga）

- **決定**: 日本語全文検索エンジンに pg_bigm（bigram）を採用。
- **背景/代替案**: PGroonga は検索品質・スコアリングで優れるが、専用エンジン依存が重い。
- **根拠**: 拡張1つで軽量に動かせ、MVPを動かすこと優先の判断に合致。差し替え時の影響範囲（拡張定義・全文検索クエリ・全文インデックス）は局所化されている。
- **詳細**: [db_design.md §2](db_design.md#2-拡張extensionsと日本語全文検索の選択)

---

## 検索パイプライン

### RRF 融合

- **決定**: ベクトル候補と全文候補を RRF（Reciprocal Rank Fusion）で統合する。
- **根拠**: スコアスケールの異なるベクトル類似度と全文一致度を、順位ベースで安全に統合できる。
- **詳細**: [architecture.md §11](architecture.md#11-主要な設計判断要点) / [db_design.md §6](db_design.md#6-ハイブリッド検索クエリrrf-融合の例)

### リランクを最終段に

- **決定**: 一次検索（ベクトル+全文融合）で再現率を稼ぎ、精度はVoyage rerank-2.5による最終段リランクで担保する。
- **根拠**: 一次検索とリランクの役割分担が明確な定番構成。
- **詳細**: [architecture.md §11](architecture.md#11-主要な設計判断要点)

---

## 運用・スコープ

### ジョブキューを持たない（CLI + BackgroundTasks）

- **決定**: Redis/ARQ/Celery等のジョブキューを導入せず、取り込みはCLI + FastAPIのプロセス内BackgroundTasksで賄う。
- **背景/代替案**: SaaSコネクタ導入時にはARQ/Redis再検討が必要になる想定。
- **根拠**: SaaS同期がv1スコープ外である以上、キューを持つ運用コストに見合わない。
- **詳細**: [architecture.md §11](architecture.md#11-主要な設計判断要点)

### 開発DB / テストDBの分離（rag_dev / rag_test）

- **決定**: 開発・デモ用DBを `rag_dev`、自動テスト用DBを `rag_test` として、同一PostgreSQLインスタンス内の別データベースで運用する。
- **背景/代替案**: テスト用DBを強制する仕組み（`conftest.py`等）が無いまま `make test` を実行し、`DELETE /api/index` や取り込みテストの全置換ロジックがデモ用DBのデータを消してしまう事故がM5・M6で計2回発生した。
- **根拠**: 開発/デモ用データを自動テストの破壊的操作から物理的に隔離する。同一Postgresインスタンス内の別DBに留めることで、単一Postgres集約という既存方針（[pgvector単一DB](#pgvector-単一-dbvs-qdrant)）とも矛盾しない。
- **詳細**: [architecture.md §9](architecture.md#9-設定とシークレット)。`rag_dev`/`rag_test`を実DBとして作成済み。`conftest.py`等の新規抽象化はせず、`make test`がDATABASE_URLを`rag_test`に固定する形で分離を実装（既存の`demo:`ターゲットと同じ環境変数上書きパターン）。

### SaaS コネクタ・OAuth をスコープ外

- **決定**: Notion/Slack/Drive等のSaaSコネクタ、OAuth認証・トークン管理、ACL権限考慮検索、エージェンティックRAG、PDF/Officeパースはv1スコープ外。
- **根拠**: 単一ユーザー・ローカルコーパスに絞ることで、Eval・可観測性・クリーンな境界という技術ショーケースの核に集中する。
- **詳細**: [requirements.md §11](requirements.md#11-将来拡張)

### assistant-ui 採用（ChatKit 不採用）

- **決定**: チャットUIランタイムに assistant-ui を採用。
- **背景/代替案**: OpenAI ChatKit は UIスクリプトが特定ベンダーのCDNに依存し、バックエンド非依存構成・公開リポジトリという方針に不向きなため不採用。
- **根拠**: バックエンド非依存のカスタムランタイムで自前SSEを直接受けられ、shadcn/uiベースでコードを資産化できる。streaming/auto-scroll/retry等の定番UXを自前実装しない（AGENTS.md §11 DO NOT）。
- **詳細**: [architecture.md §11](architecture.md#11-主要な設計判断要点) / [requirements.md §7](requirements.md#7-技術選定決定と根拠)

---

## 会話・ストリーミング（M2）

### 会話の done 一括保存

- **決定**: user/assistantメッセージは `done` イベント送出時に1トランザクションで一括保存する（受信直後のuser先行保存はしない）。
- **根拠**: user先行保存だと生成失敗時に「回答のないuser行」が履歴に残り、次ターンのcondenseが宙ぶらりんな発話を含む履歴を見てしまう。done一括保存なら履歴は常にuser↔assistantの整合した交互列に保たれる（NFR-6のfaithfulnessにも寄与）。DDL変更も不要になる。
- **詳細**: [m2_streaming_and_history.md §4.2](specs/m2_streaming_and_history.md#42-会話の永続化done-一括保存)

### citations 生成前送出

- **決定**: `citations` SSEイベントはrerank完了直後・最初の`token`より前に1度だけ送る。
- **根拠**: 回答本文中の `[n]` と出典カードの対応は生成前（リランク後のtop_k確定時点）で決まるため、生成前に送ることでクライアント側の対応付けが単純になる。
- **詳細**: [m2_streaming_and_history.md §4.5](specs/m2_streaming_and_history.md#45-引用citationsの番号付けとペイロード)

---

## Eval（M3）

### path レベルの正解ラベル（chunk_id を使わない）

- **決定**: ゴールデンデータセットの正解は `sources.path`（+任意heading）で表現し、chunk UUIDを使わない。
- **背景**: 更新戦略が「source更新時にchunksを全削除→再挿入」（全置換）のため、再取り込みのたびに`chunks.id`が変わる。
- **根拠**: chunk UUIDを正解に固定するとデータセットが再取り込みで壊れる。pathレベルにすることでチャンキング戦略を変えてもデータセットを作り直さずに再評価できる（M3の主眼＝チャンキング変更の回帰検出に必須）。
- **詳細**: [m3_eval_expansion.md §3.3](specs/m3_eval_expansion.md#33-正解の粒度--文書pathレベルchunk_id-を正解に使わない)

### EVAL_TOP_K 分離・検索ハード/生成ソフトのゲート

- **決定**: 評価専用の取得件数 `EVAL_TOP_K`（既定12）を本番の生成用 `top_k=8` とは別パラメータとして持つ。ゲートは検索指標=ハード（閾値超で `make eval` を失敗させる）、生成指標=ソフト（劣化を警告するのみ）。
- **根拠**: Recall@10を測るには本番のtop_k=8では不足する。また生成指標はLLM-as-judgeのブレを含むため、これをハードゲートにすると偽陽性が多発する。検索指標を主・生成指標を従とすることでCIの実用性を保つ。
- **詳細**: [m3_eval_expansion.md §7.3](specs/m3_eval_expansion.md#73-ゲート方針検索は硬め生成は柔らかめ)

---

## 増分再取り込み（M4）

### 埋め込み事前・短トランザクション全置換

- **決定**: 更新分は「埋め込みをトランザクション外で事前計算 → 全チャンク揃ってからdelete+insertを1つの短いトランザクションで実行」の順で全置換する。
- **根拠**: 埋め込み計算（外部API呼び出し・低速）をトランザクションの外に出すことでDBロック時間を最小化する。埋め込み失敗時はDBに触れず旧chunksを維持したままそのファイルをスキップできる。
- **詳細**: [m4_ingestion_and_demo.md §4.2](specs/m4_ingestion_and_demo.md#42-更新の全置換埋め込みは事前db-反映は短いトランザクション)

### 削除安全弁

- **決定**: 走査で消えたsourceは`deleted_at`を立てるが、生存source数に対する走査ヒット数の比率が`INGEST_DELETE_GUARD_RATIO`（既定0.5）を下回る、または走査0件の場合は削除フェーズのみを中断する（追加/更新は適用済みのまま）。`FORCE_DELETE`でバイパス可能。
- **根拠**: マウント外れ等でコーパスディレクトリが一時的に空/縮小した際に、誤って大量のsourceを削除してしまう事故を防ぐ。
- **詳細**: [m4_ingestion_and_demo.md §4.3](specs/m4_ingestion_and_demo.md#43-削除反映と安全弁)

### advisory lock 排他

- **決定**: 取り込みの多重起動は`ingest_runs`のrunning行の存在で抑止しつつ、開始の原子性はPostgresのadvisory lockで担保する。
- **根拠**: running行チェックだけでは複数リクエストが同時にチェックを通過するレースが起きうる。advisory lockで開始判定を原子化することで、ジョブキューを持たずに排他制御を実現する（「ジョブキューを持たない」判断と両立）。
- **詳細**: [m4_ingestion_and_demo.md §4.4](specs/m4_ingestion_and_demo.md#44-多重実行の抑止running-行で担保advisory-lock-は開始の原子性のみ)

---

## ショーケース仕上げ（M5）

### M5クローズ範囲の判断（スクショ・GIF・別マシン実測・CI実行確認を先送り）

- **決定**: M5の受け入れ条件（`m5_release_readiness.md` §12）のうち、(1) Langfuseトレーススクショ3枚、(2) デモGIF、(3) 別マシンでの真クリーンルーム15分実測、(4) `eval.yml`のCI修正を実GitHub Actions上で確認、の4項目はプロジェクトオーナーの判断で意図的に先送りし、それ以外（`docs/eval_report.md`の実データ完成・`docs/decisions.md`・設計文書⇄実装の一致監査・可観測性の実装とドキュメント化・README・LICENSE/`.env.example`/リンクチェック等のリポジトリ整備）をもってM5完了、M0〜M5完了と扱う。
- **背景**: 上記4項目はいずれもブラウザ操作（スクショ撮影・GIF録画）・別マシン・GitHubリモート接続（本リポジトリには`git remote`/`gh`が未設定）など、エージェントの実行環境の外側にある人手/インフラ依存の作業であり、自動化では完了できない。
- **根拠**: スペックの★（最高優先度）項目である`docs/decisions.md`（判断の索引＝§1「レビュアーが見るのは判断の痕跡」に直結）と`docs/eval_report.md`（Evalレポート）は共に実データ・実行結果に基づき完成しており、レビュアーが判断の痕跡を追える状態は達成済み。残り4項目は「見せ方」の仕上げ（スクショ・GIF）と「検証の網羅性」（別マシン・実CI）であり、技術的信頼性の核ではない。ツール制約下で無理に代替措置（画像の捏造等）を取るより、正直に「未実施」と明記し人手作業として引き継ぐ方が、本リポジトリが掲げる誠実性（Evalの数値を捏造しないのと同じ精神）に合致すると判断した。
- **詳細**: [m5_release_readiness.md §12](specs/m5_release_readiness.md#12-受け入れ条件-definition-of-success-のクローズ) / [m5_tasklist.md Phase 9](specs/m5_tasklist.md) / [assets/README.md](assets/README.md)（未実施4項目の引き継ぎ手順）

---

## Google Drive 取り込み（M9）

### サービスアカウント認証（vs OAuth）

- **決定**: Google Drive へのアクセス認証にサービスアカウントを採用する。OAuth（installed app フロー）は採用しない。
- **背景/代替案**: requirements.md が SaaS コネクタ・OAuth を v1 スコープ外とした理由は「単一ユーザー MVP に OAuth フロー（アプリ登録・コールバック・トークン更新）は過剰」。OAuth を使う場合、この除外理由がそのまま該当してしまう。
- **根拠**: サービスアカウントは対象フォルダをメールアドレスに共有するだけで動作し、同意画面・コールバック・トークンリフレッシュのロジックを一切実装せずに済む。単一固定フォルダという M9 のスコープと相性が良く、CLI から何度でも非対話で再実行できる。
- **詳細**: [m9_google_drive_ingestion.md §3.4/§4.3](specs/m9_google_drive_ingestion.md#34-oauth-を使わない)

### identity key の一般化（source_type + external_id）

- **決定**: `sources.path` の単一 `UNIQUE` 制約を廃止し、`source_type`（`local_fs`/`google_drive`）ごとのパーシャルユニークインデックスに置き換える。ローカルは `path`、Drive は `external_id`（Drive file ID）を一意性キーとする。
- **背景/代替案**: Google Drive は同一フォルダ内でのファイル名重複を許容するため、Drive ソースを疑似パスで一意識別することはできない。`external_id`（Drive file ID）はリネーム・フォルダ移動があっても不変であり、識別キーとして頑健。
- **根拠**: パーシャルユニークインデックスにより、ローカルソースの一意性制約は実質的に変更せず完全後方互換を保ちながら、Drive ソースに適した識別方式を追加できる。
- **詳細**: [m9_google_drive_ingestion.md §3.2/§4.2](specs/m9_google_drive_ingestion.md#32-identity-key-の一般化後方互換を維持)

### ARQ/Redis は API 経由トリガの堅牢化のみに限定

- **決定**: ARQ/Redis ジョブキューを導入するが、対象は Google Drive 取り込みの **API 経由トリガ（`POST /api/ingest/gdrive`）のみ**。CLI 経由トリガ・ローカル取り込みは引き続き同期実行/`BackgroundTasks` のまま変更しない。スケジューリング（定期自動同期）は行わない。
- **背景/代替案**: requirements.md §11 は将来 SaaS コネクタ導入時に ARQ/Redis の再検討を想定していたが、「ジョブキューを持たない」という既存決定（本ファイル前掲）はスコープの大きな逆転になる。ユーザーとの合意により、目的を「定期自動同期」ではなく「API プロセスの再起動をまたいだ再試行・状態非依存の実行」に限定した。
- **根拠**: 外部 API 呼び出し（Drive API）を伴う処理はローカル FS 読み込みと異なり、ネットワーク起因の失敗・長時間化の可能性があり、プロセス再起動への耐性の価値が高い。一方 CLI 経由トリガはコマンド実行プロセス自体が寿命であり、BackgroundTasks 由来の弱点（API プロセス再起動でタスクが失われる）が元々当てはまらないため、変更する理由がない。
- **詳細**: [m9_google_drive_ingestion.md §3.3/§4.6](specs/m9_google_drive_ingestion.md#33-arq-は api-経由トリガの堅牢化のみに使う)

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.5 | 2026-07-17 | M9（Google Drive フォルダ取り込み）スペック起票に伴い、サービスアカウント認証・identity key の一般化・ARQ/Redis を API 経由トリガに限定する判断を追記 |
| v0.4 | 2026-07-14 | 開発DB/テストDB分離（rag_dev/rag_test）を実装反映。DB作成・`make test`のDATABASE_URL固定まで完了 |
| v0.3 | 2026-07-14 | 開発DB/テストDB分離（rag_dev/rag_test）の決定を追記。M5・M6で計2回発生した、テスト実行がデモ用DBを誤って初期化する事故が背景 |
| v0.2 | 2026-07-13 | M5クローズにあたり、スクショ/GIF/別マシン実測/CI実行確認を意図的に先送りした判断を追記 |
| v0.1 | 2026-07-13 | 初版。M5 Phase 2。既存specs/requirements/architecture/db_designの判断を集約 |
