"use client";

import { CheckCircle2, Circle, Loader2, XCircle, Ban } from "lucide-react";
import type { TaskStatus } from "@/lib/types";

interface TaskStepProps {
  status: TaskStatus;
  progress: string | null;
  timestamp: string;
  isLatest: boolean;
}

function StatusIcon({ status, isLatest }: { status: TaskStatus; isLatest: boolean }) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="h-3.5 w-3.5 text-green-500 shrink-0" />;
    case "failed":
      return <XCircle className="h-3.5 w-3.5 text-red-500 shrink-0" />;
    case "cancelled":
      return <Ban className="h-3.5 w-3.5 text-amber-500 shrink-0" />;
    case "running":
      return isLatest ? (
        <Loader2 className="h-3.5 w-3.5 text-blue-500 shrink-0 animate-spin" />
      ) : (
        <CheckCircle2 className="h-3.5 w-3.5 text-green-500 shrink-0" />
      );
    case "pending":
    default:
      return <Circle className="h-3.5 w-3.5 text-muted-foreground shrink-0" />;
  }
}

function formatTime(iso: string): string {
  try {
    const date = new Date(iso);
    return date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "";
  }
}

export function TaskStep({ status, progress, timestamp, isLatest }: TaskStepProps) {
  return (
    <div
      className={`flex items-start gap-2 py-1.5 px-2 rounded text-xs ${
        isLatest ? "bg-muted/50" : ""
      }`}
    >
      <div className="mt-0.5">
        <StatusIcon status={status} isLatest={isLatest} />
      </div>
      <div className="flex-1 min-w-0">
        <span className="text-foreground">
          {progress || status}
        </span>
      </div>
      {timestamp && (
        <span className="text-muted-foreground shrink-0 tabular-nums">
          {formatTime(timestamp)}
        </span>
      )}
    </div>
  );
}
