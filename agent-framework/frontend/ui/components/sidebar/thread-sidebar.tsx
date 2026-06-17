"use client";

import { useEffect, useState, useCallback } from "react";
import { Plus, PanelLeftClose, PanelLeft } from "lucide-react";
import { ThreadItem } from "./thread-item";
import { listThreads, createThread, renameThread, archiveThread, deleteThread } from "@/lib/api";
import type { Thread } from "@/lib/types";

interface ThreadSidebarProps {
  activeThreadId: string | null;
  onSelectThread: (threadId: string) => void;
  onNewThread: (thread: Thread) => void;
}

export function ThreadSidebar({
  activeThreadId,
  onSelectThread,
  onNewThread,
}: ThreadSidebarProps) {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const [loading, setLoading] = useState(true);

  const refreshThreads = useCallback(async () => {
    try {
      const data = await listThreads();
      setThreads(data.threads);
    } catch (err) {
      console.error("Failed to load threads:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshThreads();
  }, [refreshThreads]);

  async function handleNewThread() {
    try {
      const thread = await createThread();
      setThreads((prev) => [thread, ...prev]);
      onNewThread(thread);
    } catch (err) {
      console.error("Failed to create thread:", err);
    }
  }

  async function handleRename(threadId: string, newTitle: string) {
    try {
      const updated = await renameThread(threadId, newTitle);
      setThreads((prev) =>
        prev.map((t) => (t.thread_id === threadId ? updated : t))
      );
    } catch (err) {
      console.error("Failed to rename thread:", err);
    }
  }

  async function handleArchive(threadId: string) {
    try {
      await archiveThread(threadId);
      setThreads((prev) => prev.filter((t) => t.thread_id !== threadId));
      if (activeThreadId === threadId) {
        const remaining = threads.filter((t) => t.thread_id !== threadId);
        if (remaining.length > 0) {
          onSelectThread(remaining[0].thread_id);
        }
      }
    } catch (err) {
      console.error("Failed to archive thread:", err);
    }
  }

  async function handleDelete(threadId: string) {
    try {
      await deleteThread(threadId);
      setThreads((prev) => prev.filter((t) => t.thread_id !== threadId));
      if (activeThreadId === threadId) {
        const remaining = threads.filter((t) => t.thread_id !== threadId);
        if (remaining.length > 0) {
          onSelectThread(remaining[0].thread_id);
        }
      }
    } catch (err) {
      console.error("Failed to delete thread:", err);
    }
  }

  if (collapsed) {
    return (
      <div className="flex flex-col items-center py-3 px-1 border-r bg-card w-12">
        <button
          onClick={() => setCollapsed(false)}
          className="p-2 rounded-md hover:bg-muted"
          title="Expand sidebar"
        >
          <PanelLeft className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col w-64 border-r bg-card h-full">
      <div className="flex items-center justify-between px-3 py-3 border-b">
        <span className="text-sm font-semibold">Threads</span>
        <div className="flex items-center gap-1">
          <button
            onClick={handleNewThread}
            className="p-1.5 rounded-md hover:bg-muted"
            title="New thread"
          >
            <Plus className="h-4 w-4" />
          </button>
          <button
            onClick={() => setCollapsed(true)}
            className="p-1.5 rounded-md hover:bg-muted"
            title="Collapse sidebar"
          >
            <PanelLeftClose className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
        {loading ? (
          <div className="px-3 py-2 text-sm text-muted-foreground animate-pulse">
            Loading...
          </div>
        ) : threads.length === 0 ? (
          <div className="px-3 py-4 text-sm text-muted-foreground text-center">
            No threads yet.
            <br />
            Click + to start a conversation.
          </div>
        ) : (
          threads.map((thread) => (
            <ThreadItem
              key={thread.thread_id}
              thread={thread}
              isActive={thread.thread_id === activeThreadId}
              onSelect={onSelectThread}
              onRename={handleRename}
              onArchive={handleArchive}
              onDelete={handleDelete}
            />
          ))
        )}
      </div>
    </div>
  );
}
