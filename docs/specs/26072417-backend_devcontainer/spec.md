# Private RAG Apps — バックエンド専用 VS Code Devcontainer スペック (docs/specs/26072417-backend_devcontainer/spec.md)

> 配置先: `docs/specs/26072417-backend_devcontainer/spec.md`
> 対象: 開発体験の改善（RAG挙動に影響しない開発ツーリング。特定マイルストーンには対応しない）
> 上位ドキュメント: 構成=`docs/architecture.md`、規約=`AGENTS.md`(v0.17)。
> **矛盾時の優先順位**（AGENTS.md冒頭）: 本スペック > AGENTS.md > 一般慣習。

---

## 1. 目的と背景

`backend/`（FastAPI）の開発を VS Code の「Reopen in Container」で行えるようにする。狙いは2つ:

1. **環境差異の解消・再現性**: Python は `pyproject.toml` が `>=3.12` を要求する一方、実運用（Dockerfile・CI）は 3.13 に固定されており、`.python-version` は存在しない。Node/pnpm に至ってはバージョンがリポジトリ内どこにも固定されていない。
2. **CI/ローカルの環境統一**: 現状 CI（`.github/workflows/eval.yml`）は `docker-compose.yml` の `db` サービスのみを再利用しており、frontend の CI ジョブは存在しない。

対象範囲は当初 backend+frontend 統合を検討したが、ユーザーの判断で **backend のみ** に絞った。frontend（`frontend/`）は引き続きホストで `pnpm dev`（`make web`）を実行し、この devcontainer の対象外とする。

AGENTS.md には devcontainer への言及が無いため、AGENTS.md §11「仕様に無い機能を勝手に追加しない」に従い、実装前に本スペックを作成する。

---

## 2. スコープ

### 2.1 In scope

- `.devcontainer/devcontainer.json` の新規作成（`dockerComposeFile` + `service: "api"` で既存の `api` サービスにアタッチ）
- `backend/.dockerignore` の新規作成（副次的に発見した既存の問題の修正。§3.6）
- `docker-compose.yml`: `api` サービスへ `backend/tests` のバインドマウントを追加
- `Makefile`: `test` ターゲットの DB ホストを `DB_HOST` 変数化
- 上記に伴う `README.md` / `AGENTS.md` への小さな追記

### 2.2 Out of scope

| 項目 | 理由 |
|---|---|
| frontend（Node/pnpm）を devcontainer に含める | ユーザーの判断により明示的に対象外。`frontend/` はホストで `make web` のまま |
| `ingest_worker`（M9 ARQ worker）を devcontainer 内から起動可能にする | docker-outside-of-docker ツーリングの追加が必要になり、この最小構成の範囲外（§3.4 で許容する既知の制約として記録） |
| `docker-compose.yml` への新規サービス追加 | 既存 `api` サービスへのアタッチのみで済ませる（§3.1） |
| `backend/docker/api/Dockerfile.local` の変更 | CI・`make api` 等の他フローもこの Dockerfile からビルドするため、devcontainer 専用ツーリング（git 等）は devcontainer Features で追加し、共有 Dockerfile は変更しない |
| `./backend:/app` の丸ごとバインドマウント | image build 時に作成された `/app/.venv` を、ホスト側 `backend/`（`.venv` が無いか、macOS 等で Linux コンテナと非互換）で上書きしてしまうため。既存パターン通り個別マウントのみ追加する |
| コンテナ内で文字通り `make test`/`make lint` コマンドを使えるようにする | §3.5 で実機確認した通り、現イメージには `make` バイナリが存在せず、ルート `Makefile` も `/app` 配下には無い。今回のスコープでは対応しない |
| リポジトリ全体を別マウントポイント（例: `/workspace`）に追加し git/Makefile を使えるようにする | `.venv` の二重管理・ターミナル既定 cwd の不一致など複雑さが増すため見送り、§3.5 の制約として受け入れる |

---

## 3. 設計判断

### 3.1 新規 Compose サービスを作らず `api` に直接アタッチする

`dockerComposeFile: "../docker-compose.yml"` + `service: "api"` を使う。これにより「`make api` が作るコンテナそのものにエディタをアタッチするだけ」という設計意図を機械的に保証する。`api` の `depends_on: [db, redis]` により、devcontainer 起動時に `db`/`redis` も自動的に起動する（追加の `runServices` 設定は不要）。`overrideCommand: false` により、VS Code 既定の `sleep infinity` で `Dockerfile.local` の `CMD`（`uvicorn --reload`）を上書きしない。`make api` 実行時と全く同じプロセスがフォアグラウンドで動き続ける。

devcontainer専用のツール（git 等）は共有の `Dockerfile.local` を変更せず、`devcontainer.json` の **Features** で後乗せする。

### 3.2 既知の問題1: `make test` のホスト名ハードコード

`Makefile` の `test` ターゲットは `DATABASE_URL` に `localhost` を直書きしている。ホスト実行時は `db` サービスの公開ポート（`5432:5432`）経由で機能するが、devcontainer で `api` コンテナにアタッチして実行すると、`localhost` は `api` コンテナ自身を指し 5432 番ポートには何も listen していないため接続エラーになる。

**対応**: `DB_HOST ?= localhost` という変数を導入し、`test` ターゲットの `DATABASE_URL` 内 `localhost` を `$(DB_HOST)` に置き換える。ホスト実行時は既定値 `localhost` のままで**挙動を一切変えない**。devcontainer 側は `remoteEnv` で `DB_HOST=db` を統合ターミナルの環境に注入する。CI（`.github/workflows/eval.yml`）は `make test` を一切呼び出さない（`docker compose up -d db` 後は `alembic`/`evals` を `uv run` 経由で直接実行するのみ）ため、この変更は CI に影響しない。

**追記（2026-07-25）**: `Makefile` の `test`（および `migrate`/`ingest`/`demo`/`ingest-gdrive`/`lint`/`fmt`/`eval*`）がホスト直接実行から `docker compose run --rm --build api ...` 経由のコンテナ実行に統一されたことに伴い、この `DB_HOST` 変数は撤去した。コンテナ実行では `DB_HOST` は `docker-compose.yml` の `api` サービス定義（`environment: DB_HOST=db`）のみで完結し、`test` ターゲットは接続文字列全体ではなく `DB_NAME=rag_test` のみを `-e` で上書きする（`core/config.py` の `DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASS`/`DB_NAME` 分解による）。devcontainer 側の `remoteEnv: DB_HOST=db`（§3.5 で説明する、devcontainer 内で直接 `uv run pytest` 等を叩く場合の設定）はこの変更と無関係でそのまま維持する。詳細は `docs/decisions.md`「backend Makefile ターゲットのコンテナ経由への統一」を参照。

### 3.3 既知の問題2: `backend/tests/` が未マウント

`api` サービスの `volumes` は `./backend/seed:/app/seed` と `./backend/src:/app/src` のみで、`backend/tests/` は含まれない。テストファイルは `COPY . .` によりイメージビルド時に `/app/tests` へ焼き込まれるだけなので、devcontainer 内でテストファイルを編集してもコンテナ内には反映されず、確認のたびにイメージの再ビルドが必要になってしまう。

**対応**: `./backend/tests:/app/tests` を `api` の `volumes` に追加する。`./backend:/app` のような丸ごとマウントは行わない（2.2 参照。`/app/.venv` を壊すため）。`src`/`seed` と同じ「ディレクトリ単位の個別マウント」パターンを踏襲する。

### 3.4 既知の問題3（受容する制約）: `ingest_worker` を devcontainer 内から起動できない

`make worker`（M9 の ARQ worker）は Docker Compose の別サービス（`ingest_worker`）をコンテナとして起動するコマンドであり、devcontainer 内（＝`api` コンテナの中）から新たに docker compose を操作するには docker-outside-of-docker の追加が必要になる。これは最小構成のこの devcontainer では明示的にスコープ外とする。

**影響**: Google Drive の取り込み（`POST /api/ingest/gdrive` 経由の非同期取り込み）を試す場合、`make worker` は引き続き**ホスト側の別ターミナル**から実行する必要がある。CLI 経由の取り込み（`make ingest-gdrive`）は worker/Redis 不要なため、この制約の影響を受けない。

**追加の相互作用**: devcontainer の `shutdownAction: "stopCompose"` は、VS Code がこの devcontainer から切断される際に、起動した compose サービス（`api` と、その依存先である `db`/`redis`）を停止する（`db_data` ボリューム自体は削除されない）。ホスト側で `make worker`（`ingest_worker`）を実行中に devcontainer を閉じると、依存する `db`/`redis` も一緒に止まる可能性がある。

### 3.5 既知の問題4（受容する制約）: git が実質使えず、`make` 自体も使えない

実装前の確認として、既にビルド済みの `private_rag_apps-api` イメージを直接調査した結果、`make`・`git`・`curl` 等の基本ツールが未インストールと確認した（`python:3.13-slim` ベースのため）。加えて次の2点により、Features でツールを追加するだけでは解決しない:

- **git**: `workspaceFolder` は `/app`（`backend/` 相当）で、`.git` はどこにもマウントされない（§2.2「丸ごとマウントをしない」の帰結）。git バイナリを Feature で入れても対象リポジトリが無いため、VS Code の Source Control パネルや統合ターミナルの `git` コマンドはこのリポジトリに対して機能しない。
- **make**: ルートの `Makefile` は `backend/`（Docker ビルドコンテキスト）の外にあるため、個別マウント（`seed`/`src`/`tests`）にも含まれず `/app` 配下からは到達できない。`make` バイナリ自体も無い。

**結論**: git 操作は引き続きホスト側（別ターミナル、または別の VS Code ウィンドウをリポジトリルートで開く）で行う。devcontainer 内でのテスト・lint 実行は、`make test`/`make lint` が展開する実体を直接叩く:

```bash
DATABASE_URL="postgresql+psycopg://rag_user:rag_pass@db:5432/rag_test" uv run pytest
uv run ruff check .
uv run mypy .
```

（`/app` が既に `backend/` 相当のため `cd backend &&` は不要。`DB_HOST=db` 注入後の `make test` と機能的に等価）

リポジトリ全体を別マウントポイントに追加すれば両方解決できるが、`/app/.venv` との二重管理・ターミナル既定 cwd の不一致など複雑さが増すため見送る。必要になった場合は別途スペックを提案する。

### 3.6 副次的に発見した既存の問題: `backend/.dockerignore` が無い

調査中に、`backend/.dockerignore` が存在しないことが判明した。`Dockerfile.local` の `COPY . .` はビルドコンテキスト（`./backend`）をそのままコピーするため、`docker compose build api`（`make api`）を叩くたびに、実際の `backend/.env`（本物の API キー入り）がイメージのレイヤーに焼き込まれる。ホストに留まっている限り実害は無いが、イメージを push・共有したり `docker save`/`docker history` されると漏れる構造であり、AGENTS.md §11 の「シークレットをコミットしない」と同種の懸念があるため、本スペックの範囲に含めて修正する。

**対応**: `backend/.dockerignore` を新規作成し、`.env`・`.venv/`・キャッシュディレクトリ・`secrets/` を除外する（§4.2）。

---

## 4. 実装内容

### 4.1 `docker-compose.yml`

`api.volumes` に `./backend/tests:/app/tests` を1行追加（§3.3）。

### 4.2 `backend/.dockerignore`（新規）

```
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
secrets/
```

### 4.3 `Makefile`

`DB_HOST ?= localhost` を追加し、`test` ターゲットの `localhost` を `$(DB_HOST)` に置換（§3.2）。

### 4.4 `.devcontainer/devcontainer.json`（新規）

- `dockerComposeFile: "../docker-compose.yml"` + `service: "api"` + `workspaceFolder: "/app"`
- `overrideCommand: false`（§3.1）
- `shutdownAction: "stopCompose"`（§3.4 の相互作用に注意）
- Features: `ghcr.io/devcontainers/features/git:1`、`ghcr.io/devcontainers/features/common-utils:2`（§3.5 の制約を軽減する範囲での導入。git は動作こそしないが `git --version` 程度の確認や将来の拡張余地として維持する）
- `remoteEnv: {"DB_HOST": "db"}`（§3.2）
- `forwardPorts: [8000]`
- VS Code 拡張機能: `ms-python.python`, `charliermarsh.ruff`（AGENTS.md §6 の lint/format=ruff）, `ms-python.mypy-type-checker`（AGENTS.md §6 の型=mypy）
- 設定: `python.defaultInterpreterPath: "/app/.venv/bin/python"`（`Dockerfile.local` が uv 経由で作る venv の場所）

### 4.5 ドキュメント

- `AGENTS.md` §3 のディレクトリ構成に `.devcontainer/` を1行追加
- `README.md` に「VS Code Devcontainer で開発する場合（任意）」の短い節を追加し、本 spec.md へリンクする

---

## 5. 検証方針

エージェント単独で確認可能:
- `docker compose config` によるcompose構文検証（**シークレットが解決された状態で出力されるため、フルダンプはせず必要な項目のみ見る**）
- `backend/tests` マウントが編集を即時反映すること
- `Makefile` の `DB_HOST` 既定動作（ホスト実行）が変わっていないこと
- `DB_HOST=db` 注入時に `db:5432` への接続が成功すること（§3.5 の等価コマンドで確認）
- `.devcontainer/devcontainer.json` が有効な JSONC として解析できること
- `backend/.dockerignore` 追加後、新規ビルドしたイメージに `.env` が含まれないこと

人間による VS Code 実機確認が必要:
- 「Reopen in Container」の UX 自体
- 拡張機能が実際にマーケットプレイスからインストールされ有効化されること
- `python.defaultInterpreterPath` がステータスバーに反映されること

---

## 6. 受け入れ条件

- [ ] `docker compose config` が新しい `docker-compose.yml` でエラー無く解決できる
- [ ] `backend/tests/` 配下の編集が、再ビルド無しに実行中の `api` コンテナへ反映される
- [ ] `make test` がホスト実行（`DB_HOST` 未指定、既定 `localhost`）で従来通り成功する
- [ ] `DB_HOST=db` を注入した状態で、`db:5432` の `rag_test` に対する pytest 相当のコマンド（§3.5）が成功する（コンテナ内での文字通りの `make test` はスコープ外）
- [ ] `.devcontainer/devcontainer.json` が有効な JSONC として解析できる
- [ ] `backend/.dockerignore` 追加後にビルドしたイメージに `.env` が含まれない
- [ ] `AGENTS.md` §3・`README.md` への追記が完了している
- [ ] `make lint` / `make test`（ホスト実行）が通る

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.1 | 2026-07-24 | 初版 |
