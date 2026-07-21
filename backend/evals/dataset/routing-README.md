# routing.jsonl — M7 Adaptive Routing eval データセット

M7 タスク T1 の成果物。`docs/specs/26071422-m7_adaptive_routing/spec.md`(rev.3)§7.1/§7.2 と
`.superpowers/sdd/task-T1-brief.md` に基づいて作成した、閾値(THETA)キャリブレーション
と grade 精度検証のためのデータセット。T2 以降(閾値キャリブレーション・実装)が
このファイルを使用する。

本ドキュメントは以下を記録する:

1. カテゴリ定義・ラベリング基準
2. **grounded 期待の全件について根拠ドキュメントのパス**
3. **direct 期待の全件について「コーパスに記述なし」の確認結果**
4. calibration / holdout 分割の方法と乱数 seed(再現可能性)
5. followup カテゴリの実アプリ回答収集手順

対象コーパスは `seed/corpus/`(4 ファイル: `requirements.md`, `architecture.md`,
`db_design.md`, `AGENTS.md`)。判定はすべてこの4ファイルの実文言を人手で確認して
行った(Voyage AI のレート制限が既知の問題であるため、ライブの retrieval API は
判定には使わず、直接 Read/Grep で確認する方針を採った。詳細は §6 参照)。

---

## 1. スキーマ

```json
{"id": str, "query": str, "history": list, "expected_route": "grounded" | "direct",
 "category": "corpus" | "general" | "ambiguous" | "followup",
 "split": "calibration" | "holdout",
 "expected_search_query": str | null}
```

- `history`: corpus/general/ambiguous は常に `[]`。followup のみ
  `[{"role": "user", "content": ...}, {"role": "assistant", "content": ...}]`(1往復)。
- `expected_search_query`: followup のみ非 null。rewrite(condense)評価用に、
  「history を踏まえた理想的な自己完結クエリ」を人手で記述したもの(実際に
  condense() を呼んで得た値ではない。T2 以降の rewrite 実装評価の正解データ)。

生成スクリプト: `backend/evals/generate_routing_dataset.py`(`generate_dataset.py`が
m3_golden.jsonl の初期生成に使われた前例と同じ位置づけの一度きりの構築ツール。
calibration/holdout 分割の再現性を担保するために残してある)。

```bash
cd backend && uv run python evals/generate_routing_dataset.py
```

---

## 2. カテゴリ定義とラベリング基準

| カテゴリ | 件数 | expected_route | 決定方法 |
|---|---|---|---|
| corpus | 40 | grounded(固定) | 既存 e2e eval セット(`m3_golden.jsonl`)由来の質問 27 件 + コーパスの別トピックを根拠にした新規 13 件。全件、根拠パスを本READMEに記録(§3) |
| general | 40 | direct(固定) | 4 ドメイン(プログラミング一般/統計/インフラ一般/RAG一般論)×10件の新規質問。コーパス4ファイルへの grep + 目視で関連記述が無いことを確認(§4) |
| ambiguous | 30 | 記述の実在で機械的決定 | corpus 質問から固有名詞を落として一般化する操作で境界ケースを作成。一般化後の文言だけを見て「コーパスに対応する記述が実在するか」を4ファイル読み込みで判定(§5) |
| followup | 20 | 記述の実在で機械的決定(大半 grounded、direct 期待 4 件) | ターン1(history)は実アプリの生成パイプラインで実際に生成した回答を使用。ターン2(query、指示語による言い換え)を人手で作成し、ambiguous と同じ方法で expected_route を決定(§6) |

**誤判定コストの非対称性**(スペック §3.1)を踏まえ、ambiguous・followup の
判定に迷った場合は grounded 側に倒す方針で判断した(スペックの設計原則と整合)。

---

## 3. corpus(40件)根拠一覧

27 件(`c001`-`c027`)は既存 `backend/evals/dataset/m3_golden.jsonl` の `q01`-`q29` から、
negative/no-answer 系(`q13`, `q15`, `q30`)と history 付き(`q31`)を除いた質問文を
そのまま流用した。残り 13 件(`c028`-`c040`)はコーパスの別トピックを根拠に新規作成した。
全件、根拠パスを実際にコーパスファイルを読み込んで確認済み。

| id | query | 根拠パス |
|---|---|---|
| c001 | 全文検索に使っている拡張は何? | db_design.md, requirements.md |
| c002 | 埋め込みモデルと次元は? | requirements.md, db_design.md |
| c003 | ベクトルインデックスの種類とパラメータは? | db_design.md |
| c004 | ハイブリッド検索で結果を融合する手法は? | architecture.md, db_design.md |
| c005 | なぜ Qdrant ではなく Postgres を選んだ? | architecture.md |
| c006 | チャットUIに採用したライブラリと、ChatKit を選ばなかった理由は? | requirements.md, architecture.md |
| c007 | ドキュメント更新時のチャンクの扱いは? | architecture.md, db_design.md |
| c008 | LLM 呼び出しを許可されているモジュールは? | AGENTS.md |
| c009 | v1 でスコープ外にした主要項目は? | requirements.md |
| c010 | 取り込みの実行経路(CLIとAPI)はどうなっている? | architecture.md |
| c011 | Langfuse がダウンしているとアプリは動かなくなる? | requirements.md |
| c012 | Eval で CI を fail させる条件は? | AGENTS.md |
| c013 | Redis をキャッシュに使っている? | AGENTS.md |
| c014 | チャンキング戦略の前提(Markdown)は? | architecture.md |
| c015 | 生成モデルへのプロンプトはどう管理する? | AGENTS.md |
| c016 | 評価用のゴールデンデータセットの実データはコミットしてよいか? | AGENTS.md |
| c017 | DB マイグレーションツールは何を使っている? | AGENTS.md |
| c018 | ユーザー認証の方式は? | requirements.md |
| c019 | AI モデルへの API キーはどこで設定する? | AGENTS.md |
| c020 | インジェスト時の重複排除はどのように行われる? | db_design.md |
| c021 | PDF の読み込みに対応しているか? | AGENTS.md, requirements.md |
| c022 | 回答に必ず含めるべき情報は? | AGENTS.md |
| c023 | 外部 API のテスト方針は? | AGENTS.md |
| c024 | マルチターンの履歴はどう考慮される? | architecture.md |
| c025 | RRF のパラメータは? | db_design.md |
| c026 | フロントエンドの言語とフレームワークは? | AGENTS.md |
| c027 | コンテキストに見つからない質問をされた時の期待挙動は? | AGENTS.md |
| c028 | リランクに使っているモデルは何ですか? | db_design.md, requirements.md |
| c029 | 会話やメッセージの引用(citations)はどのように保存されますか? | db_design.md, architecture.md |
| c030 | 取り込み実行の多重実行はどのように防いでいますか? | architecture.md, db_design.md |
| c031 | 検索結果が0件のときの挙動はどうなりますか? | architecture.md |
| c032 | SSEで配信されるイベントの種類を教えてください | architecture.md |
| c033 | データベースの主要なテーブル構成を教えてください | db_design.md |
| c034 | パフォーマンス(TTFTなど)の目標値はありますか? | requirements.md |
| c035 | デモモードとは何ですか? | requirements.md |
| c036 | 取り込み対象として認識されるファイル形式は? | requirements.md, architecture.md |
| c037 | ソースファイルが削除された場合、インデックスはどう扱われますか? | architecture.md, db_design.md |
| c038 | チャンクのメタデータには何が含まれますか? | architecture.md, db_design.md |
| c039 | コスト管理(トークン数・費用)はどのように可視化されますか? | requirements.md |
| c040 | 埋め込みの次元を変更する場合、何が必要になりますか? | db_design.md |

注: `seed/corpus/AGENTS.md` はリポジトリ現行の `AGENTS.md`(LLM=OpenAI GPT表記)とは
異なる古いスナップショット(LLM=Anthropic Claude表記)であることに注意。grounded判定・
根拠パスは**シードコーパス側の文言**を基準にした(retrieval が実際に検索する対象は
シードコーパスのため)。

---

## 4. general(40件)ドメイン分布と無関係性確認

4 ドメイン×10件、`g001`-`g040`。全件についてコーパス4ファイルへの `grep`(主要な
固有名詞・専門用語)と目視確認により、関連記述が無いことを確認した。

| ドメイン | 件数 | id範囲 |
|---|---|---|
| プログラミング一般 | 10 | g001-g010 |
| 統計 | 10 | g011-g020 |
| インフラ一般 | 10 | g021-g030 |
| RAG一般論 | 10 | g031-g040 |

質問一覧:

| id | query | ドメイン |
|---|---|---|
| g001 | Pythonのwalrus operator(:=)とは何ですか? | programming |
| g002 | JavaScriptのPromiseとasync/awaitの違いは何ですか? | programming |
| g003 | Gitのrebaseとmergeの違いを教えてください | programming |
| g004 | オブジェクト指向設計におけるSOLID原則とは何ですか? | programming |
| g005 | Pythonのwith文(コンテキストマネージャ)とは何ですか? | programming |
| g006 | 再帰関数とループの使い分けの一般的な指針は? | programming |
| g007 | TypeScriptのジェネリクスとは何ですか? | programming |
| g008 | デザインパターンのFactory Methodパターンとは何ですか? | programming |
| g009 | 単体テストとE2Eテストの一般的な違いは何ですか? | programming |
| g010 | Pythonのデコレータとは何ですか? | programming |
| g011 | t検定とは何ですか? | statistics |
| g012 | 統計学におけるp値の意味を教えてください | statistics |
| g013 | 標準偏差と分散の違いは何ですか? | statistics |
| g014 | ベイズ統計における事前分布とは何ですか? | statistics |
| g015 | 相関と因果の違いを教えてください | statistics |
| g016 | 正規分布の性質について教えてください | statistics |
| g017 | A/Bテストのサンプルサイズは一般的にどう決めますか? | statistics |
| g018 | 中心極限定理とは何ですか? | statistics |
| g019 | 回帰分析における多重共線性とは何ですか? | statistics |
| g020 | 標本と母集団の違いは何ですか? | statistics |
| g021 | Kubernetesのpodとは何ですか? | infra |
| g022 | ロードバランサーの一般的な負荷分散アルゴリズムには何がありますか? | infra |
| g023 | サーキットブレーカーパターンとは何ですか? | infra |
| g024 | DNSの名前解決の流れを教えてください | infra |
| g025 | コンテナオーケストレーションとは何ですか? | infra |
| g026 | TCP/IPにおける3ウェイハンドシェイクとは何ですか? | infra |
| g027 | 一般的なCI/CDパイプラインの構成要素は何ですか? | infra |
| g028 | リバースプロキシとは何ですか? | infra |
| g029 | Blue-Greenデプロイメントとは何ですか? | infra |
| g030 | 一般的なオートスケーリングの仕組みを教えてください | infra |
| g031 | RAGにおけるHyDE(Hypothetical Document Embeddings)とは何ですか? | rag_theory |
| g032 | Self-RAGとは何ですか? | rag_theory |
| g033 | RAG評価のためのRAGASフレームワークとは何ですか? | rag_theory |
| g034 | GraphRAGとは何ですか? | rag_theory |
| g035 | Corrective RAG(CRAG)とは何ですか? | rag_theory |
| g036 | RAGにおけるsentence-window retrievalとは何ですか? | rag_theory |
| g037 | Late chunking(埋め込み後にチャンク化する手法)とは何ですか? | rag_theory |
| g038 | RAGパイプラインにおける埋め込みモデルのfine-tuningは一般的にどのように行いますか? | rag_theory |
| g039 | マルチベクトル検索(ColBERT等)とは何ですか? | rag_theory |
| g040 | RAGにおけるコンテキスト圧縮(contextual compression)とは何ですか? | rag_theory |

### 確認手順と注意点

`seed/corpus/*.md` に対し、各質問のキーワード(英語技術用語・統計用語等)を
`grep -l` で検索し、ヒットした場合は文脈を目視確認した。

- インフラ一般ドメインは元々 `CDN` を含む質問(CDNの仕組み)を用意していたが、
  `architecture.md`(ChatKit不採用理由の一節で「OpenAI CDN 依存」という**言及のみ**
  ヒットした)ため、無関係性が疑義なく確認できる `g023`(サーキットブレーカー
  パターン)に差し替えた。CDNの「仕組み」自体の説明はコーパスに無いが、紛れの
  無いテストケースにするため差し替えを選んだ。
- RAG一般論ドメインは、コーパス自体が RAG プロジェクトの設計文書であるため
  特に注意した。`requirements.md` §3 に用語集(RRF・リランク・ハイブリッド検索・
  チャンク・埋め込み・Faithfulness・TTFT を定義)があり、`requirements.md` §11 に
  「エージェンティックRAG(クエリ分解/多段検索/自己評価ループ)」が将来拡張として
  **名前だけ**登場する。これらの用語(および `クロスエンコーダ`)を general の
  質問には使わず、コーパスに一切登場しない具体的な技法(HyDE, Self-RAG, RAGAS,
  GraphRAG, Corrective RAG, sentence-window retrieval, late chunking, ColBERT,
  contextual compression)を選定した(grep で無ヒットを確認済み)。「用語は
  登場するが説明が無い」という境界ケースは general ではなく ambiguous
  カテゴリ(`a012`, `a013`, `a026` 等)で扱う。

---

## 5. ambiguous(30件)判断根拠一覧

作成手順(ブリーフの実装ノート通り): corpus の質問から固有名詞を落として
一般化する操作で境界ケースを作成した。一般化後の文言**だけ**を見て、
「コーパスに対応する記述が実在するか」を4ファイルの読み込みで機械的に判定した
(判定者の主観による「一般的にどうあるべきか」の判断は入れない)。

15件は「固有名詞は消えたが対応する設計判断の記述が実在する」grounded 側の
境界ケース、15件は「採用された**値**は書かれているが一般的な決定方法・手法の
説明は書かれていない」direct 側の境界ケース。

### 5.1 grounded 側(15件)

| id | query | 根拠パス |
|---|---|---|
| a001 | ベクトルDBとRDBを統合する設計を選ぶ理由にはどのようなものがある? | architecture.md |
| a002 | 拡張1つで完結する軽量な日本語全文検索の方式にはどのようなものがある? | db_design.md |
| a003 | チャットの基本UXを自前実装せず既存ライブラリに任せる場合の判断基準は? | architecture.md |
| a004 | 更新のあるデータセットに向いた、事前学習不要のベクトルインデックス手法は? | db_design.md |
| a005 | スコアスケールの異なる複数の検索結果を安全に統合する一般的な手法は? | architecture.md |
| a006 | ドキュメント更新時に部分更新ではなく全置換を選ぶ判断基準は? | architecture.md |
| a007 | 検索の再現率と精度をどう役割分担させるのが定番構成か? | architecture.md |
| a008 | 取り込みにジョブキュー基盤を持たない設計はどのような場合に妥当か? | architecture.md, requirements.md |
| a009 | 単一ユーザー向けMVPでOAuth認証を省略する判断はどう正当化されるか? | requirements.md |
| a010 | 埋め込みベンダーとLLMベンダーを同一にする組み合わせのメリットは? | requirements.md |
| a011 | 全文検索においてbigram方式と形態素解析方式のどちらを使うべきか? | requirements.md, db_design.md |
| a012 | Faithfulness(忠実性)とは何を指す指標ですか? | requirements.md |
| a013 | TTFTとは何の略で、何を指しますか? | requirements.md |
| a014 | 埋め込みモデルやチャンキング戦略を変更した場合、何をセットで行う必要があるか? | AGENTS.md, db_design.md |
| a015 | コンテキストにない主張をさせないためにプロンプトで工夫すべきことは? | architecture.md, requirements.md |

`a012`/`a013` は「一見一般的な用語定義の質問」に見えるが、`requirements.md` §3
の用語集に直接定義があるため grounded とした(ambiguous カテゴリの狙い通りの
境界ケース)。

### 5.2 direct 側(15件)

| id | query | 「記述なし」の根拠 |
|---|---|---|
| a016 | HNSWのefConstructionはどう決めるべきか? | 採用値(m=16, ef_construction=64)は db_design.md にあるが、一般的なチューニング指針の記述なし(スペック §7.1 の ambiguous 例そのもの) |
| a017 | RRFのk値は一般的にどのように調整するのが望ましいか? | 採用値(k=60)は db_design.md にあるが、調整方法の一般論の記述なし |
| a018 | チャンクサイズ(トークン数)を決める一般的な指針は? | 採用値(512トークン/15%オーバーラップ)は architecture.md にあるが、決め方の一般論の記述なし |
| a019 | リランクのtop_kをいくつに設定するのが適切かの一般的な判断基準は? | 採用値(top_k=8)は db_design.md にあるが、一般的な判断基準の記述なし |
| a020 | ベクトル検索の候補数をどう決めるべきかの一般的な指針は? | 採用値(candidate_k=50)は architecture.md にあるが、決め方の一般論の記述なし |
| a021 | content_hashの計算に使うべきハッシュアルゴリズムは何が適切か? | content_hashを比較する旨の記述はあるが、アルゴリズム名(SHA-256等)の言及なし |
| a022 | 埋め込みの次元数は一般的に何次元が最適か? | 採用値(1024次元)は db_design.md にあるが、一般的な最適次元数の議論なし |
| a023 | LLMの温度パラメータ(temperature)は一般的にどう設定すべきか? | コーパス中に temperature パラメータの記述なし(grep確認済み) |
| a024 | APIのレート制限には一般的にどう対処するのが望ましいか? | コーパス中にレート制限対処の記述なし(grep確認済み) |
| a025 | ストリーミング表示の体感速度を上げる一般的なバッファリングの工夫は? | SSEイベント種別の記述はあるが、バッファリング戦略の記述なし |
| a026 | クロスエンコーダ型リランクの速度対精度のトレードオフは一般的にどう考えられるか? | 「クロスエンコーダ」の用語自体は requirements.md §3 用語集にあるが、速度対精度のトレードオフ論は記述なし |
| a027 | RAGにおいて引用の粒度(文単位か段落単位か)は一般的にどう決めるべきか? | citation構造の記述はあるが、粒度の決め方の一般論は記述なし |
| a028 | 会話履歴を要約してプロンプトに含める際の一般的なベストプラクティスは? | トークン予算内で切り詰める旨の記述はあるが、要約のベストプラクティス論は記述なし |
| a029 | ベクトルインデックスの再構築(reindex)は一般的にどのくらいの頻度で行うべきか? | コーパス中に reindex 頻度の記述なし(grep確認済み) |
| a030 | マルチテナントSaaSでのAPIキー管理のベストプラクティスは? | 本プロジェクトは単一ユーザー前提でマルチテナントは対象外(requirements.md)。マルチテナントAPIキー管理の一般論の記述はなし |

---

## 6. followup(20件)収集手順と一覧

### 6.1 実アプリ回答の収集手順(ターン1)

スペック §7.1 の要件「history に含める assistant 応答は、実アプリで生成した実物を
記録して使う(人工的な応答文は照応解決の難度を歪める)」を満たすため、以下の手順で
収集した。**LLM が生成した文章は一切編集・整形せず、そのまま採用している。**

1. 前提: `seed/corpus` の4ファイルを worktree からホスト側 `make demo` で実際に
   ingestion 済みの状態にした(`/api/sources` で4ソース・113チャンクを確認)。
2. `settings.llm_provider` は事前に `ollama` に設定済みだった(`.env` の
   `LLM_PROVIDER=ollama`、稼働中の API コンテナにも反映済みであることを
   `docker exec ... printenv` で確認)。ただし `.env` の `LLM_MODEL`/`CONDENSE_MODEL`
   が `gpt-5.4-nano`(OpenAI用モデル名)のまま残っており、ローカル Ollama に
   pull 済みなのは `qwen3.5:9b` のみだったため、収集スクリプト実行時に限り
   **プロセス環境変数**で `LLM_MODEL=qwen3.5:9b` / `CONDENSE_MODEL=qwen3.5:9b` を
   上書きした(`.env` ファイル自体は変更していない。pydantic-settings は実環境
   変数を `.env` より優先するため、この上書きは実行プロセスのみに閉じる)。
3. **HTTPサーバ(API)は経由していない。** 稼働中の docker コンテナは main
   リポジトリのディレクトリから起動されたものでポート(8000/5432)が専有されて
   おり、worktree から別途 `docker compose up` すると衝突するため、代わりに
   `api/main.py` のchatハンドラが実際に呼んでいるのと**全く同じ関数**
   (`private_rag_apps.retrieval.searcher.retrieve_context` →
   `private_rag_apps.generation.generator.generate_answer_stream`。
   コードは無変更)を、使い捨てスクリプトから直接呼び出した。履歴が無い最初の
   ターンなので実アプリの挙動と同じく `condense()` は呼んでいない
   (`api/main.py` も `existing_messages_count == 0` の場合は condense をスキップする)。
4. Voyage API のレート制限(3RPM)対策として、質問ごとに最低28秒のsleepを
   挟んで逐次実行した(429時は90秒backoffで最大2回リトライする設計としたが、
   **実際には20/20件とも1回目の試行で成功し、429は一度も発生しなかった**)。
5. 実行日時: 2026-07-14 23:18–23:34(JST)。使用モデル: Ollama `qwen3.5:9b`
   (`reasoning.effort=none` を指定。既存コードの ollama 分岐をそのまま使用)。

収集に使用したスクリプトは一度きりの調査用のためリポジトリにはコミットして
いない(scratchpad 上で実行)。実際に得られた回答全文は、`generate_routing_dataset.py`
の `FOLLOWUP_ITEMS` にそのまま埋め込んで保存してあり、それが一次記録となる。

### 6.2 ターン2(query)の作成方法

ターン1の実回答の内容を踏まえ、自然な指示語(「それの」「その場合」等)を含む
フォローアップ質問を人手で作成した。`expected_route` と `expected_search_query` は
ambiguous と同じ方法(コーパスに当該記述が実在するかを4ファイル読み込みで機械的に
判定)で決定した。

**direct 期待は4件**(`f004`, `f011`, `f012`, `f017`。要件の3-5件の範囲内)。
このうち `f004`/`f011` は話題を無関係な一般知識へ明示的に転換するパターン
(rewrite が誤って前の話題を引きずらないかの検出)、`f012`/`f017` は指示語自体は
正しく解決できるが、解決した先の具体的な問いにコーパスが答えていないパターン
(grade がここで正しく direct に倒せるかの検出)の2種を含めた。

### 6.3 一覧

| id | turn1(実アプリ回答の元質問) | turn2(query) | expected_route | expected_search_query | 根拠 |
|---|---|---|---|---|---|
| f001 | 全文検索に使っている拡張は何ですか? | それの代替として検討された案はありますか? | grounded | pg_bigmの代替候補となる全文検索エンジン | db_design.md, requirements.md |
| f002 | ベクトルインデックスの種類とパラメータを教えてください | なぜその方式を採用したのですか? | grounded | HNSWを採用した理由 | db_design.md |
| f003 | ハイブリッド検索で結果を融合する手法は何ですか? | それのパラメータは? | grounded | RRFのパラメータ(k)の値 | db_design.md |
| f004 | なぜQdrantではなくPostgresを選んだのですか? | ところで、一般的なRDBのACID特性とは何ですか? | direct | RDBのACID特性とは | コーパス中にACIDの記述なし(grep確認済み)。話題転換パターン |
| f005 | 埋め込みモデルと次元数を教えてください | それのコストはどれくらいですか? | grounded | Voyageの埋め込みAPIのコスト(無料枠) | requirements.md |
| f006 | ドキュメント更新時のチャンクの扱いはどうなっていますか? | 削除されたファイルの場合はどうなりますか? | grounded | ソースファイルが削除された場合のチャンクの扱い | architecture.md, db_design.md |
| f007 | LLM呼び出しを許可されているモジュールはどこですか? | 埋め込みの呼び出しはどうですか? | grounded | 埋め込み呼び出しが許可されているモジュール | AGENTS.md |
| f008 | RRFのパラメータ(k)の既定値はいくつですか? | その値を変える場合、設定はどこで行いますか? | grounded | RRFのkなどパラメータの設定変更方法 | architecture.md, AGENTS.md |
| f009 | チャンキング戦略のトークン数の目安を教えてください | オーバーラップの割合はどれくらいですか? | grounded | チャンクのオーバーラップ率 | architecture.md |
| f010 | インジェスト時の重複排除はどのように行われますか? | ファイルが変更された場合はどう処理されますか? | grounded | 変更されたファイルのチャンク再生成処理 | architecture.md, db_design.md |
| f011 | リランクに使っているモデルは何ですか? | ちなみに一般的なAPIのバージョニング戦略にはどんなものがありますか? | direct | APIのバージョニング戦略とは | コーパス中にAPIバージョニングの記述なし(grep確認済み)。話題転換パターン |
| f012 | マルチターンの会話履歴はどのように考慮されますか? | それは何ターン分の履歴を見ますか? | direct | クエリ書き換えで考慮する会話履歴のターン数 | architecture.mdは「トークン予算内で切り詰め」と述べるのみでターン数Nの記述なし。指示語解決先が未記載パターン |
| f013 | 取り込みの実行経路(CLIとAPI)はどうなっていますか? | APIから実行した場合の完了確認はどうすればいいですか? | grounded | 取り込み実行状況の確認方法(API) | architecture.md |
| f014 | チャットUIに採用したライブラリを教えてください | それを選んだ決め手は何ですか? | grounded | assistant-uiを採用した決め手 | requirements.md, architecture.md |
| f015 | 検索結果が0件のときの挙動はどうなりますか? | その場合、生成モデルは何も呼ばれないのですか? | grounded | 検索結果0件の場合に生成処理は実行されるか | architecture.md |
| f016 | データベースのテーブル構成を教えてください | その中でJSONB型を使っているカラムはどれですか? | grounded | テーブルでJSONB型を使っているカラム | db_design.md |
| f017 | 取り込み実行の多重実行はどのように防いでいますか? | それはどのくらいの時間でタイムアウトとみなされますか? | direct | 取り込み実行の多重実行防止におけるタイムアウト・stale判定 | コーパスは「status='running'の行の存在チェック」と述べるのみでタイムアウト・stale判定の記述なし。指示語解決先が未記載パターン |
| f018 | コンテキストに答えがない場合、生成側はどう応答すべきですか? | その場合、ユーザーには何と表示されますか? | grounded | コンテキストに情報がない場合のユーザー向け表示文言 | architecture.md, requirements.md |
| f019 | v1でスコープ外にした主要な機能は何ですか? | その中でマルチユーザー化に関連するものはどれですか? | grounded | スコープ外項目のうちマルチユーザー関連のもの | requirements.md |
| f020 | パフォーマンスに関する目標値(TTFTなど)はありますか? | 検索のレイテンシ目標はどのくらいですか? | grounded | 検索レイテンシのp95目標値 | requirements.md |

実際の assistant 応答全文(実アプリ生成)は `routing.jsonl` の各 `f0XX` 行の
`history[1].content` を参照。

---

## 7. calibration / holdout 分割

- **乱数 seed: `20260714`**(本日日付を固定値として使用。`generate_routing_dataset.py`
  の `SEED` 定数)。`random.Random(SEED).shuffle()` で決定的にシャッフルした
  リストの先頭70%を calibration、末尾30%を holdout とする。
- 各カテゴリの件数を 40/40/30/20 に揃えたことで、holdout件数(12/12/9/6)が
  **誤差なく厳密に30.0%**になるようにした。
- 層化の単位:
  - **corpus**: カテゴリ全体で1回シャッフル(全件 grounded のため経路による
    層化は不要)
  - **general**: ドメインごと(10件×4)に層化してからシャッフル。各ドメイン
    holdout 3件 → 合計12件(全ドメインが両方のsplitに均等に現れる)
  - **ambiguous**: `expected_route` ごと(grounded 15件・direct 15件)に層化。
    grounded は 15件×30%=4.5 → holdout 4件、direct は 15件×30%=4.5 → holdout
    5件、合計 9件(=30.0%)。両 split に grounded/direct が両方含まれるようにし、
    holdout上の「grounded見逃し」「direct誤り」の両指標が計算可能な最小件数を
    確保した
  - **followup**: 同様に `expected_route` ごと(grounded 16件・direct 4件)に
    層化。grounded は holdout 5件、direct は holdout 1件、合計6件(=30.0%)
- 再現方法: `cd backend && uv run python evals/generate_routing_dataset.py` を
  実行すると、本READMEに記載の通りの分割で `routing.jsonl` が再生成される。

実績(`generate_routing_dataset.py` 実行時の出力):

| category | total | calibration | holdout | holdout比率 |
|---|---|---|---|---|
| corpus | 40 | 28 | 12 | 30.0% |
| general | 40 | 28 | 12 | 30.0% |
| ambiguous | 30 | 21 | 9 | 30.0% |
| followup | 20 | 14 | 6 | 30.0% |

---

## 8. 既知の制約・注記

- followup の 20 件はすべて実アプリ生成の収集に成功した(目標20件に対し
  20/20達成。429エラーは1件も発生しなかった)。当初は Voyage のレート制限
  (3RPM)により未達の可能性を見込み、45–60分のタイムボックスと90秒backoff
  リトライを用意していたが、質問間に28秒のsleepを挟む設計だけで十分だった。
- ambiguous・followup の expected_route 判定はすべて本タスク実行者による
  コーパス4ファイルの直読・grep に基づく。将来 T2 でのキャリブレーション時に
  実際の rerank score 分布と照らして疑わしい項目が見つかった場合は、本READMEの
  該当行を更新すること。
- `expected_search_query`(followup)は「理想的な rewrite 結果」を人手で記述した
  正解データであり、実際に `condense()` を呼び出して得た値ではない
  (`condense()` の評価は T2 以降のスコープ)。
- コーパス側 `seed/corpus/AGENTS.md` はリポジトリ現行の `AGENTS.md` と内容が
  異なる(LLMベンダー表記など)。本データセットの grounded 判定は常にシード
  コーパス側の文言を基準にしている(§3 注記参照)。
