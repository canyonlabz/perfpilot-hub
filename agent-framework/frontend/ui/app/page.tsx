"use client";

import { useState, useCallback, useEffect } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { Header } from "@/components/layout/header";
import { ThreadSidebar } from "@/components/sidebar/thread-sidebar";
import { ChatPanel } from "@/components/chat/chat-panel";
import { CatalogPanel } from "@/components/catalog/catalog-panel";
import { TaskProgressPanel } from "@/components/tasks/task-progress-panel";
import { listThreads, createThread } from "@/lib/api";
import type { Thread } from "@/lib/types";

const STORAGE_KEY = "perfpilot_active_thread_id";

export default function HomePage() {
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [initialized, setInitialized] = useState(false);
  const [showCatalog, setShowCatalog] = useState(false);
  const [showTasks, setShowTasks] = useState(false);

  useEffect(() => {
    async function init() {
      try {
        const data = await listThreads();
        const storedId = localStorage.getItem(STORAGE_KEY);

        if (storedId && data.threads.some((t) => t.thread_id === storedId)) {
          setActiveThreadId(storedId);
        } else if (data.threads.length > 0) {
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

  useEffect(() => {
    if (activeThreadId) {
      localStorage.setItem(STORAGE_KEY, activeThreadId);
    }
  }, [activeThreadId]);

  const handleSelectThread = useCallback((threadId: string) => {
    setActiveThreadId(threadId);
  }, []);

  const handleNewThread = useCallback((thread: Thread) => {
    setActiveThreadId(thread.thread_id);
  }, []);

  const handleToggleCatalog = useCallback(() => {
    setShowCatalog((prev) => {
      if (!prev) setShowTasks(false);
      return !prev;
    });
  }, []);

  const handleToggleTasks = useCallback(() => {
    setShowTasks((prev) => {
      if (!prev) setShowCatalog(false);
      return !prev;
    });
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
    <div className="flex flex-col h-screen">
      <Header
        showCatalog={showCatalog}
        onToggleCatalog={handleToggleCatalog}
        showTasks={showTasks}
        onToggleTasks={handleToggleTasks}
      />
      <div className="flex flex-1 overflow-hidden">
        <ThreadSidebar
          activeThreadId={activeThreadId}
          onSelectThread={handleSelectThread}
          onNewThread={handleNewThread}
        />
        {activeThreadId ? (
          <CopilotKit
            key={activeThreadId}
            runtimeUrl="/api/copilotkit"
            agent="perfpilot-orchestrator"
            threadId={activeThreadId}
            showDevConsole={false}
          >
            <ChatPanel threadId={activeThreadId} />
          </CopilotKit>
        ) : (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            <p className="text-sm">Select a thread to start chatting.</p>
          </div>
        )}
        {showCatalog && (
          <aside className="w-80 border-l bg-card flex-shrink-0">
            <CatalogPanel />
          </aside>
        )}
        {showTasks && (
          <aside className="w-80 border-l bg-card flex-shrink-0">
            <TaskProgressPanel />
          </aside>
        )}
      </div>
    </div>
  );
}
