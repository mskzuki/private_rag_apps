# Private RAG Apps

ローカルのプライベートドキュメントコーパスを取り込み、ハイブリッド検索 + リランクで出典付き回答を返す RAG チャットアプリケーションです。

## 技術スタック
- **Backend**: Python 3.13, FastAPI, uvicorn
- **Store**: PostgreSQL + pgvector + pg_bigm, Alembic
- **AI**: Anthropic Claude, Voyage AI
- **Observability**: Langfuse

## クイックスタート (デモモード)
1. `cp .env.example .env` して各種APIキーを設定
2. データベースの起動: `docker compose up -d`
3. セットアップ: `make setup`
4. デモデータ取り込み: `make demo`
5. APIサーバー起動: `make api`

`GET http://localhost:8000/health` または `POST http://localhost:8000/api/chat` で疎通確認が可能です。
