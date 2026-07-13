# 可観測性（Langfuse トレース）

> **スクリーンショットは未収録**: 3枚（chat / ingestion / eval）は撮影にブラウザ操作を要するため、[M5クローズ時の判断](decisions.md#m5クローズ範囲の判断スクショgif別マシン実測ci実行確認を先送り)により意図的に先送りしています。構成・span名は実装済みコードから確認済みです。撮影手順は [docs/assets/README.md](assets/README.md) 参照。
>
> **現状の既知の問題（2026-07-13時点）**: `backend/.env` に設定されている Langfuse の鍵ペアで API に到達すると、EU/US いずれのホスト（`https://cloud.langfuse.com` / `https://us.cloud.langfuse.com`）でも `401 Unauthorized` が返る（リージョンの問題ではなく鍵自体が無効/失効している可能性が高い）。そのため本セッションでは実トレースの取得・スクリーンショット撮影ができていない。鍵を再発行・更新すれば、以下の計装（`@observe()`）はコード上は正しく動作する状態になっている（`core/config.py` が `.env` の値を `os.environ` へ反映する形に修正済み。以前はこの反映が無く、鍵を設定しても計装が有効化されない別のバグがあった）。

## Langfuse を有効化する

`LANGFUSE_*` はデフォルトで未設定であり、その場合すべての計装は **no-op**（アプリ・デモ・eval の動作を妨げない。requirements NFR-4/NFR-8）。トレースを見るには `backend/.env` に以下を設定します。

```
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=https://cloud.langfuse.com
```

## chat トレース

1 リクエスト（`POST /api/chat`）= 1 トレース。以下の span 構成で記録されます（`api/main.py` の `@observe()` を起点に、`generation/generator.py` と `retrieval/searcher.py` の各関数が子 span を形成）。

```
trace (POST /api/chat)
├─ condense            （2ターン目以降のみ。フォローアップを自己完結クエリへ書き換え）
└─ retrieve_context
   ├─ embed_query       （Voyage: クエリ埋め込み）
   ├─ hybrid_search      （vector CTE + fts CTE を1SQLで実行）
   │  └─ rrf_fuse        （RRF融合、fused_candidates件数を記録）
   └─ rerank             （Voyage rerank-2.5、トークン/コスト/レイテンシ）
└─ generate_answer_stream （OpenAI GPT、ストリーミング生成）
```

トレースの `metadata.ttft_ms`（Time To First Token）が最初のトークン送出時に記録されます。各 LLM/埋め込み/リランク span にはトークン数・コスト・レイテンシが付与されます。

<!-- TODO(M5 Phase 4): docs/assets/langfuse_chat_trace.png を撮影して掲載 -->

## ingestion トレース（増分取り込みのコスト効果）

`ingest_run`（トレース）→ `embed_documents`（span）の構成。**無変更ファイルは `embed_documents` span 自体が発生しません**（`content_hash` 比較でスキップされ、埋め込みAPIを呼ばないため）。実際にコストが「埋め込んだ分だけ」発生していることを、初回取り込み後に同じコーパスで再取り込みを実行し、2回目の実行で embed span が（更新ファイル分を除き）出ないことで示します。

<!-- TODO(M5 Phase 4): docs/assets/langfuse_ingestion_trace.png を撮影して掲載（2回目=skip実行のトレース） -->

## eval トレース

`make eval` はチャットと同じ `retrieve_context`/`generate_answer_stream` span に加え、`judge_faithfulness` / `judge_answer_relevance`（`evals/judge.py`）の generation span を記録します。judge 呼び出しのコストもここに現れます。

<!-- TODO(M5 Phase 4): docs/assets/langfuse_eval_trace.png を撮影して掲載 -->

## コストの提示

コスト集計は Langfuse 標準画面（Traces / Sessions のコスト列、Dashboards）でそのまま確認できます。自作のコストダッシュボードは作っていません（NFR-5）。

## 未実装の連携

`backend/src/private_rag_apps/evals/__main__.py` に Langfuse Datasets/Experiments へのアップロードフックがコメントアウトで用意されていますが、現時点では未配線です（Langfuse 側の dataset 機能が整い次第、後続で有効化予定）。

関連: [docs/architecture.md §8](architecture.md) / [docs/decisions.md](decisions.md)
