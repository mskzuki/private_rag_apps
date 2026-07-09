"use client";

import { useMemo } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  useRemoteThreadListRuntime,
} from "@assistant-ui/react";
import { Thread } from "@/components/assistant-ui/thread";
import { ThreadList } from "@/components/assistant-ui/thread-list";
import { useActiveThreadStore } from "@/lib/active-thread-store";
import { createChatAdapter } from "@/lib/chat-adapter";
import { createThreadAdapter } from "@/lib/thread-adapter";

export const Assistant = () => {
  const threadAdapter = useMemo(() => createThreadAdapter(), []);
  const runtime = useRemoteThreadListRuntime({
    adapter: threadAdapter,
    runtimeHook: () => {
      // biome-ignore lint/correctness/useHookAtTopLevel: runtimeHook is itself a hook invoked by useRemoteThreadListRuntime, per assistant-ui's API contract
      const threadId = useActiveThreadStore((s) => s.activeThreadId);
      // biome-ignore lint/correctness/useHookAtTopLevel: runtimeHook is itself a hook invoked by useRemoteThreadListRuntime, per assistant-ui's API contract
      const chatAdapter = useMemo(
        () => createChatAdapter(threadId),
        [threadId],
      );
      // biome-ignore lint/correctness/useHookAtTopLevel: runtimeHook is itself a hook invoked by useRemoteThreadListRuntime, per assistant-ui's API contract
      return useLocalRuntime(chatAdapter);
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="bg-background text-foreground flex h-dvh w-full">
        <div className="w-64 overflow-y-auto border-r">
          <ThreadList />
        </div>
        <div className="relative flex-1 overflow-hidden">
          <Thread />
        </div>
      </div>
    </AssistantRuntimeProvider>
  );
};
