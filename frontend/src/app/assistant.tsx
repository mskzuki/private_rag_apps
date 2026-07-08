"use client";

import { useMemo } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  useRemoteThreadListRuntime,
} from "@assistant-ui/react";
import { Thread } from "@/components/assistant-ui/thread";
import { ThreadList } from "@/components/assistant-ui/thread-list";
import { createChatAdapter } from "@/lib/chat-adapter";
import { createThreadAdapter, getActiveThreadId } from "@/lib/thread-adapter";

export const Assistant = () => {
  const threadAdapter = useMemo(() => createThreadAdapter(), []);
  const runtime = useRemoteThreadListRuntime({
    adapter: threadAdapter,
    runtimeHook: () => {
      const threadId = getActiveThreadId();
      // eslint-disable-next-line react-hooks/rules-of-hooks
      const chatAdapter = useMemo(() => createChatAdapter(threadId), [threadId]);
      // eslint-disable-next-line react-hooks/rules-of-hooks
      return useLocalRuntime(chatAdapter);
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="flex h-dvh w-full bg-background text-foreground">
        <div className="w-64 border-r overflow-y-auto">
          <ThreadList />
        </div>
        <div className="flex-1 overflow-hidden relative">
          <Thread />
        </div>
      </div>
    </AssistantRuntimeProvider>
  );
};
