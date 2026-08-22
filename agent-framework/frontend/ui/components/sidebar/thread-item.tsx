"use client";

import { useState, useRef, useEffect } from "react";
import { MessageSquare, Pencil, Archive, Trash2, Check, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Thread } from "@/lib/types";

interface ThreadItemProps {
  thread: Thread;
  isActive: boolean;
  onSelect: (threadId: string) => void;
  onRename: (threadId: string, newTitle: string) => void;
  onArchive: (threadId: string) => void;
  onDelete: (threadId: string) => void;
}

export function ThreadItem({
  thread,
  isActive,
  onSelect,
  onRename,
  onArchive,
  onDelete,
}: ThreadItemProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  const displayTitle = thread.title || "Untitled Thread";

  function startEditing() {
    setEditTitle(displayTitle);
    setIsEditing(true);
  }

  function confirmRename() {
    const trimmed = editTitle.trim();
    if (trimmed && trimmed !== displayTitle) {
      onRename(thread.thread_id, trimmed);
    }
    setIsEditing(false);
  }

  function cancelEditing() {
    setIsEditing(false);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") confirmRename();
    if (e.key === "Escape") cancelEditing();
  }

  return (
    <div
      className={cn(
        "group flex items-center gap-2 px-3 py-2 rounded-md cursor-pointer transition-colors",
        isActive
          ? "bg-accent text-accent-foreground"
          : "hover:bg-muted text-foreground"
      )}
      onClick={() => !isEditing && onSelect(thread.thread_id)}
    >
      <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground" />

      {isEditing ? (
        <div className="flex-1 flex items-center gap-1">
          <input
            ref={inputRef}
            className="flex-1 bg-background border border-input rounded px-1.5 py-0.5 text-sm"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={confirmRename}
          />
          <button
            onClick={(e) => { e.stopPropagation(); confirmRename(); }}
            className="p-0.5 hover:text-primary"
          >
            <Check className="h-3 w-3" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); cancelEditing(); }}
            className="p-0.5 hover:text-destructive"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      ) : (
        <>
          <span className="flex-1 text-sm truncate">{displayTitle}</span>
          <div className="hidden group-hover:flex items-center gap-0.5">
            <button
              onClick={(e) => { e.stopPropagation(); startEditing(); }}
              className="p-1 rounded hover:bg-background"
              title="Rename"
            >
              <Pencil className="h-3 w-3" />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onArchive(thread.thread_id); }}
              className="p-1 rounded hover:bg-background"
              title="Archive"
            >
              <Archive className="h-3 w-3" />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(thread.thread_id); }}
              className="p-1 rounded hover:bg-background text-destructive"
              title="Delete"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </div>
        </>
      )}
    </div>
  );
}
