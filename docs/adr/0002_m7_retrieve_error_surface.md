# ADR 0002: retrieve失敗時のエラー経路をHTTP 500からSSE errorイベントへ変更する

- Status: Accepted
- Date: 2026-07-15
- 関連: `docs/specs/m7_adaptive_routing.md`（rev.3 §6 ストリーミング統合）、`docs/specs/m7_tasklist.md`（T3）
- 契機: T3レビューでのCritical指摘（`.superpowers/sdd/task-T3-report.md`）

## Context

T3の完了条件は「このタスク完了時点で外形的挙動は現行と同等であること」だった。しかし、`/chat`エンドポイントをLangGraphの`graph.astream(stream_mode="custom")`経由に置き換えたことで、retrieveフェーズの例外処理経路が変化した。

- **変更前**: `retrieve_context()`はSSEレスポンス開始前（`try`の外）で同期実行されており、例外は生のHTTP 500として返っていた
- **変更後**: retrieveはグラフの1ノードとしてSSEストリーム開始後（`event_generator()`内の`try`配下）に実行されるため、例外は`error` SSEイベント（HTTP 200 OK）として通知される

これはT3レビューでCriticalとして指摘され、コントローラーからユーザーに判断を仰いだ。

## Decision

この挙動変化を**受け入れる**。実装は変更しない（peek-ahead方式等でHTTP 500を維持する追加実装は行わない）。

理由:

1. `error`イベントは新設のSSEイベント型ではなく、既存プロトコル（generate失敗時に元々使われていた）の一部。retrieve失敗もgenerate失敗も同一のエラーチャンネルに統一されることは、むしろ一貫性の向上と捉えられる
2. フロント（assistant-ui）は元々`error`イベントを処理する実装であり、エンドユーザー体験としての実質的な差は小さいと想定される
3. 本プロジェクトの可観測性はLangfuseトレース中心であり、HTTPステータスコードでの監視・アラートに依存する仕組みは存在しない
4. peek-ahead方式（ストリーム開始前にグラフの最初の1手を試験実行し、retrieveフェーズの例外を先出しで捕捉する）は実装可能だが、「LangGraphは薄く使う」というスペックの設計原則（rev.3 §3.3）に反する複雑さを持ち込む割に得られる利益が小さい

## Consequences

- HTTPステータスコードのみを見て成否判定する外部クライアント・監視ツールがもし将来追加される場合、retrieveフェーズの失敗を検知できない（SSEストリーム内の`error`イベントをパースする必要がある）。現時点でそのような外部クライアントは存在しない
- T6以降でSSEイベント設計を見直す際、この挙動を前提として設計すること
- 同様の「LangGraph化に伴う既存挙動からの意図しない逸脱」が今後のタスク（T4以降）でも発生しうる。発見した場合は本ADRと同様に、実装を先に進めず一旦立ち止まってユーザーに諮ること
