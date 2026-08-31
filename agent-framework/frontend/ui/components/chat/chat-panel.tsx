"use client";

import { useState, useEffect, useRef, FormEvent } from "react";
import { useCopilotChat, useCopilotReadable } from "@copilotkit/react-core";
import { TextMessage, Role } from "@copilotkit/runtime-client-gql";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { fetchMessages } from "@/lib/api";
import type { Message as DbMessage } from "@/lib/types";
import {
  getGitHubCredsMetadata,
  subscribeToCredsChanges,
  type GitHubCredsMetadata,
} from "@/lib/github-creds";

interface ChatPanelProps {
  threadId: string | null;
}

function extractText(content: string | Record<string, unknown>): string {
  if (typeof content === "string") return content;
  if (content && typeof content.text === "string") return content.text;
  if (content && typeof content.content === "string") return content.content;
  return JSON.stringify(content);
}

function extractLiveContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content))
    return content
      .map((c: Record<string, unknown>) =>
        typeof c.text === "string" ? c.text : ""
      )
      .join("");
  return "";
}

interface HistoryMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

function MessageBubble({
  role,
  content,
}: {
  role: "user" | "assistant";
  content: string;
}) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-3`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-2 text-sm ${
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-foreground"
        }`}
      >
        {isUser ? (
          <span className="whitespace-pre-wrap">{content}</span>
        ) : (
          <div className="prose prose-sm dark:prose-invert max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {content}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}

function ToolCallBubble({ name, args }: { name: string; args?: Record<string, unknown> }) {
  const isDelegation = name === "delegate_to_specialist";
  const targetAgent = isDelegation && args?.agent_name
    ? String(args.agent_name).replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
    : null;

  if (isDelegation) {
    return (
      <div className="flex justify-start mb-3">
        <div className="max-w-[80%] rounded-lg px-3 py-2 text-xs bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800">
          <span className="font-medium">Task delegated</span>
          {targetAgent && (
            <span> to {targetAgent}</span>
          )}
          <span className="text-blue-500 dark:text-blue-400 ml-1">
            — check Tasks panel for progress
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start mb-3">
      <div className="max-w-[80%] rounded-lg px-3 py-1.5 text-xs bg-muted/50 text-muted-foreground border border-dashed">
        Using tool: <span className="font-mono">{name}</span>
      </div>
    </div>
  );
}

export function ChatPanel({ threadId }: ChatPanelProps) {
  const {
    visibleMessages = [],
    appendMessage,
    isLoading,
    stopGeneration,
  } = useCopilotChat();

  const [historyMessages, setHistoryMessages] = useState<HistoryMessage[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [input, setInput] = useState("");
  const [githubCreds, setGitHubCreds] = useState<GitHubCredsMetadata | null>(
    null
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const prevLoadingRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      const meta = await getGitHubCredsMetadata();
      if (!cancelled) setGitHubCreds(meta);
    };
    refresh();
    const unsubscribe = subscribeToCredsChanges(refresh);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  useCopilotReadable({
    description:
      "Session-attached GitHub SCM target for pushing generated JMeter scripts. " +
      "When present, the orchestrator should treat this as an scm block and " +
      "delegate the new-JMX pipeline (script-agent generate -> push -> smoke, " +
      "then execution-agent provision_performance_test). The personal access " +
      "token is NOT exposed here; it stays in the browser session and is only " +
      "forwarded to the local Script Agent when a push is actually executed.",
    value: githubCreds
      ? {
          url: githubCreds.url,
          branch: githubCreds.branch || null,
          path: githubCreds.path || null,
          owner: githubCreds.owner,
          repo: githubCreds.repo,
          provider: "github",
          token_available: githubCreds.has_token,
          storage: "browser-session",
          attached_at: githubCreds.updated_at,
        }
      : null,
    available: githubCreds ? "enabled" : "disabled",
  });

  useEffect(() => {
    setHistoryLoaded(false);
    setHistoryMessages([]);
    if (!threadId) return;

    async function load() {
      try {
        const data = await fetchMessages(threadId!);
        const history: HistoryMessage[] = (data.messages ?? [])
          .filter(
            (m: DbMessage) => m.role === "user" || m.role === "assistant"
          )
          .map((m: DbMessage) => ({
            id: String(m.id),
            role: m.role as "user" | "assistant",
            content: extractText(m.content),
          }))
          .filter((m: HistoryMessage) => m.content.length > 0);
        setHistoryMessages(history);
      } catch (err) {
        console.error("Failed to load thread history:", err);
      } finally {
        setHistoryLoaded(true);
      }
    }
    load();
  }, [threadId]);

  // When streaming finishes, reload DB history to capture the new exchange
  useEffect(() => {
    if (prevLoadingRef.current && !isLoading && threadId) {
      fetchMessages(threadId).then((data) => {
        const history: HistoryMessage[] = (data.messages ?? [])
          .filter(
            (m: DbMessage) => m.role === "user" || m.role === "assistant"
          )
          .map((m: DbMessage) => ({
            id: String(m.id),
            role: m.role as "user" | "assistant",
            content: extractText(m.content),
          }))
          .filter((m: HistoryMessage) => m.content.length > 0);
        setHistoryMessages(history);
      });
    }
    prevLoadingRef.current = isLoading;
  }, [isLoading, threadId]);

  // Always show DB history. During streaming, show all visibleMessages below.
  // After streaming completes (and DB reloads), clear live to avoid duplicates.
  const liveMessages = isLoading ? visibleMessages : [];

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [historyMessages.length, visibleMessages.length, isLoading]);

  async function handleSend(e?: FormEvent) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || isLoading) return;
    setInput("");
    await appendMessage(new TextMessage({ content: text, role: Role.User }));
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  if (!threadId) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground">
        <p className="text-sm">
          Select a thread or create a new one to start chatting.
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden">
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-4 py-4"
      >
        {!historyLoaded && (
          <div className="flex items-center justify-center py-8">
            <p className="text-sm text-muted-foreground animate-pulse">
              Loading conversation...
            </p>
          </div>
        )}

        {/* DB history — ALWAYS shown */}
        {historyMessages.map((msg) => (
          <MessageBubble
            key={`db-${msg.id}`}
            role={msg.role}
            content={msg.content}
          />
        ))}

        {historyLoaded &&
          historyMessages.length > 0 &&
          liveMessages.length === 0 &&
          !isLoading && (
            <div className="text-center text-xs text-muted-foreground py-2">
              End of history — continue the conversation below
            </div>
          )}

        {/* Live CopilotKit messages — shown when streaming or new messages */}
        {liveMessages.map((msg) => {
          if (msg.isTextMessage()) {
            return (
              <MessageBubble
                key={msg.id}
                role={msg.role === Role.User ? "user" : "assistant"}
                content={extractLiveContent(msg.content)}
              />
            );
          }
          if (msg.isActionExecutionMessage()) {
            return (
              <ToolCallBubble
                key={msg.id}
                name={msg.name}
                args={msg.arguments as Record<string, unknown> | undefined}
              />
            );
          }
          return null;
        })}

        {isLoading &&
          liveMessages.filter(
            (m) => m.isTextMessage() && m.role !== Role.User
          ).length === 0 && (
            <div className="flex justify-start mb-3">
              <div className="bg-muted rounded-lg px-4 py-2 text-sm text-muted-foreground animate-pulse">
                PerfPilot is thinking...
              </div>
            </div>
          )}
      </div>

      {/* Input area */}
      <div className="shrink-0 border-t bg-background p-4">
        <form onSubmit={handleSend} className="flex gap-2 items-end">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask PerfPilot anything about performance testing..."
            className="flex-1 resize-none rounded-lg border bg-background px-3 py-2 text-sm
                       placeholder:text-muted-foreground focus-visible:outline-none
                       focus-visible:ring-1 focus-visible:ring-ring min-h-[40px] max-h-[120px]"
            rows={1}
            disabled={isLoading}
          />
          <button
            type={isLoading ? "button" : "submit"}
            onClick={isLoading ? stopGeneration : undefined}
            disabled={!isLoading && !input.trim()}
            className="shrink-0 rounded-lg bg-primary px-4 py-2 text-sm font-medium
                       text-primary-foreground hover:bg-primary/90
                       disabled:opacity-50 disabled:cursor-not-allowed
                       transition-colors"
          >
            {isLoading ? "Stop" : "Send"}
          </button>
        </form>
      </div>
    </div>
  );
}
