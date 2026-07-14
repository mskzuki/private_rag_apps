# docs/assets/

M5 ショーケース仕上げ（`docs/specs/m5_release_readiness.md`）で必要な画像・GIFの置き場所。以下はいずれもブラウザでの手動操作が必要なため未取得（このリポジトリでの自動化作業の対象外）。取得後、本ディレクトリに配置し `docs/observability.md` / `README.md` 内の該当 `<!-- TODO -->` コメントを実画像への参照に差し替える。

| ファイル名 | 内容 | 参照元 |
|---|---|---|
| `langfuse_chat_trace.png` | chat トレース（`POST /api/chat` 1件。`condense → embed_query → retrieve(vector/fts) → rerank → generate` の span ツリー、各 span のトークン/コスト/レイテンシ、`metadata.ttft_ms` が見えるスクショ） | `docs/observability.md` |
| `langfuse_ingestion_trace.png` | ingestion トレース（`ingest_run` → `embed_documents`。同一コーパスを2回取り込み、2回目は無変更ファイルの `embed_documents` span が発生しないことが分かるスクショが望ましい） | `docs/observability.md` |
| `langfuse_eval_trace.png` | eval トレース（`make eval` 実行中の1トレース。`judge_faithfulness` / `judge_answer_relevance` の generation span とコストが見えるスクショ） | `docs/observability.md` |
| `demo.gif` | seed/demo モードでの質問→ストリーミング回答→出典カード表示（十数秒、seed の実挙動をそのまま録画。演出しない） | `README.md` |
| `demo_admin.gif`（任意） | 管理UIでの再取り込み/一覧表示のデモ | `README.md`（任意扱い） |

## 撮影前の前提条件

- Langfuse を有効化するには `backend/.env` に有効な `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` が必要（2026-07-13時点、現在設定されている鍵は401 Unauthorizedで無効。`docs/observability.md` の既知の問題を参照して鍵を再発行すること）。
- スクリーンショット・GIFに実データ・APIキー・トークンが写り込んでいないことを確認する（`docs/specs/m5_release_readiness.md` §10）。seedコーパス由来のデータのみが写る分には問題ない。
