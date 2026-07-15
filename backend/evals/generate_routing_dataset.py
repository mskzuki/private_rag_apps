"""
M7 T1: backend/evals/dataset/routing.jsonl の生成スクリプト。

`generate_dataset.py`(m3_golden.jsonl の初期生成に使われた前例)と同じ位置づけの
一度きりの構築用ツール。eval実行スクリプト(T2/T4スコープ)ではなく、データセット
構築の再現性(特に calibration/holdout 分割の再現性)を担保するために残す。

各カテゴリの grounding(根拠パス or 「記述なし」の確認結果)は
`docs/specs/`ではなく `backend/evals/dataset/routing-README.md` に人間可読な形で
まとめてある。このスクリプト内の grounding フィールドはその一次情報。

followup カテゴリの history 中の assistant 発話は、実アプリの生成パイプライン
(private_rag_apps.retrieval.searcher.retrieve_context +
 private_rag_apps.generation.generator.generate_answer_stream, 2026-07-14実行,
 LLM_PROVIDER=ollama, llm_model=qwen3.5:9b)が実際に生成したテキストをそのまま
使用している(創作していない)。収集手順の詳細は routing-README.md §5 を参照。

実行方法: cd backend && uv run python ../backend/evals/generate_routing_dataset.py
"""

import json
import random
from pathlib import Path

SEED = 20260714  # 本日日付。再現可能な分割のため固定(routing-README.md にも記載)

OUT_PATH = Path(__file__).resolve().parent / "dataset" / "routing.jsonl"

# =====================================================================
# corpus (40件, expected_route=grounded)
# 27件: m3_golden.jsonl (q01-q29 のうち negative/turns付きを除く) の質問文を流用
# 13件: コーパスの別トピックを根拠に新規作成
# 各タプル: (id, query, grounding_paths)
# =====================================================================
CORPUS_ITEMS = [
    ("c001", "全文検索に使っている拡張は何?", ["db_design.md", "requirements.md"]),
    ("c002", "埋め込みモデルと次元は?", ["requirements.md", "db_design.md"]),
    ("c003", "ベクトルインデックスの種類とパラメータは?", ["db_design.md"]),
    ("c004", "ハイブリッド検索で結果を融合する手法は?", ["architecture.md", "db_design.md"]),
    ("c005", "なぜ Qdrant ではなく Postgres を選んだ?", ["architecture.md"]),
    (
        "c006",
        "チャットUIに採用したライブラリと、ChatKit を選ばなかった理由は?",
        ["requirements.md", "architecture.md"],
    ),
    ("c007", "ドキュメント更新時のチャンクの扱いは?", ["architecture.md", "db_design.md"]),
    ("c008", "LLM 呼び出しを許可されているモジュールは?", ["AGENTS.md"]),
    ("c009", "v1 でスコープ外にした主要項目は?", ["requirements.md"]),
    ("c010", "取り込みの実行経路(CLIとAPI)はどうなっている?", ["architecture.md"]),
    ("c011", "Langfuse がダウンしているとアプリは動かなくなる?", ["requirements.md"]),
    ("c012", "Eval で CI を fail させる条件は?", ["AGENTS.md"]),
    ("c013", "Redis をキャッシュに使っている?", ["AGENTS.md"]),
    ("c014", "チャンキング戦略の前提(Markdown)は?", ["architecture.md"]),
    ("c015", "生成モデルへのプロンプトはどう管理する?", ["AGENTS.md"]),
    ("c016", "評価用のゴールデンデータセットの実データはコミットしてよいか?", ["AGENTS.md"]),
    ("c017", "DB マイグレーションツールは何を使っている?", ["AGENTS.md"]),
    ("c018", "ユーザー認証の方式は?", ["requirements.md"]),
    ("c019", "AI モデルへの API キーはどこで設定する?", ["AGENTS.md"]),
    ("c020", "インジェスト時の重複排除はどのように行われる?", ["db_design.md"]),
    ("c021", "PDF の読み込みに対応しているか?", ["AGENTS.md", "requirements.md"]),
    ("c022", "回答に必ず含めるべき情報は?", ["AGENTS.md"]),
    ("c023", "外部 API のテスト方針は?", ["AGENTS.md"]),
    ("c024", "マルチターンの履歴はどう考慮される?", ["architecture.md"]),
    ("c025", "RRF のパラメータは?", ["db_design.md"]),
    ("c026", "フロントエンドの言語とフレームワークは?", ["AGENTS.md"]),
    ("c027", "コンテキストに見つからない質問をされた時の期待挙動は?", ["AGENTS.md"]),
    # --- 新規13件 ---
    ("c028", "リランクに使っているモデルは何ですか?", ["db_design.md", "requirements.md"]),
    (
        "c029",
        "会話やメッセージの引用(citations)はどのように保存されますか?",
        ["db_design.md", "architecture.md"],
    ),
    (
        "c030",
        "取り込み実行の多重実行はどのように防いでいますか?",
        ["architecture.md", "db_design.md"],
    ),
    ("c031", "検索結果が0件のときの挙動はどうなりますか?", ["architecture.md"]),
    ("c032", "SSEで配信されるイベントの種類を教えてください", ["architecture.md"]),
    ("c033", "データベースの主要なテーブル構成を教えてください", ["db_design.md"]),
    ("c034", "パフォーマンス(TTFTなど)の目標値はありますか?", ["requirements.md"]),
    ("c035", "デモモードとは何ですか?", ["requirements.md"]),
    ("c036", "取り込み対象として認識されるファイル形式は?", ["requirements.md", "architecture.md"]),
    (
        "c037",
        "ソースファイルが削除された場合、インデックスはどう扱われますか?",
        ["architecture.md", "db_design.md"],
    ),
    ("c038", "チャンクのメタデータには何が含まれますか?", ["architecture.md", "db_design.md"]),
    ("c039", "コスト管理(トークン数・費用)はどのように可視化されますか?", ["requirements.md"]),
    ("c040", "埋め込みの次元を変更する場合、何が必要になりますか?", ["db_design.md"]),
]
assert len(CORPUS_ITEMS) == 40

# =====================================================================
# general (40件, expected_route=direct)
# 4ドメイン x 10件。全件コーパス4ファイルにgrep+目視で「関連記述なし」を確認済み
# (routing-README.md §3 参照)。各タプル: (id, query, domain)
# =====================================================================
GENERAL_ITEMS = [
    # プログラミング一般
    ("g001", "Pythonのwalrus operator(:=)とは何ですか?", "programming"),
    ("g002", "JavaScriptのPromiseとasync/awaitの違いは何ですか?", "programming"),
    ("g003", "Gitのrebaseとmergeの違いを教えてください", "programming"),
    ("g004", "オブジェクト指向設計におけるSOLID原則とは何ですか?", "programming"),
    ("g005", "Pythonのwith文(コンテキストマネージャ)とは何ですか?", "programming"),
    ("g006", "再帰関数とループの使い分けの一般的な指針は?", "programming"),
    ("g007", "TypeScriptのジェネリクスとは何ですか?", "programming"),
    ("g008", "デザインパターンのFactory Methodパターンとは何ですか?", "programming"),
    ("g009", "単体テストとE2Eテストの一般的な違いは何ですか?", "programming"),
    ("g010", "Pythonのデコレータとは何ですか?", "programming"),
    # 統計
    ("g011", "t検定とは何ですか?", "statistics"),
    ("g012", "統計学におけるp値の意味を教えてください", "statistics"),
    ("g013", "標準偏差と分散の違いは何ですか?", "statistics"),
    ("g014", "ベイズ統計における事前分布とは何ですか?", "statistics"),
    ("g015", "相関と因果の違いを教えてください", "statistics"),
    ("g016", "正規分布の性質について教えてください", "statistics"),
    ("g017", "A/Bテストのサンプルサイズは一般的にどう決めますか?", "statistics"),
    ("g018", "中心極限定理とは何ですか?", "statistics"),
    ("g019", "回帰分析における多重共線性とは何ですか?", "statistics"),
    ("g020", "標本と母集団の違いは何ですか?", "statistics"),
    # インフラ一般
    ("g021", "Kubernetesのpodとは何ですか?", "infra"),
    ("g022", "ロードバランサーの一般的な負荷分散アルゴリズムには何がありますか?", "infra"),
    ("g023", "サーキットブレーカーパターンとは何ですか?", "infra"),
    ("g024", "DNSの名前解決の流れを教えてください", "infra"),
    ("g025", "コンテナオーケストレーションとは何ですか?", "infra"),
    ("g026", "TCP/IPにおける3ウェイハンドシェイクとは何ですか?", "infra"),
    ("g027", "一般的なCI/CDパイプラインの構成要素は何ですか?", "infra"),
    ("g028", "リバースプロキシとは何ですか?", "infra"),
    ("g029", "Blue-Greenデプロイメントとは何ですか?", "infra"),
    ("g030", "一般的なオートスケーリングの仕組みを教えてください", "infra"),
    # RAG一般論(コーパスに用語だけ登場するものは避け、完全に無関係な技法を採用)
    ("g031", "RAGにおけるHyDE(Hypothetical Document Embeddings)とは何ですか?", "rag_theory"),
    ("g032", "Self-RAGとは何ですか?", "rag_theory"),
    ("g033", "RAG評価のためのRAGASフレームワークとは何ですか?", "rag_theory"),
    ("g034", "GraphRAGとは何ですか?", "rag_theory"),
    ("g035", "Corrective RAG(CRAG)とは何ですか?", "rag_theory"),
    ("g036", "RAGにおけるsentence-window retrievalとは何ですか?", "rag_theory"),
    ("g037", "Late chunking(埋め込み後にチャンク化する手法)とは何ですか?", "rag_theory"),
    (
        "g038",
        "RAGパイプラインにおける埋め込みモデルのfine-tuningは一般的にどのように行いますか?",
        "rag_theory",
    ),
    ("g039", "マルチベクトル検索(ColBERT等)とは何ですか?", "rag_theory"),
    ("g040", "RAGにおけるコンテキスト圧縮(contextual compression)とは何ですか?", "rag_theory"),
]
assert len(GENERAL_ITEMS) == 40

# =====================================================================
# ambiguous (30件)
# corpusの質問から固有名詞を落として一般化する操作で作成した境界ケース。
# expected_routeは「コーパスに関連記述が実在するか」で機械的に決定
# (routing-README.md §4 に全件の判断根拠を記録)。
# 各タプル: (id, query, expected_route, grounding_paths_or_reason)
# =====================================================================
AMBIGUOUS_ITEMS = [
    # --- grounded寄り: 固有名詞は消えたが、対応する設計判断の記述が実在する ---
    (
        "a001",
        "ベクトルDBとRDBを統合する設計を選ぶ理由にはどのようなものがある?",
        "grounded",
        ["architecture.md"],
    ),
    (
        "a002",
        "拡張1つで完結する軽量な日本語全文検索の方式にはどのようなものがある?",
        "grounded",
        ["db_design.md"],
    ),
    (
        "a003",
        "チャットの基本UXを自前実装せず既存ライブラリに任せる場合の判断基準は?",
        "grounded",
        ["architecture.md"],
    ),
    (
        "a004",
        "更新のあるデータセットに向いた、事前学習不要のベクトルインデックス手法は?",
        "grounded",
        ["db_design.md"],
    ),
    (
        "a005",
        "スコアスケールの異なる複数の検索結果を安全に統合する一般的な手法は?",
        "grounded",
        ["architecture.md"],
    ),
    (
        "a006",
        "ドキュメント更新時に部分更新ではなく全置換を選ぶ判断基準は?",
        "grounded",
        ["architecture.md"],
    ),
    (
        "a007",
        "検索の再現率と精度をどう役割分担させるのが定番構成か?",
        "grounded",
        ["architecture.md"],
    ),
    (
        "a008",
        "取り込みにジョブキュー基盤を持たない設計はどのような場合に妥当か?",
        "grounded",
        ["architecture.md", "requirements.md"],
    ),
    (
        "a009",
        "単一ユーザー向けMVPでOAuth認証を省略する判断はどう正当化されるか?",
        "grounded",
        ["requirements.md"],
    ),
    (
        "a010",
        "埋め込みベンダーとLLMベンダーを同一にする組み合わせのメリットは?",
        "grounded",
        ["requirements.md"],
    ),
    (
        "a011",
        "全文検索においてbigram方式と形態素解析方式のどちらを使うべきか?",
        "grounded",
        ["requirements.md", "db_design.md"],
    ),
    ("a012", "Faithfulness(忠実性)とは何を指す指標ですか?", "grounded", ["requirements.md"]),
    ("a013", "TTFTとは何の略で、何を指しますか?", "grounded", ["requirements.md"]),
    (
        "a014",
        "埋め込みモデルやチャンキング戦略を変更した場合、何をセットで行う必要があるか?",
        "grounded",
        ["AGENTS.md", "db_design.md"],
    ),
    (
        "a015",
        "コンテキストにない主張をさせないためにプロンプトで工夫すべきことは?",
        "grounded",
        ["architecture.md", "requirements.md"],
    ),
    # --- direct寄り: 採用値は書かれているが、一般的な決定方法・手法自体は書かれていない ---
    (
        "a016",
        "HNSWのefConstructionはどう決めるべきか?",
        "direct",
        "採用値(m=16,ef_construction=64)は記載あり(db_design.md)だが、一般的なチューニング指針の記述なし",
    ),
    (
        "a017",
        "RRFのk値は一般的にどのように調整するのが望ましいか?",
        "direct",
        "採用値(k=60)は記載あり(db_design.md)だが、調整方法の一般論の記述なし",
    ),
    (
        "a018",
        "チャンクサイズ(トークン数)を決める一般的な指針は?",
        "direct",
        "採用値(512トークン/15%オーバーラップ)は記載あり(architecture.md)だが、決め方の一般論の記述なし",
    ),
    (
        "a019",
        "リランクのtop_kをいくつに設定するのが適切かの一般的な判断基準は?",
        "direct",
        "採用値(top_k=8)は記載あり(db_design.md)だが、一般的な判断基準の記述なし",
    ),
    (
        "a020",
        "ベクトル検索の候補数をどう決めるべきかの一般的な指針は?",
        "direct",
        "採用値(candidate_k=50)は記載あり(architecture.md)だが、決め方の一般論の記述なし",
    ),
    (
        "a021",
        "content_hashの計算に使うべきハッシュアルゴリズムは何が適切か?",
        "direct",
        "content_hashの比較を行う旨の記述はあるが、アルゴリズム名(SHA-256等)の言及なし",
    ),
    (
        "a022",
        "埋め込みの次元数は一般的に何次元が最適か?",
        "direct",
        "採用値(1024次元)は記載あり(db_design.md)だが、一般的な最適次元数の議論なし",
    ),
    (
        "a023",
        "LLMの温度パラメータ(temperature)は一般的にどう設定すべきか?",
        "direct",
        "コーパス中にtemperatureパラメータの記述なし(grep確認済み)",
    ),
    (
        "a024",
        "APIのレート制限には一般的にどう対処するのが望ましいか?",
        "direct",
        "コーパス中にレート制限対処の記述なし(grep確認済み)",
    ),
    (
        "a025",
        "ストリーミング表示の体感速度を上げる一般的なバッファリングの工夫は?",
        "direct",
        "SSEイベント種別の記述はあるが、バッファリング戦略の記述なし",
    ),
    (
        "a026",
        "クロスエンコーダ型リランクの速度対精度のトレードオフは一般的にどう考えられるか?",
        "direct",
        "「クロスエンコーダ」という用語自体はrequirements.md §3の用語集にあるが、速度対精度のトレードオフ論は記述なし",
    ),
    (
        "a027",
        "RAGにおいて引用の粒度(文単位か段落単位か)は一般的にどう決めるべきか?",
        "direct",
        "citation構造の記述はあるが、粒度をどう決めるかの一般論は記述なし",
    ),
    (
        "a028",
        "会話履歴を要約してプロンプトに含める際の一般的なベストプラクティスは?",
        "direct",
        "トークン予算内で切り詰める旨の記述はあるが、要約のベストプラクティス論は記述なし",
    ),
    (
        "a029",
        "ベクトルインデックスの再構築(reindex)は一般的にどのくらいの頻度で行うべきか?",
        "direct",
        "コーパス中にreindex頻度の記述なし(grep確認済み)",
    ),
    (
        "a030",
        "マルチテナントSaaSでのAPIキー管理のベストプラクティスは?",
        "direct",
        "本プロジェクトは単一ユーザー前提でマルチテナントは対象外(requirements.md)。マルチテナントAPIキー管理の一般論の記述はなし",
    ),
]
assert len(AMBIGUOUS_ITEMS) == 30

# =====================================================================
# followup (20件)
# history の user/assistant 各1ターン目は、実アプリの生成パイプラインを
# 2026-07-14に実行して得られた実回答をそのまま使用(創作していない)。
# 収集ログ全文は task-T1 作業時点の scratchpad に保存(リポジトリ外)。
# 各タプル: (id, turn1_user, turn1_assistant_real, turn2_query, expected_route,
#            expected_search_query, grounding_paths_or_reason)
# =====================================================================
FOLLOWUP_ITEMS = [
    (
        "f001",
        "全文検索に使っている拡張は何ですか?",
        "提供されたコンテキスト情報に基づくと、プロジェクトで採用されている全文検索に使用している拡張は以下の通りです。\n\n**pg_bigm** です [1]。\n\nPostgres 標準の FTS（`to_tsvector`）では日本語の分かち書きができないため使わず、軽量な bigram ベースであり類似度スコアを持つ `pg_bigm` を採用しています [2][3]。「検索品質を上げたい場合」には PGroonga に差し替える候補がありますが、現在のデフォルトは pg_bigm となっています [4][7]。",
        "それの代替として検討された案はありますか?",
        "grounded",
        "pg_bigmの代替候補となる全文検索エンジン",
        ["db_design.md", "requirements.md"],
    ),
    (
        "f002",
        "ベクトルインデックスの種類とパラメータを教えてください",
        "提供されたコンテキスト情報に基づき、「ベクトルインデックスの種類」および「パラメータ」について回答します。\n\n### ベクトルインデックスの種類\n**HNSW (Hierarchical Navigable Small World)** が採用されています [1]。また、この手法は更新のあるデータにおいて再現率と速度のバランスが良いとして選択されたことも記載されています [8]。\n\n### パラメータ詳細\n作成時のパラメータ（`WITH (...)` クロース）としては以下が設定されています：\n*   `m = 16`: HNSW グラフ構築時の階層内の隣接するエッジの最大数です [1]。\n*   `ef_construction = 64`: HNSW グラフを構築するための効率的な検索領域（探索候補）の数値です [1]。\n\nなお、実際の検索時におけるパラメータとして「調整可能なもの」として以下の設定も挙げられています：\n*   `hnsw.ef_search`: リコール性能と速度のバランスをとるために検索時に調整するパラメータです [8]。",
        "なぜその方式を採用したのですか?",
        "grounded",
        "HNSWを採用した理由",
        ["db_design.md"],
    ),
    (
        "f003",
        "ハイブリッド検索で結果を融合する手法は何ですか?",
        "ハイブリッド検索の結果を融合する手法として、**Reciprocal Rank Fusion (RRF)** が採用されています `[1][3]`。\n\n具体的には以下の通りです：\n\n*   **並列実行**: クエリに対しベクトル検索と全文検索（pg_bigm）を並列で実行し、それぞれから上位の結果を取得します `[1]`。\n    *   ベクトル検索の候補数はデフォルト 50 件 `[3]`\n    *   全文検索の候補数もデフォルト 50 件です（合計して RRF 用に 60 件のスロットにマージされます）[2][3]\n*   **融合処理**: 取得された複数のリストを RRF アルゴリズムを使って順位に基づいて統合し、スコア化した結果が上位 `k` 件（デフォルトでは 40 件：`:fuse_k = 40`）になります `[1][3][4]`。\n*   **リランク**: この融合後の上位結果に対して Voyage rerank-2.5 を使用して再度並べ替えを行い、最終的な Top-k 件（デフォルトでは 8 件）を決定します `[1][3]`。",
        "それのパラメータは?",
        "grounded",
        "RRFのパラメータ(k)の値",
        ["db_design.md"],
    ),
    (
        "f004",
        "なぜQdrantではなくPostgresを選んだのですか?",
        "提供されたコンテキスト情報に基づき、なぜ Qdrant ではなく PostgreSQL (pgvector) が選ばれたかの理由は以下の通りです。\n\n1. **運用の簡略化と統合**: ベクトルデータおよび全文検索を扱いながら、リレーショナルデータを含めた全ての情報を**単一のデータベース (PostgreSQL)** に集約することで、運用対象を絞り込もうとして選択されました `[2]`[4]。\n2. **ハイブリッド検索の実装の容易さ**: PostgreSQL を 1 つ用いればベクトルと全文検索を同時に行えるため、複雑な**ハイブリッド検索が比較的組みやすい**点が評価されました `[1]`。また、Qdrant と比較検討した結果、この選択は妥当であると判断されています `[2]`。\n3. **データ整合性とトランザクション**: 外部ベクトル DB（Qdrant など）と比較した場合、PostgreSQL は FK/トランザクションなどを用いた**整合性を確保しやすい**とされました `[2]`。\n\n代替案として Qdrant や Weaviate が挙げられていましたが、これらの理由から PostgreSQL + pgvector の採用が決定しました `[1]`[4]。",
        "ところで、一般的なRDBのACID特性とは何ですか?",
        "direct",
        "RDBのACID特性とは",
        "コーパス中にACIDという語・説明の記述なし(grep確認済み)。トピックを無関係な一般知識へ転換したdirect期待の例",
    ),
    (
        "f005",
        "埋め込みモデルと次元数を教えてください",
        "ご提示いただいたコンテキスト情報に基づき、埋め込みモデルとその次元数は以下の通りです。\n\n*   **埋め込みモデル**: Voyage (voyage-4-lite) または voyage-4 / OpenAI text-embedding-3 です [1][8]。特に推奨されるのは現行世代で多言語に対応し、最初の 2 億トークン無料である「Voyage (voyage-4-lite)」です [1]。\n*   **次元数**: デフォルトは**1024 次元**（`vector(1024)`）ですが、必要に応じて 256/512/2048 のサイズも選択可能です [1][2]。",
        "それのコストはどれくらいですか?",
        "grounded",
        "Voyageの埋め込みAPIのコスト(無料枠)",
        ["requirements.md"],
    ),
    (
        "f006",
        "ドキュメント更新時のチャンクの扱いはどうなっていますか?",
        "ドキュメント（ソース）の更新時、または変更検知された場合のチャンクに関する扱いは以下の通りです。\n\n1.  **全置換戦略**: ソースが更新・新規作成された場合は、そのソースに関連するチャンクを**すべて削除し、改めて再チャンキングと再挿入を行います**。[2][4][6]\n2.  **部分更新は実施しない**: チャンク単位の部分更新ではなく、「無変更スキップ」と「全置換」の二分法をとります。これにより単純さを保ちながらデータ整合性を維持しています [2][6]。\n3.  **処理フロー**: アーキテクチャ上、`content_hash` の比較で新規または変更が判明すると、チャンク生成（見出し境界尊重）→埋め込み→DBへのアップセット（全削除後の再挿入）という順序で行われます [4]。\n\nこの「該当 source の chunks を全削除 → 再チャンク → 再挿入」という戦略は決定されており、部分更新よりも整合性を保ちやすいとされています [1][2]。",
        "削除されたファイルの場合はどうなりますか?",
        "grounded",
        "ソースファイルが削除された場合のチャンクの扱い",
        ["architecture.md", "db_design.md"],
    ),
    (
        "f007",
        "LLM呼び出しを許可されているモジュールはどこですか?",
        "提供されたコンテキスト情報に基づき、LLM 呼び出しが許可されているモジュールについて回答します。\n\n- `generation` モジュールのみ（[1]）\n- または `evals/` も該当する可能性がありますが、明確なルール文では「`generation/`（と `evals/`）」という表現も一部に見られることから主に `generation` が規定されています [2][5]。\n- より詳細には、「LLM 呼び出しは `generation/` と `evals/` のみ」という記述があります [1], また「LLM は generation モジュールのみ」とも記載されます[3],[6]。\n\n結論として、明確に許可されているのは **`generation`** およびコンテキストによっては **`evals`** です（他モジュールでの呼び出しは禁止されています）。",
        "埋め込みの呼び出しはどうですか?",
        "grounded",
        "埋め込み呼び出しが許可されているモジュール",
        ["AGENTS.md"],
    ),
    (
        "f008",
        "RRFのパラメータ(k)の既定値はいくつですか?",
        "RRF のパラメータ `k`（融合に使用する候補数の閾値）のデフォルト値は **60** です [1]。これは、ベクトル検索と全文検索からそれぞれ 50 件ずつ取得した結果を RRF で融合作業を行う際に適用されます [2][3]。\n\n| ステップ | デフォルトパラメータ/動作詳細 |\n| :--- | :--- |\n| **融合 (Reciprocal Rank Fusion)** | `k = 60`（計算式: $1.0 / (:rrf\\_k + rank)$）[2][3] |\n\nこの設定により、ベクトル検索と全文検索の両方から得られた上位候補を順位に基づいて統合し、その結果からさらに上位 40 件 (`fuse_k`) を選別します [1][3]。",
        "その値を変える場合、設定はどこで行いますか?",
        "grounded",
        "RRFのkなどパラメータの設定変更方法",
        ["architecture.md", "AGENTS.md"],
    ),
    (
        "f009",
        "チャンキング戦略のトークン数の目安を教えてください",
        "チャンキングのトークンの数に関する目安は以下の通りです：\n\n*   **512 トークン**が目標となります [1]。",
        "オーバーラップの割合はどれくらいですか?",
        "grounded",
        "チャンクのオーバーラップ率",
        ["architecture.md"],
    ),
    (
        "f010",
        "インジェスト時の重複排除はどのように行われますか?",
        "提供されたコンテキスト情報に基づき、インジェスト（データ取り込み）時における重複排除や更新処理の仕組みについて回答します。\n\n**1. 変更判定によるスキップと置換戦略**\nシステムは「増分再取り込み」を採用しており、以下の手順でファイルを扱います [5][6]：\n*   **内容の確認**: ドキュメントを変更するかどうかを `content_hash` を比較して判断します。ファイルが消失している場合は論理削除（`deleted_at` の設定）が行われます [4][6]。\n*   **無変更ファイルの扱い**: 変化がないと判定されたドキュメントについては、再埋め込みや更新処理をスキップし、コスト削減を図ります [3][5]。\n*   **有変更有りファイルの扱い**: 内容に変更があった場合（新規追加または改変）、単純な部分更新ではなく、「該当 source の chunks を全削除 → チャンキング・埋め込みを行う → 新しいチャンクを再挿入する」ことで置き換える戦略をとります [4][6]。これは整合性を優先するためです。\n\n**2. 統計情報の記録**\n実行プロセスにおいて、以下の結果が `ingest_runs` テーブルの `stats` フィールドに JSON で記録されます [1][5][8]：\n*   `added`: 新規追加されたチャンク数など\n*   `updated`: 更新（置換）された source の数 など\n*   `deleted`: 削除され検索対象から除外されたドキュメントの数\n\n**3. エラー時の処理**\n取り込み中に特定のファイルの埋め込みに失敗した場合などは、そのファイルをスキップして他の実行を続行し、`ingest_runs.stats.failed_files` や状態によってエラー情報を記録します [8]。",
        "ファイルが変更された場合はどう処理されますか?",
        "grounded",
        "変更されたファイルのチャンク再生成処理",
        ["architecture.md", "db_design.md"],
    ),
    (
        "f011",
        "リランクに使っているモデルは何ですか?",
        "リランクに使用されているモデルは、Voyage AI の提供する `rerank-2.5` です。具体的には以下の情報に基づいています：\n\n*   検索設計における「リランク」の手法として**Voyage rerank-2.5**が設定されています [1]。\n*   リランキング（埋め込み）については、Anthropic Claude と併用するために自然な **Voyage (voyage-4-lite / 1024次元)** または **rerank-2.5** が採用されていると定義されており、リランクも同ベンダで完結する仕様です [2]。\n*   アーキテクチャ設計上の「retrieval」モジュールが外部呼び出しとして行う処理に **Voyage(rerank)** が含まれています [6]。",
        "ちなみに一般的なAPIのバージョニング戦略にはどんなものがありますか?",
        "direct",
        "APIのバージョニング戦略とは",
        "コーパス中にAPIバージョニングの記述なし(grep確認済み)。トピックを無関係な一般知識へ転換したdirect期待の例",
    ),
    (
        "f012",
        "マルチターンの会話履歴はどのように考慮されますか?",
        "マルチターン会話において、ユーザーの質問が「それの詳細は？」のような指示語を含む場合、システムはその**会話履歴を考慮したクエリ書き換え（condense）**を行い、フォローアップ質問を自己完結する検索用クエリへと変換します [1][5]。\n\nまた、プロンプト構成においては直近の会話をトークン予算内で切り詰めた形で利用し、LLM に提供して文脈を理解させます [6][7]。このように、会話履歴はクエリの再構築と生成処理の入力として考慮されます。",
        "それは何ターン分の履歴を見ますか?",
        "direct",
        "クエリ書き換えで考慮する会話履歴のターン数",
        "コーパス(architecture.md)は「直近の会話(トークン予算内で切り詰め)」と述べるのみで、具体的なターン数Nの記述なし。指示語(それ)は正しく解決できるが答えが無い境界ケース",
    ),
    (
        "f013",
        "取り込みの実行経路(CLIとAPI)はどうなっていますか?",
        "提供されたコンテキスト情報に基づく回答です：\n\n**1. エキスツ（実行）形態**\n*   **2 つの経路**: CLI (`make ingest`, `make demo`) と、API からの BackgroundTasks による実行があります [1]。\n*   **実体の統一**: これらの両方のケースにおいて、処理する中身は同一の `ingestion` サービスです [1][3]。\n\n**2. API（BackgroundTasks）経由の場合の実行フロー (Ingestion Path)**\nAPI トリガーからインデックス更新までの流れは以下の通りです [2]:\n*   コーパスディレクトリを走査し、`.md` / `.txt` ファイルを対象とします。\n*   ドキュメント共通表現へ正規化（タイトル抽出・メタデータ付与）。\n*   `content_hash` を比較して変更判定を行います。変更のないものはスキップされ [2][6]：\n    *   **新規/変更**: チャンキング（見出し境界尊重）→ 埋め込み生成 → upsert で置換（全削除後再挿入）します [2]。\n    *   **ファイル消失**: `deleted_at` の設定による論理削除となります [2][6]。\n*   最後に統計情報 (`added`, `updated`, `deleted`, `skipped`) を記録します [1][3][7]。\n\n**3. CLI 経由の場合の実行フロー**\nCLI はロジックを持たない薄い層であり、`ingestion` サービスを呼び出すだけです [6][7]:\n*   コマンドとして `make ingest CORPUS=path/` を実行することで取り込みが可能です [5]。\n*   同様にデモ用コーパスを使用する場合は `make demo` とします [3][4][8]。\n\n**技術スタックおよびアーキテクチャ上の留意点:**\n*   **ジョブキューの是非**: Redis や ARQ (Redis) を持たない構成とされており、ローカル環境で CLI 実行が MVP に適しているためです（SaaS コネクタ導入時に再検討する旨）[3][4]。\n*   **依存関係ルール**: `ingestion` はストアに書き込む側であり、「retreival」から直接読み取ることを防ぐ一方向の依存構造になっています [7]。",
        "APIから実行した場合の完了確認はどうすればいいですか?",
        "grounded",
        "取り込み実行状況の確認方法(API)",
        ["architecture.md"],
    ),
    (
        "f014",
        "チャットUIに採用したライブラリを教えてください",
        "チャット UI に採用されたライブラリは **`assistant-ui`** です。\n\nこの情報は以下の文書から確認できます：\n\n*   **[1]** 「ChatKit は OpenAI API 密結合のため不採用」とされ、「Vercel AI SDK + AI Elements / 自前(shadcn)」の選択肢の中に `assistant-ui` があり、これが「shadcn/ui ベース」を採用しているとして紹介されています。\n    [2] v0.3 の変更履歴でチャット UI ライブラリに **`assistant-ui`** を採用したことが記載されており、ChatKit は不採用とされています。\n*   **[4]** チャット UI に `assistant-ui`（shadcn/ui + Tailwind ベース）を採用しており、Vercel AI SDK のプロトコルには乗せずカスタムランタイムで SSE を直接受ける方式としています。また出典カードの描画も React コンポーネントで行うことになっています。\n*   **[5]** Frontend スタックとして `assistant-ui`（shadcn/ui ベースのチャット UI）を採用していることが明記されています。\n    [6] v0.3 の変更履歴で「フロントに assistant-ui を採用した」とあり、ChatKit 不採用および自前再実装禁止などの運用方針も示されています。",
        "それを選んだ決め手は何ですか?",
        "grounded",
        "assistant-uiを採用した決め手",
        ["requirements.md", "architecture.md"],
    ),
    (
        "f015",
        "検索結果が0件のときの挙動はどうなりますか?",
        "コンテキスト情報に基づくと、検索結果が 0 件の場合の動作は以下の通りです。\n\n*   回答生成を行うことなく、「該当情報が見つかりません」または「見つからない」というメッセージを返します [1][2]。\n*   これは信頼性確保のためであり、出典にない主張や推測を行わないという要件を満たす措置となります [5][7]。",
        "その場合、生成モデルは何も呼ばれないのですか?",
        "grounded",
        "検索結果0件の場合に生成処理は実行されるか",
        ["architecture.md"],
    ),
    (
        "f016",
        "データベースのテーブル構成を教えてください",
        "ご提示いただいたコンテキスト情報に基づき、データベースのテーブル構成について回答します。\n\n### システムの方針と基本仕様\nシステムではリレーショナルデータ、ベクトルデータを問わず**PostgreSQL 1 つに集約**しており[7]、すべての主要エントティ（Source, Chunk, Conversation/Messageなど）を管理しています [5]。また、キーは URL の露出耐性を考慮した **UUID** ([7], デフォルトで `gen_random_uuid()` を使用) が採用されていますし、監査証跡として各テーブルには `created_at` と `updated_at` があります [3][4]。\n\n### テーブルの詳細構成\n\n#### 1. チャット関連\n*   **conversations**: チャンネル（会話）を表します。ID, タイトル, 作成日時/更新日時のカラムを持っています [1][2]。\n    *   データ型: `id` (uuid), `title` (text), `created_at`, `updated_at` (timestamptz)\n*   **messages**: 会話内のメッセージ（発話）を表します。ロール区分がユーザーかアシスタントでチェックされており、引用情報も保持する設計となっています [1][2]。\n    *   データ型: `id` (uuid), `conversation_id` (FK), `role` (text, 'user'/'assistant'), `content` (text)\n\n#### 2. コーパス（取り込み元）関連\n*   **sources**: ローカルファイルや外部ドキュメントを表します [3]。変更検知のためハッシュ値を管理しており、論理削除も対応しています [7]。\n    *   データ型: `id` (uuid), `path` (text), `title` (text), `content_hash` (text), `source_updated_at`, `deleted_at`, アーカイブ用の作成日時/更新日付\n\n#### 3. インデックス（検索単位）関連\n*   **chunks**: 全文検索とベクトル検索を行う単位です [4]。位置情報やメタデータ、埋め込みベクターを格納します[2][5]。\n    *   データ型: `id` (uuid), `source_id` (FK), `position` (int), `content` (text), `embedding` (vector, 例：voyage-4-lite は1024次元), `metadata` (jsonb)\n\n#### 4. オペレーション関連\n*   **ingest_runs**: コーパスの取り込み実行ログです [6]。スクリプト呼び出しやAPIからのトリガー、成功/失敗状態および統計情報を格納します[5][7]。\n    *   データ型: `id` (uuid), `trigger`, `status`, `stats` (jsonb, 追加件数などを含む), `error`, スタート日時,完了日時\n\n以上の通りです。",
        "その中でJSONB型を使っているカラムはどれですか?",
        "grounded",
        "テーブルでJSONB型を使っているカラム",
        ["db_design.md"],
    ),
    (
        "f017",
        "取り込み実行の多重実行はどのように防いでいますか?",
        "取り込み実行の多重実行は、「`status='running'` の行が既に存在するかどうかをチェック」することで防いでいます [1]。具体的には、実行中の `ingest_runs` テーブル上のレコードが存在すれば新規実行を拒否します [2]。また、UI からの再取り込みでもこの仕組みを用い、ジョブキューは導入せずプロセス内の非同期実行で管理されています [7]。",
        "それはどのくらいの時間でタイムアウトとみなされますか?",
        "direct",
        "取り込み実行の多重実行防止におけるタイムアウト・stale判定",
        "コーパスは「status='running'の行の存在チェック」とのみ述べ、タイムアウト・stale判定の時間の記述なし(grep確認済み)",
    ),
    (
        "f018",
        "コンテキストに答えがない場合、生成側はどう応答すべきですか?",
        "提供されたコンテキスト情報に基づき、コンテキストに答えが見つからない際の対応は以下の通りです。\n\n1.  **回答の停止と特定フレーズの使用**: コンテキスト（文脈）の中に質問への答えがない場合や検索結果が 0 件の場合は、生成を停止し、「該当する情報が見つかりませんでした」と返答します [4][5]。\n2.  **出典による説明禁止**: コンテンツ外の主張は行わず、コンテキストに基づいた回答のみを行います [1][3]。\n3.  **評価指標 (Faithfulness)**: 「生成した内容が取得されたコンテキストに忠実か（＝無関係な情報を捏造していないか）」を検証する信頼性指標である「Faithfulness」の観点から、この対応は必須とされています [8][4]。",
        "その場合、ユーザーには何と表示されますか?",
        "grounded",
        "コンテキストに情報がない場合のユーザー向け表示文言",
        ["architecture.md", "requirements.md"],
    ),
    (
        "f019",
        "v1でスコープ外にした主要な機能は何ですか?",
        "v1 でスコープから外された主な機能は以下の通りです。コンテキスト情報によると、これらはすべて将来の拡張として §11 に明示されています [1]。また、AGENTS.md と requirements.md の変更履歴（§5, v0.2）でも SaaS コネクタ・OAuth などがスコープ外へ移動したと記述されており [3][4]、アーキテクチャ設計ではジョブキューや Redis は導入しないことも示されています [7]。\n\n具体的には以下の項目が含まれます：\n*   **SaaS コネクタ**（Notion / Slack / Google Drive）および **OAuth 認証**[1][2][3]\n*   **マルチユーザー/組織テナント** の機能[1]\n*   **ドキュメント単位の ACL**（権限考慮検索）[1]\n*   **エージェンティック RAG**（多段検索・クエリ分解・自己評価ループなど）[1]\n*   **PDF / Office ファイルのパース**（取り込みは Markdown/テキストのみ限定されるため）[1]\n*   **モバイルアプリ** [1]\n*   **ジョブキュー**の実装（Redis, ARQ 等。v0.2 で SaaS 同期がスコープ外となったことなどもあり、将来拡張へ保留されたものを含む）[4][7]",
        "その中でマルチユーザー化に関連するものはどれですか?",
        "grounded",
        "スコープ外項目のうちマルチユーザー関連のもの",
        ["requirements.md"],
    ),
    (
        "f020",
        "パフォーマンスに関する目標値(TTFTなど)はありますか?",
        "はい、パフォーマンスに関する目標値については以下に定められています。\n\n*   **TTFT**（初回トークンまでの生成時間）：p95 で 2.0 秒未満を目標とされています [1]※ただし、実測後に確定するとの注釈があります)。\n*   **検索レイテンシ**: p95 で 500ms 未満（リランクを含めて）が目標とされています [1]。\n\nなお、これらの具体的な数値は実際に測定を行った後の確認を経て最終確定すると記載されています [2], [3], [4], [6][7]",
        "検索のレイテンシ目標はどのくらいですか?",
        "grounded",
        "検索レイテンシのp95目標値",
        ["requirements.md"],
    ),
]
assert len(FOLLOWUP_ITEMS) == 20
_directs = [f for f in FOLLOWUP_ITEMS if f[4] == "direct"]
assert 3 <= len(_directs) <= 5, f"direct期待は3-5件の想定: got {len(_directs)}"


def _split_holdout(ids: list[str], seed: int, holdout_frac: float = 0.3) -> set[str]:
    """idのリストを決定的にシャッフルし、末尾holdout_frac分をholdoutとして返す(0-indexの集合)。"""
    shuffled = list(ids)
    random.Random(seed).shuffle(shuffled)
    holdout_n = round(len(ids) * holdout_frac)
    return set(shuffled[:holdout_n])


def build_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    # corpus: カテゴリ全体でシャッフル(40件 -> holdout 12件 = 30.0%)
    corpus_ids = [item[0] for item in CORPUS_ITEMS]
    corpus_holdout = _split_holdout(corpus_ids, SEED)
    for id_, query, _paths in CORPUS_ITEMS:
        rows.append(
            {
                "id": id_,
                "query": query,
                "history": [],
                "expected_route": "grounded",
                "category": "corpus",
                "split": "holdout" if id_ in corpus_holdout else "calibration",
                "expected_search_query": None,
            }
        )

    # general: ドメインごとに層化(10件 x 4 -> 各holdout 3件 = 全体12件 = 30.0%)
    domains: dict[str, list[str]] = {}
    for id_, query, domain in GENERAL_ITEMS:
        domains.setdefault(domain, []).append(id_)
    general_holdout: set[str] = set()
    for domain, ids in domains.items():
        general_holdout |= _split_holdout(ids, SEED)
    for id_, query, _domain in GENERAL_ITEMS:
        rows.append(
            {
                "id": id_,
                "query": query,
                "history": [],
                "expected_route": "direct",
                "category": "general",
                "split": "holdout" if id_ in general_holdout else "calibration",
                "expected_search_query": None,
            }
        )

    # ambiguous: expected_routeごとに層化(15件grounded->holdout4, 15件direct->holdout5 = 9件 = 30.0%)
    amb_by_route: dict[str, list[str]] = {"grounded": [], "direct": []}
    for id_, query, route, _grounding in AMBIGUOUS_ITEMS:
        amb_by_route[route].append(id_)
    amb_holdout: set[str] = set()
    amb_holdout |= _split_holdout(amb_by_route["grounded"], SEED, holdout_frac=4 / 15)
    amb_holdout |= _split_holdout(amb_by_route["direct"], SEED, holdout_frac=5 / 15)
    for id_, query, route, _grounding in AMBIGUOUS_ITEMS:
        rows.append(
            {
                "id": id_,
                "query": query,
                "history": [],
                "expected_route": route,
                "category": "ambiguous",
                "split": "holdout" if id_ in amb_holdout else "calibration",
                "expected_search_query": None,
            }
        )

    # followup: expected_routeごとに層化(16件grounded->holdout5, 4件direct->holdout1 = 6件 = 30.0%)
    fu_by_route: dict[str, list[str]] = {"grounded": [], "direct": []}
    for id_, *_rest, route, _esq, _grounding in FOLLOWUP_ITEMS:
        fu_by_route[route].append(id_)
    fu_holdout: set[str] = set()
    fu_holdout |= _split_holdout(fu_by_route["grounded"], SEED, holdout_frac=5 / 16)
    fu_holdout |= _split_holdout(fu_by_route["direct"], SEED, holdout_frac=1 / 4)
    for id_, u1, a1, q2, route, esq, _grounding in FOLLOWUP_ITEMS:
        rows.append(
            {
                "id": id_,
                "query": q2,
                "history": [
                    {"role": "user", "content": u1},
                    {"role": "assistant", "content": a1},
                ],
                "expected_route": route,
                "category": "followup",
                "split": "holdout" if id_ in fu_holdout else "calibration",
                "expected_search_query": esq,
            }
        )

    return rows


def main() -> None:
    rows = build_rows()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # サマリ表示(README作成時の突合用)
    from collections import Counter

    cat_split = Counter((r["category"], r["split"]) for r in rows)
    print(f"Wrote {len(rows)} rows to {OUT_PATH}")
    for cat in ["corpus", "general", "ambiguous", "followup"]:
        cal = cat_split[(cat, "calibration")]
        hold = cat_split[(cat, "holdout")]
        total = cal + hold
        pct = (hold / total * 100) if total else 0
        print(f"  {cat}: total={total} calibration={cal} holdout={hold} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
