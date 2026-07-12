# Private RAG Apps

ローカルのプライベートドキュメントコーパスを取り込み、ハイブリッド検索 + リランクで出典付き回答を返す RAG チャットアプリケーションです。

## 技術スタック
- **Backend**: Python 3.13, FastAPI, uvicorn
- **Store**: PostgreSQL + pgvector + pg_bigm, Alembic
- **AI**: OpenAI GPT, Voyage AI
- **Observability**: Langfuse

## クイックスタート (デモモード)

クリーンな環境（`git clone` 直後）から 15 分以内にチャットできる状態を目指しています。必須の外部キーは **OpenAI** と **Voyage** のみです（Langfuse は任意 — 未設定でも計装が no-op になり、アプリ・デモ・eval は問題なく動作します）。

1. `cp backend/.env.example backend/.env` して `OPENAI_API_KEY` / `VOYAGE_API_KEY` を設定
2. `make setup`（uv sync / pnpm install / `.env` 生成（既にあれば何もしない）/ PostgreSQL(pgvector+pg_bigm) コンテナ起動）
3. `make demo`（マイグレーション適用 → seed コーパス（`seed/corpus/`）を取り込み。2回目以降は無変更ファイルをスキップするため高速）
4. 別ターミナルで `make api`（FastAPI起動、http://localhost:8000）
5. 別ターミナルで `make web`（Next.js起動、http://localhost:3000 でチャット画面）

`GET http://localhost:8000/health` または `POST http://localhost:8000/api/chat` で疎通確認が可能です。データ管理画面（ソース一覧・再取り込み・インデックス初期化）は http://localhost:3000/sources から利用できます。

### 自分の文書を取り込みたい場合

`make demo` は常に `seed/corpus` を取り込み対象にします（`.env` の `CORPUS_DIR` を上書きしても影響を受けません）。自分の文書を試したい場合は次の手順で切り替えてください。

1. データ管理画面またはAPI（`DELETE /api/index`）でインデックスを初期化する（seedとの混在を避けるため）
2. `backend/.env` の `CORPUS_DIR` を自分の文書ディレクトリに変更する
3. `make ingest`（`trigger=cli` で増分取り込み。`FORCE_DELETE=1` を付けると削除安全弁をバイパスできる）

## OpenAPI仕様書の生成

```bash
make openapi
```

`backend/openapi.json` にOpenAPI仕様書が出力されます。
