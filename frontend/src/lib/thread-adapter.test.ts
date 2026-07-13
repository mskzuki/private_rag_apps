import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createThreadHistoryAdapter,
  createThreadListAdapter,
} from "./thread-adapter";

function mockFetchJson(
  handler: (
    url: string,
    init?: RequestInit,
  ) => { status: number; body: unknown },
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const { status, body } = handler(url, init);
      return new Response(JSON.stringify(body), { status });
    }),
  );
}

describe("createThreadListAdapter", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("list() maps GET /api/conversations into RemoteThreadListResponse shape", async () => {
    mockFetchJson((url) => {
      expect(url).toBe("/api/conversations");
      return {
        status: 200,
        body: [
          { id: "c1", title: "Hello", updated_at: "2026-07-01T00:00:00Z" },
        ],
      };
    });

    const adapter = createThreadListAdapter();
    const result = await adapter.list();

    expect(result.threads).toEqual([
      {
        status: "regular",
        remoteId: "c1",
        title: "Hello",
        lastMessageAt: new Date("2026-07-01T00:00:00Z"),
      },
    ]);
  });

  it("initialize() maps POST /api/conversations into { remoteId }", async () => {
    mockFetchJson((url, init) => {
      expect(url).toBe("/api/conversations");
      expect(init?.method).toBe("POST");
      return { status: 200, body: { id: "c2", title: "New Chat" } };
    });

    const adapter = createThreadListAdapter();
    const result = await adapter.initialize("local-optimistic-id");

    expect(result.remoteId).toBe("c2");
  });

  it("fetch() maps GET /api/conversations/{id} into RemoteThreadMetadata", async () => {
    mockFetchJson((url) => {
      expect(url).toBe("/api/conversations/c3");
      return {
        status: 200,
        body: { id: "c3", title: "Resumed", messages: [] },
      };
    });

    const adapter = createThreadListAdapter();
    const result = await adapter.fetch("c3");

    expect(result).toEqual({
      status: "regular",
      remoteId: "c3",
      title: "Resumed",
    });
  });

  const outOfScopeMethods = [
    "rename",
    "archive",
    "unarchive",
    "delete",
    "generateTitle",
  ] as const;

  it.each(
    outOfScopeMethods,
  )("%s() rejects because it is out of scope until M4+ (m2_streaming_and_history.md §4.4)", async (method) => {
    const adapter = createThreadListAdapter();
    const fn = adapter[method] as (...args: unknown[]) => Promise<unknown>;
    await expect(fn("id", "arg2")).rejects.toThrow();
  });
});

describe("createThreadHistoryAdapter", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("load() returns an empty repository when there is no active thread yet", async () => {
    const adapter = createThreadHistoryAdapter(undefined);
    const repo = await adapter.load();

    expect(repo.messages).toEqual([]);
  });

  it("load() maps GET /api/conversations/{id} messages into an ExportedMessageRepository", async () => {
    mockFetchJson((url) => {
      expect(url).toBe("/api/conversations/c1");
      return {
        status: 200,
        body: {
          id: "c1",
          title: "Hello",
          messages: [
            {
              id: "m1",
              role: "user",
              content: "hi",
              created_at: "2026-07-01T00:00:00Z",
            },
            {
              id: "m2",
              role: "assistant",
              content: "hello!",
              citations: [{ n: 1, title: "doc", heading: "h" }],
              created_at: "2026-07-01T00:00:01Z",
            },
          ],
        },
      };
    });

    const adapter = createThreadHistoryAdapter("c1");
    const repo = await adapter.load();

    expect(repo.messages).toHaveLength(2);
    expect(repo.messages[0]?.message.role).toBe("user");
    expect(repo.messages[1]?.message.role).toBe("assistant");
    expect(
      (repo.messages[1]?.message.metadata?.custom as { citations?: unknown[] })
        ?.citations,
    ).toEqual([{ n: 1, title: "doc", heading: "h" }]);
  });

  it("append() resolves without making a network request (server persists via /api/chat)", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    const adapter = createThreadHistoryAdapter("c1");
    await adapter.append({
      parentId: null,
      message: {
        id: "m1",
        role: "user",
        content: [{ type: "text", text: "hi" }],
        createdAt: new Date(),
        metadata: { custom: {} },
        attachments: [],
      } as never,
    });

    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
