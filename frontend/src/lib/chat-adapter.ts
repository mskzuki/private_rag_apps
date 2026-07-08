import { ChatModelAdapter } from "@assistant-ui/react";

export const createChatAdapter = (conversationId?: string): ChatModelAdapter => {
  return {
    async *run(options) {
      console.log("RUN OPTIONS:", Object.keys(options), options);
      const { messages, abortSignal } = options;
      const lastMessage = messages[messages.length - 1];
      if (!lastMessage || lastMessage.role !== "user") {
        return;
      }
      
      const userMessage = lastMessage.content[0]?.type === "text" ? lastMessage.content[0].text : "";

      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          conversation_id: conversationId,
          message: userMessage,
        }),
        signal: abortSignal,
      });

      if (!response.ok) {
        throw new Error(`Failed to send message: ${response.statusText}`);
      }
      
      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error("No response body");
      }

      const decoder = new TextDecoder("utf-8");
      let textBuffer = "";
      let accumulatedText = "";
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const metadata: any = { citations: [] };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        textBuffer += decoder.decode(value, { stream: true });
        
        const lines = textBuffer.split("\n");
        textBuffer = lines.pop() || ""; // Keep the last incomplete line in buffer

        let currentEvent = "";

        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent = line.substring(7).trim();
          } else if (line.startsWith("data: ")) {
            const dataStr = line.substring(6).trim();
            if (currentEvent === "token") {
              try {
                const parsed = JSON.parse(dataStr);
                if (parsed.delta) {
                  accumulatedText += parsed.delta;
                } else if (typeof parsed === "string") {
                  accumulatedText += parsed;
                }
              } catch (e) {
                // If the parse fails, assume it's raw string (e.g. basic fallback)
                accumulatedText += dataStr.replace(/^"|"$/g, '');
              }
              
              yield {
                content: [{ type: "text", text: accumulatedText }],
                metadata: metadata
              };
            } else if (currentEvent === "citations") {
              try {
                const parsed = JSON.parse(dataStr);
                // backend yields {"event": "citations", "data": json.dumps(data)}
                // wait, if it yields json.dumps({"citations": []}), it might be a double encoded string or just an object
                metadata.citations = parsed?.citations ?? parsed ?? []; 
                yield {
                  content: [{ type: "text", text: accumulatedText }],
                  metadata: metadata
                };
              } catch (e) {
                console.error("Failed to parse citations", e);
              }
            } else if (currentEvent === "error") {
              throw new Error(`Server returned error: ${dataStr}`);
            } else if (currentEvent === "done") {
              // End of stream, could extract conversation_id if we want
              // but we rely on RemoteThreadListAdapter to manage that
            }
          }
        }
      }
    },
  };
};
