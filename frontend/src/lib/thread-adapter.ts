import { ThreadState, ThreadMessage } from "@assistant-ui/react";

let activeThreadId: string | undefined = undefined;

export const getActiveThreadId = () => activeThreadId;

export const createThreadAdapter = (): any => {
  return {
    async initialize() {
      const res = await fetch("/api/conversations", {
        method: "POST",
      });
      if (!res.ok) throw new Error("Failed to initialize conversation");
      const data = await res.json();
      activeThreadId = data.id;
      return {
        threadId: data.id,
      };
    },
    async subscribe(callback: any) {
      // For initial load, we fetch the list
      const fetchList = async () => {
        const res = await fetch("/api/conversations");
        if (res.ok) {
          const list = await res.json();
          callback({
            threads: list.map((c: any) => ({
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
    async getHistory(threadId: any) {
      activeThreadId = threadId;
      const res = await fetch(`/api/conversations/${threadId}`);
      if (!res.ok) throw new Error("Failed to fetch conversation history");
      const data = await res.json();
      
      const messages: ThreadMessage[] = data.map((msg: any) => ({
        id: msg.id,
        role: msg.role === "assistant" ? "assistant" : "user",
        createdAt: new Date(msg.created_at),
        content: [{ type: "text", text: msg.content }],
        metadata: {
          citations: msg.citations || [],
        }
      }));
      
      return {
        messages,
      };
    },
  };
};
