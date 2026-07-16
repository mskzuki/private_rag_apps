import type {
  ChatModelRunOptions,
  ChatModelRunResult,
} from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createChatAdapter } from "./chat-adapter";

const encoder = new TextEncoder();

function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

function runOptionsFor(text: string): ChatModelRunOptions {
  return {
    messages: [
      {
        id: "u1",
        role: "user",
        content: [{ type: "text", text }],
        createdAt: new Date(),
      },
    ],
    runConfig: {},
    abortSignal: new AbortController().signal,
    context: {},
    unstable_getMessage: () => {
      throw new Error("not used by createChatAdapter");
    },
  } as unknown as ChatModelRunOptions;
}

async function collectFinalResult(
  chunks: string[],
): Promise<ChatModelRunResult | undefined> {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(streamFromChunks(chunks), { status: 200 })),
  );

  const adapter = createChatAdapter("conv-1");
  const generator = adapter.run(runOptionsFor("hi")) as AsyncGenerator<
    ChatModelRunResult,
    void
  >;
  let last: ChatModelRunResult | undefined;
  for await (const update of generator) {
    last = update;
  }

  return last;
}

async function collectFinalText(chunks: string[]): Promise<string> {
  const last = await collectFinalResult(chunks);
  const textPart = last?.content?.find((p) => p.type === "text");
  return textPart?.text ?? "";
}

describe("createChatAdapter SSE parsing", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps a token when its event: and data: lines arrive in the same read() chunk", async () => {
    const text = await collectFinalText(['event: token\ndata: "H"\n\n']);
    expect(text).toBe("H");
  });

  it("does not drop a token when its event: and data: lines are split across separate read() chunks", async () => {
    const text = await collectFinalText(["event: token\n", 'data: "H"\n\n']);
    expect(text).toBe("H");
  });

  it("ignores unknown SSE event types (e.g. node_start/rewrite_result, and truly unknown future events) without breaking the stream", async () => {
    // M7 T6: node_start/rewrite_result はスコープ外(受信のみ・表示なし)。真に未知の
    // イベント型が将来追加されても、フロントはクラッシュせず黙殺し、既知のイベント
    // (token等)の処理を継続できることを確認する（タスクT3レビュー指摘の継続確認）
    const text = await collectFinalText([
      'event: node_start\ndata: {"node": "generate"}\n\n',
      'event: rewrite_result\ndata: {"applied": false, "query": "hi"}\n\n',
      'event: some_future_event\ndata: "anything"\n\n',
      'event: token\ndata: "H"\n\n',
      'event: token\ndata: "i"\n\n',
    ]);
    expect(text).toBe("Hi");
  });

  it("sets metadata.custom.route to 'grounded' when route_decided reports the grounded route", async () => {
    // M7 T6 (docs/specs/m7_adaptive_routing.md §5.2): route_decided を受けて
    // metadata.custom.route にセットする（RouteBadge がこれを読んでバッジ表示する）
    const last = await collectFinalResult([
      'event: route_decided\ndata: {"route": "grounded", "kept": 1, "dropped": 0, "top_score": 0.9}\n\n',
      'event: token\ndata: "H"\n\n',
    ]);
    expect(
      (last?.metadata as { custom?: { route?: string } } | undefined)?.custom
        ?.route,
    ).toBe("grounded");
  });

  it("sets metadata.custom.route to 'direct' when route_decided reports the direct route (top_score: null)", async () => {
    // direct 経路では top_score が null を許容される（スペック§5.2、grade.pyのtop_score定義）
    const last = await collectFinalResult([
      'event: route_decided\ndata: {"route": "direct", "kept": 0, "dropped": 3, "top_score": null}\n\n',
      'event: token\ndata: "H"\n\n',
    ]);
    expect(
      (last?.metadata as { custom?: { route?: string } } | undefined)?.custom
        ?.route,
    ).toBe("direct");
  });
});
