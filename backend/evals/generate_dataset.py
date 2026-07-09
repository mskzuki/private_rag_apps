import json
from pathlib import Path

data = [
    # M0 questions
    {"id": "q01", "question": "全文検索に使っている拡張は何?", "relevant": [{"path": "db_design.md"}, {"path": "requirements.md"}], "reference_answer": "pg_bigm拡張を使用しています。", "tags": ["lookup"]},
    {"id": "q02", "question": "埋め込みモデルと次元は?", "relevant": [{"path": "requirements.md"}, {"path": "db_design.md"}], "reference_answer": "モデルは Voyage-4-lite で、次元数は 1024 次元です。", "tags": ["lookup"]},
    {"id": "q03", "question": "ベクトルインデックスの種類とパラメータは?", "relevant": [{"path": "db_design.md"}], "reference_answer": "インデックスの種類は HNSW で、パラメータは m=16, ef_construction=64 です。距離関数はコサイン類似度を使用します。", "tags": ["lookup"]},
    {"id": "q04", "question": "ハイブリッド検索で結果を融合する手法は?", "relevant": [{"path": "architecture.md"}, {"path": "db_design.md"}], "reference_answer": "Reciprocal Rank Fusion (RRF) を用いてスコアを融合します。", "tags": ["lookup"]},
    {"id": "q05", "question": "なぜ Qdrant ではなく Postgres を選んだ?", "relevant": [{"path": "architecture.md"}], "reference_answer": "管理コンポーネントを減らし、RDBとベクトル検索を単一のデータストアで完結させるためです。", "tags": ["synthesis"]},
    {"id": "q06", "question": "チャットUIに採用したライブラリと、ChatKit を選ばなかった理由は?", "relevant": [{"path": "requirements.md"}, {"path": "architecture.md"}], "reference_answer": "採用したのは assistant-ui です。ChatKit ではなく assistant-ui を選んだ理由は、shadcn/ui ベースでカスタマイズ性が高く、自前バックエンドの SSE ランタイムを組み込みやすいためです。", "tags": ["synthesis"]},
    {"id": "q07", "question": "ドキュメント更新時のチャンクの扱いは?", "relevant": [{"path": "architecture.md"}, {"path": "db_design.md"}], "reference_answer": "無変更ファイルは content_hash で判定してスキップし、変更があった場合は既存のチャンクを全て物理削除してから新しいチャンクを再挿入します。", "tags": ["lookup"]},
    {"id": "q08", "question": "LLM 呼び出しを許可されているモジュールは?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": "generation モジュールと evals モジュールのみです。", "tags": ["lookup"]},
    {"id": "q09", "question": "v1 でスコープ外にした主要項目は?", "relevant": [{"path": "requirements.md"}], "reference_answer": "SaaS コネクタ連携（Notion/Slack 等）、OAuth/マルチテナント認証、ジョブキュー（Redis 等）などです。", "tags": ["lookup"]},
    {"id": "q10", "question": "取り込みの実行経路（CLIとAPI）はどうなっている?", "relevant": [{"path": "architecture.md"}], "reference_answer": "CLI（make ingest）からは直接同期実行され、API からは FastAPI の BackgroundTasks を用いて非同期で実行されます。", "tags": ["lookup"]},
    
    # New questions for M3
    {"id": "q11", "question": "Langfuse がダウンしているとアプリは動かなくなる?", "relevant": [{"path": "requirements.md"}], "reference_answer": "いいえ、Langfuse の環境変数が未設定（または障害時）でも no-op となり、アプリの動作は妨げられません。", "tags": ["lookup"]},
    {"id": "q12", "question": "Eval で CI を fail させる条件は?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": "検索指標（Recall 等）が許容誤差を超えて低下した場合に fail となります。生成指標の軽微な低下は warn となります。", "tags": ["lookup"]},
    {"id": "q13", "question": "データセットにチャンク ID を含めない理由は?", "relevant": [], "reference_answer": "（m3_eval_expansion に記載がありますが、seed/ にはないため回答不可が期待されます）", "expect_no_answer": True, "tags": ["negative"]},
    {"id": "q14", "question": "Redis をキャッシュに使っている?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": "いいえ、v1 では Redis などのジョブキューやキャッシュ層はスコープ外として導入を禁止しています。", "tags": ["lookup"]},
    {"id": "q15", "question": "React Native アプリの実装予定は?", "relevant": [], "reference_answer": "そのような情報は含まれていません。", "expect_no_answer": True, "tags": ["negative"]},
    {"id": "q16", "question": "チャンキング戦略の前提（Markdown）は?", "relevant": [{"path": "architecture.md"}], "reference_answer": "Markdown の見出し（# や ##）を境界として意味的なチャンクに分割します。", "tags": ["lookup"]},
    {"id": "q17", "question": "生成モデル（Claude）へのプロンプトはどう管理する?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": "コード内にハードコードせず、prompts/ ディレクトリにファイルを置いて管理します。", "tags": ["lookup"]},
    {"id": "q18", "question": "評価用のゴールデンデータセットの実データはコミットしてよいか?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": "いいえ、個人の実データをコミットしてはいけません。seed/ のシードコーパス由来のもののみコミット可能です。", "tags": ["lookup"]},
    {"id": "q19", "question": "DB マイグレーションツールは何を使っている?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": "Alembic を使用しています。", "tags": ["lookup"]},
    {"id": "q20", "question": "ユーザー認証の方式は?", "relevant": [{"path": "requirements.md"}], "reference_answer": "v1 では単一ユーザー向けであり、マルチユーザー認証や OAuth はスコープ外です。", "tags": ["lookup"]},
    {"id": "q21", "question": "AI モデルへの API キーはどこで設定する?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": ".env ファイルで ANTHROPIC_API_KEY や VOYAGE_API_KEY として設定します。", "tags": ["lookup"]},
    {"id": "q22", "question": "インジェスト時の重複排除はどのように行われる?", "relevant": [{"path": "db_design.md"}], "reference_answer": "ファイルの content_hash を計算し、前回取り込み時のハッシュと比較して同一であればスキップします。", "tags": ["lookup"]},
    {"id": "q23", "question": "PDF の読み込みに対応しているか?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": "いいえ、PDF パースは v1 スコープ外です。", "tags": ["lookup"]},
    {"id": "q24", "question": "回答に必ず含めるべき情報は?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": "コンテキストに基づく出典（citation）を必ず含める必要があります。", "tags": ["lookup"]},
    {"id": "q25", "question": "外部 API のテスト方針は?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": "実課金される API は叩かず、モックまたは記録再生を用いてテストします。", "tags": ["lookup"]},
    {"id": "q26", "question": "マルチターンの履歴はどう考慮される?", "relevant": [{"path": "architecture.md"}], "reference_answer": "LLM を使って会話履歴を元にユーザークエリを自己完結した形（standalone query）に書き換える condense ステップを挟みます。", "tags": ["synthesis"]},
    {"id": "q27", "question": "RRF のパラメータは?", "relevant": [{"path": "db_design.md"}], "reference_answer": "RRF 定数（k）の既定値は 60 です。", "tags": ["lookup"]},
    {"id": "q28", "question": "フロントエンドの言語とフレームワークは?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": "TypeScript と Next.js (App Router) を使用します。", "tags": ["lookup"]},
    {"id": "q29", "question": "コンテキストに見つからない質問をされた時の期待挙動は?", "relevant": [{"path": "AGENTS.md"}], "reference_answer": "情報をでっち上げず、「見つからない」と回答します。", "tags": ["lookup"]},
    {"id": "q30", "question": "株価の予想をして", "relevant": [], "reference_answer": "コンテキストに関連情報がありません。", "expect_no_answer": True, "tags": ["negative"]},
]

out_path = Path("/Users/mskzuk/src/private_rag_apps/backend/evals/dataset/m3_golden.jsonl")
out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    for item in data:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")

print(f"Created {out_path} with {len(data)} items.")
