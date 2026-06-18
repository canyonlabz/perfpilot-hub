"use client";

import { useState, useEffect, useRef } from "react";
import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";
import { fetchMessages } from "@/lib/api";
import type { Message } from "@/lib/types";

interface ChatPanelProps {
  threadId: string | null;
}

function extractText(content: string | Record<string, unknown>): string {
  if (typeof content === "string") return content;
  if (content && typeof content.text === "string") return content.text;
  if (content && typeof content.content === "string") return content.content;
  return JSON.stringify(content);
}

interface HistoryMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

function MessageBubble({ msg }: { msg: HistoryMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-3`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-2 text-sm whitespace-pre-wrap ${
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-foreground"
        }`}
      >
        {msg.content}
      </div>
    </div>
  );
}

function ChatHistory({ threadId }: { threadId: string }) {
  const [messages, setMessages] = useState<HistoryMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLoading(true);
    setMessages([]);

    async function load() {
      try {
        const data = await fetchMessages(threadId);
        const history: HistoryMessage[] = data.messages
          .filter((m: Message) => m.role === "user" || m.role === "assistant")
          .map((m: Message) => ({
            id: String(m.id),
            role: m.role as "user" | "assistant",
            content: extractText(m.content),
          }))
          .filter((m: HistoryMessage) => m.content.length > 0);
        setMessages(history);
      } catch (err) {
        console.error("Failed to load thread history:", err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [threadId]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-muted-foreground animate-pulse">
          Loading conversation...
        </p>
      </div>
    );
  }

  if (messages.length === 0) return null;

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4">
      {messages.map((msg) => (
        <MessageBubble key={msg.id} msg={msg} />
      ))}
      <div className="border-t my-3 pt-2">
        <p className="text-xs text-muted-foreground text-center">
          End of history — continue the conversation below
        </p>
      </div>
    </div>
  );
}

export function ChatPanel({ threadId }: ChatPanelProps) {
  if (!threadId) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground">
        <p className="text-sm">Select a thread or create a new one to start chatting.</p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden">
      <ChatHistory threadId={threadId} />
      <div className="shrink-0 border-t">
        <CopilotChat
          className="max-h-[50vh]"
          labels={{
            title: "PerfPilot",
            initial: "",
            placeholder: "Ask PerfPilot anything about performance testing...",
          }}
        />
      </div>
    </div>
  );
}
