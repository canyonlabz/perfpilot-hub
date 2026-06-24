"use client";

import Link from "next/link";
import {
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  Ban,
  ChevronRight,
} from "lucide-react";
import type { RunSummary } from "@/lib/types";
import type { TaskStatus } from "@/lib/types";

function formatAgentName(name: string): string {
  return name
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function deriveOverallStatus(statuses?: Record<string, number>): TaskStatus {
  if (!statuses) return "pending";
  if (statuses.failed) return "failed";
  if (statuses.running) return "running";
  if (statuses.pending) return "pending";
  if (statuses.cancelled) return "cancelled";
  return "completed";
}

const STATUS_CONFIG: Record<TaskStatus, { label: string; className: string; icon: React.ReactNode }> = {
  pending: {
    label: "Pending",
    className: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
    icon: <Clock className="h-3 w-3" />,
  },
  running: {
    label: "Running",
    className: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
    icon: <Loader2 className="h-3 w-3 animate-spin" />,
  },
  completed: {
    label: "Completed",
    className: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
    icon: <CheckCircle2 className="h-3 w-3" />,
  },
  failed: {
    label: "Failed",
    className: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
    icon: <XCircle className="h-3 w-3" />,
  },
  cancelled: {
    label: "Cancelled",
    className: "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300",
    icon: <Ban className="h-3 w-3" />,
  },
};

interface RunCardProps {
  run: RunSummary;
}

export function RunCard({ run }: RunCardProps) {
  const overallStatus = deriveOverallStatus(run.statuses);
  const { label, className, icon } = STATUS_CONFIG[overallStatus] ?? STATUS_CONFIG.pending;

  return (
    <Link
      href={`/runs/${encodeURIComponent(run.test_run_id)}`}
      className="block rounded-lg border bg-card text-card-foreground shadow-sm hover:shadow-md hover:border-primary/30 transition-all"
    >
      <div className="px-4 py-3">
        {/* Top row: test_run_id + status badge */}
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold truncate">{run.test_run_id}</h3>
          <span
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium shrink-0 ${className}`}
          >
            {icon}
            {label}
          </span>
        </div>

        {/* Middle row: task count + agents */}
        <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
          <span>
            {run.task_count} {run.task_count === 1 ? "task" : "tasks"}
          </span>
          {run.agents.length > 0 && (
            <>
              <span className="text-border">|</span>
              <span className="truncate">
                {run.agents.map(formatAgentName).join(", ")}
              </span>
            </>
          )}
        </div>

        {/* Status breakdown for multi-task runs */}
        {run.task_count > 1 && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {Object.entries(run.statuses).map(([status, count]) => {
              const cfg = STATUS_CONFIG[status as TaskStatus];
              if (!cfg) return null;
              return (
                <span
                  key={status}
                  className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium ${cfg.className}`}
                >
                  {cfg.icon}
                  {count}
                </span>
              );
            })}
          </div>
        )}

        {/* Bottom row: timestamps + arrow */}
        <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
          <span>
            {run.earliest_submitted
              ? formatRelativeTime(run.earliest_submitted)
              : "No timestamp"}
          </span>
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/50" />
        </div>
      </div>
    </Link>
  );
}
