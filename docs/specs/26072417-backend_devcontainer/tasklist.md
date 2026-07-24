# バックエンド専用 Devcontainer タスクリスト (docs/specs/26072417-backend_devcontainer/tasklist.md)

> 配置先: `docs/specs/26072417-backend_devcontainer/tasklist.md`
> 対応スペック: `docs/specs/26072417-backend_devcontainer/spec.md`（v0.1、以下「スペック」）
> 進め方: 上から順に実施。各タスクに対応スペックの節番号を付記。

---

## Phase 1 — `backend/.dockerignore`（スペック §3.6, §4.2）

- [x] `backend/.dockerignore` を新規作成し、`.env`・`.venv/`・キャッシュディレクトリ・`secrets/` を除外する
- [x] 追加後にビルドしたイメージに `.env` が含まれないことを確認する

## Phase 2 — `docker-compose.yml`（スペック §3.3, §4.1）

- [x] `api.volumes` に `./backend/tests:/app/tests` を追加（既存の `seed`/`src` マウントと同じ形式。丸ごとマウントはしない）
- [x] `docker compose config` で構文エラーが無いことを確認（出力はシークレットを含むためフルダンプせず、`volumes` 項目のみ確認する）

## Phase 3 — `Makefile`（スペック §3.2, §4.3）

- [x] `test` ターゲットの直前に `DB_HOST ?= localhost` を追加
- [x] `test` ターゲット内の `DATABASE_URL` の `localhost` を `$(DB_HOST)` に置換
- [x] レシピ行のインデントがタブのままであることを確認（Makefile構文要件。スペースにしない）
- [x] `DB_HOST` 未指定（ホスト実行）で `make test` が従来通り成功することを確認（208 passed）

## Phase 4 — `.devcontainer/devcontainer.json`（新規。スペック §3.1, §3.5, §4.4）

- [x] `.devcontainer/devcontainer.json` を新規作成
- [x] JSONC として解析可能なことを確認（コメント除去 + `json.loads`）

## Phase 5 — ドキュメント（スペック §4.5）

- [x] `AGENTS.md` §3 のディレクトリ構成に `.devcontainer/` を1行追加、変更履歴を更新
- [x] `README.md` に「VS Code Devcontainer で開発する場合（任意）」の短い節を追加

## Phase 6 — 検証・クローズ（スペック §5, §6）

- [x] （エージェント可）`docker compose config` が新volumeを含めてエラー無く解決する
- [x] （エージェント可）`backend/tests/` 配下に加えた変更が、再ビルド無しに実行中の `api` コンテナ内へ反映されることを確認
- [x] （エージェント可）`DB_HOST=db` 注入時、composeネットワークに参加しているコンテナから `db:5432` の `rag_test` へ接続するpytest相当コマンド（スペック §3.5）が成功することを確認（207/208。残り1件は本タスクと無関係の既存環境問題。tasklist末尾の実施メモ参照）
- [ ] （人間のVS Code実機が必要）「Reopen in Container」のUXそのもの、拡張機能のインストール・有効化、`python.defaultInterpreterPath` のステータスバー反映を確認
- [x] `make lint` / `make test`（ホスト実行）が通ることを確認（208 passed、lint All checks passed!）
- [x] スペック §6 の受け入れ条件を全てクローズ（人間実機確認を除く）

---

## 実施メモ（検証時に判明した、本タスクとは別件の既存環境の問題）

- **`docker-compose.yml` 変更後、`docker compose up -d api` で `redis` サービスがポート競合により起動できず、その巻き添えで実行中の `api` コンテナが一時停止した。** 原因はこのプロジェクトの `redis`（ポート6379）とは無関係な、別プロジェクト（`m9-google-drive-ingestion-redis-1`）の残存コンテナが同じホストポートを7日間占有していたこと。`docker compose up -d --no-deps api` で `api` のみ復旧した。この `redis` ポート競合自体は本タスクの変更が原因ではなく、対応もスコープ外（別途ユーザー判断が必要）。
- **既存イメージが `pyproject.toml` に対して古く、`arq` パッケージが不足していた（`ModuleNotFoundError: No module named 'arq'`）ため `api` がリクエストに応答できていなかった。** `docker compose build api` で再ビルドし解消（本タスクの変更が原因ではなく、`arq` 依存追加後にイメージが再ビルドされていなかったための既存の問題）。
- 上記の `redis` ポート競合により、コンテナ内から `db:5432` 経由のpytest実行時に `test_worker_gdrive_integration.py` の1件のみ失敗した（このプロジェクトの `redis` サービスが未起動のため）。ホスト実行の `make test` では同じ理由で偶然別プロジェクトの残存 `redis` コンテナに到達できてしまい208件全て成功しており、`DB_HOST`/`db`ホスト名の仕組み自体は正しく機能している。
