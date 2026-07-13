import type {
  RemoteThreadListAdapter,
  ThreadHistoryAdapter,
  ThreadMessageLike,
} from "@assistant-ui/react";
import { ExportedMessageRepository } from "@assistant-ui/react";
import { useActiveThreadStore } from "@/lib/active-thread-store";

type ConversationSummary = {
  id: string;
  title: string;
  updated_at: string;
};

type ConversationMessage = {
  id: string;
  role: string;
  created_at: string;
  content: string;
  citations?: unknown[];
};

type ConversationDetail = {
  id: string;
  title: string;
  messages: ConversationMessage[];
};

// リネーム/アーカイブ/削除の UI は M2 では出さない（m2_streaming_and_history.md §4.4/§5.4）。
// バックエンドにも対応エンドポイントが無いため、呼ばれた場合は明示的に reject する。
const rejectNotSupported = (): Promise<never> =>
  Promise.reject(
    new Error(
      "rename/archive/unarchive/delete/generateTitle are out of scope until M4+ (see docs/specs/m2_streaming_and_history.md §4.4)",
    ),
  );

async function fetchConversationDetail(
  id: string,
): Promise<ConversationDetail> {
  const res = await fetch(`/api/conversations/${id}`);
  if (!res.ok) throw new Error(`Failed to fetch conversation ${id}`);
  return res.json();
}

export const createThreadListAdapter = (): RemoteThreadListAdapter => ({
  async list() {
    const res = await fetch("/api/conversations");
    if (!res.ok) throw new Error("Failed to fetch conversations");
    const conversations: ConversationSummary[] = await res.json();
    return {
      threads: conversations.map((c) => ({
        status: "regular" as const,
        remoteId: c.id,
        title: c.title,
        lastMessageAt: new Date(c.updated_at),
      })),
    };
  },

  async initialize() {
    const res = await fetch("/api/conversations", { method: "POST" });
    if (!res.ok) throw new Error("Failed to initialize conversation");
    const data: { id: string } = await res.json();
    useActiveThreadStore.getState().setActiveThreadId(data.id);
    return { remoteId: data.id, externalId: undefined };
  },

  async fetch(threadId) {
    const detail = await fetchConversationDetail(threadId);
    return {
      status: "regular" as const,
      remoteId: detail.id,
      title: detail.title,
    };
  },

  rename: rejectNotSupported,
  archive: rejectNotSupported,
  unarchive: rejectNotSupported,
  delete: rejectNotSupported,
  generateTitle: rejectNotSupported,
});

export const createThreadHistoryAdapter = (
  threadId: string | undefined,
): ThreadHistoryAdapter => ({
  async load() {
    if (!threadId) {
      return ExportedMessageRepository.fromArray([]);
    }
    const detail = await fetchConversationDetail(threadId);
    const messages: ThreadMessageLike[] = detail.messages.map((msg) => ({
      id: msg.id,
      role: msg.role === "assistant" ? "assistant" : "user",
      createdAt: new Date(msg.created_at),
      content: msg.content,
      metadata: { custom: { citations: msg.citations ?? [] } },
    }));
    return ExportedMessageRepository.fromArray(messages);
  },

  async append() {
    // /api/chat がストリーム完了時にユーザー/アシスタント両方のメッセージを
    // サーバー側で永続化する（backend/api/main.py の Bulk Save）ため、
    // クライアント側から二重に保存する必要はない。
  },
});
