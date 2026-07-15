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

async function collectFinalText(chunks: string[]): Promise<string> {
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

  it("ignores unknown SSE event types (e.g. future node_start/route_decided) without breaking the stream", async () => {
    // M7 T3: グラフ導入後、node_start/route_decided/rewrite_result 等の新規イベント型が
    // 将来追加される可能性がある（M7スペック§5.2、T6で実装予定）。バックエンドが未知の
    // イベント型を流しても、フロントはクラッシュせず黙殺し、既知のイベント(token等)の
    // 処理を継続できることを確認する（タスクT3レビュー指摘）
    const text = await collectFinalText([
      'event: node_start\ndata: {"node": "generate"}\n\n',
      'event: route_decided\ndata: {"route": "grounded", "kept": 1, "dropped": 0, "top_score": 0.9}\n\n',
      'event: some_future_event\ndata: "anything"\n\n',
      'event: token\ndata: "H"\n\n',
      'event: token\ndata: "i"\n\n',
    ]);
    expect(text).toBe("Hi");
  });
});
