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
import { ArrowUp, Check, Mic, Plus, Square } from "lucide-react";

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
    <div className="pp-msg-row" data-role={isUser ? "user" : "assistant"}>
      <div className="pp-msg" data-role={isUser ? "user" : "assistant"}>
        {isUser ? (
          <span className="whitespace-pre-wrap">{content}</span>
        ) : (
          <div className="prose prose-sm max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
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
      <div className="pp-chip-row">
        <span className="pp-chip">
          <Check className="h-3 w-3" />
          Task delegated to {targetAgent ?? "specialist"}
        </span>
      </div>
    );
  }

  return (
    <div className="pp-chip-row">
      <span className="pp-chip">
        Using tool: <span className="font-mono">{name}</span>
      </span>
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
      <div className="pp-chat flex-1 flex items-center justify-center text-[var(--text-muted)]">
        <p className="text-sm">
          Select a thread or create a new one to start chatting.
        </p>
      </div>
    );
  }

  return (
    <div className="pp-chat flex-1 flex flex-col h-full overflow-hidden">
      <div ref={scrollRef} className="pp-messages flex-1 overflow-y-auto">
        {!historyLoaded && (
          <div className="flex items-center justify-center py-8">
            <p className="text-sm text-[var(--text-muted)] animate-pulse">
              Loading conversation...
            </p>
          </div>
        )}

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
            <div className="pp-divider">
              End of history — continue the conversation below
            </div>
          )}

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
            <div className="pp-msg-row" data-role="assistant">
              <div className="pp-msg" data-role="assistant">
                <p className="animate-pulse text-[var(--text-muted)]">
                  PerfPilot is thinking...
                </p>
              </div>
            </div>
          )}
      </div>

      <div className="pp-composer-wrap shrink-0">
        <form onSubmit={handleSend} className="pp-composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask PerfPilot anything about performance testing…"
            className="pp-composer-input"
            rows={2}
            disabled={isLoading}
            aria-label="Message PerfPilot"
          />
          <div className="pp-composer-actions">
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="pp-icon-btn"
                title="Attach file"
                aria-label="Attach file"
              >
                <Plus className="h-[18px] w-[18px]" />
              </button>
              <span className="pp-composer-hint">
                Enter to send · Shift+Enter for new line
              </span>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="pp-icon-btn"
                title="Dictate"
                aria-label="Dictate message"
              >
                <Mic className="h-4 w-4" />
              </button>
              <button
                type={isLoading ? "button" : "submit"}
                onClick={isLoading ? stopGeneration : undefined}
                disabled={!isLoading && !input.trim()}
                className="pp-send"
                aria-label={isLoading ? "Stop generating" : "Send message"}
              >
                {isLoading ? (
                  <Square className="h-3.5 w-3.5" />
                ) : (
                  <ArrowUp className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
