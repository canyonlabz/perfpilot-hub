"use client";

import { useState, useCallback, useEffect } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { Header } from "@/components/layout/header";
import { ThreadSidebar } from "@/components/sidebar/thread-sidebar";
import { ChatPanel } from "@/components/chat/chat-panel";
import { listThreads, createThread } from "@/lib/api";
import type { Thread } from "@/lib/types";

export default function HomePage() {
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    async function init() {
      try {
        const data = await listThreads();
        if (data.threads.length > 0) {
          setActiveThreadId(data.threads[0].thread_id);
        } else {
          const newThread = await createThread("New Conversation");
          setActiveThreadId(newThread.thread_id);
        }
      } catch (err) {
        console.error("Failed to initialize threads:", err);
      } finally {
        setInitialized(true);
      }
    }
    init();
  }, []);

  const handleSelectThread = useCallback((threadId: string) => {
    setActiveThreadId(threadId);
  }, []);

  const handleNewThread = useCallback((thread: Thread) => {
    setActiveThreadId(thread.thread_id);
  }, []);

  if (!initialized) {
    return (
      <div className="flex flex-col h-screen">
        <Header />
        <div className="flex-1 flex items-center justify-center">
          <p className="text-sm text-muted-foreground animate-pulse">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <CopilotKit
      runtimeUrl="/api/copilotkit"
      agent="perfpilot-orchestrator"
      threadId={activeThreadId || undefined}
    >
      <div className="flex flex-col h-screen">
        <Header />
        <div className="flex flex-1 overflow-hidden">
          <ThreadSidebar
            activeThreadId={activeThreadId}
            onSelectThread={handleSelectThread}
            onNewThread={handleNewThread}
          />
          <ChatPanel threadId={activeThreadId} />
        </div>
      </div>
    </CopilotKit>
  );
}
