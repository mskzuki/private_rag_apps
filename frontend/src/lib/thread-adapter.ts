import type { ThreadMessage } from "@assistant-ui/react";
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

// biome-ignore lint/suspicious/noExplicitAny: shaped for the legacy subscribe/getHistory adapter contract, which predates the currently installed @assistant-ui RemoteThreadListAdapter interface (list/rename/archive/unarchive/delete/generateTitle/fetch) — see thread history follow-up
export const createThreadAdapter = (): any => {
  return {
    async initialize() {
      const res = await fetch("/api/conversations", {
        method: "POST",
      });
      if (!res.ok) throw new Error("Failed to initialize conversation");
      const data = await res.json();
      useActiveThreadStore.getState().setActiveThreadId(data.id);
      return {
        threadId: data.id,
      };
    },
    async subscribe(callback: (payload: { threads: unknown[] }) => void) {
      // For initial load, we fetch the list
      const fetchList = async () => {
        const res = await fetch("/api/conversations");
        if (res.ok) {
          const list: ConversationSummary[] = await res.json();
          callback({
            threads: list.map((c) => ({
              id: c.id,
              title: c.title,
              isMain: false,
              createdAt: new Date(c.updated_at),
              updatedAt: new Date(c.updated_at),
            })),
          });
        }
      };

      fetchList();

      // We don't have a websocket/SSE for thread list updates, so we just poll or refresh manually.
      // But we can trigger fetchList when a new thread is created if we have an event emitter.
      // For now, an empty unsubscribe is fine.
      return {
        unsubscribe() {},
      };
    },
    async getHistory(threadId: string) {
      useActiveThreadStore.getState().setActiveThreadId(threadId);
      const res = await fetch(`/api/conversations/${threadId}`);
      if (!res.ok) throw new Error("Failed to fetch conversation history");
      const data: ConversationMessage[] = await res.json();

      const messages: ThreadMessage[] = data.map((msg) => ({
        id: msg.id,
        role: msg.role === "assistant" ? "assistant" : "user",
        createdAt: new Date(msg.created_at),
        content: [{ type: "text", text: msg.content }],
        metadata: {
          custom: { citations: msg.citations || [] },
        },
      })) as unknown as ThreadMessage[];

      return {
        messages,
      };
    },
  };
};
