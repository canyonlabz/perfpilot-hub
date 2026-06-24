"use client";

import {
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  Ban,
  ExternalLink,
  ArrowLeft,
} from "lucide-react";
import Link from "next/link";
import type { RunDetail } from "@/lib/types";
import type { TaskSnapshot, TaskStatus } from "@/lib/types";

function formatAgentName(name: string): string {
  return name
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDuration(start: string | null, end: string | null): string | null {
  if (!start || !end) return null;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 0) return null;
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const remainSecs = secs % 60;
  return `${mins}m ${remainSecs}s`;
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

function StatusBadge({ status }: { status: TaskStatus }) {
  const { label, className, icon } = STATUS_CONFIG[status] ?? STATUS_CONFIG.pending;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${className}`}
    >
      {icon}
      {label}
    </span>
  );
}

function extractBlazeMeterUrl(task: TaskSnapshot): string | null {
  const result = task.result;
  if (!result) return null;

  const toolResult = result.tool_result as Record<string, unknown> | undefined;
  if (toolResult?.public_report_url) {
    return toolResult.public_report_url as string;
  }
  if (toolResult?.report_url) {
    return toolResult.report_url as string;
  }

  if (result.public_report_url) return result.public_report_url as string;
  if (result.report_url) return result.report_url as string;

  return null;
}

function extractRunId(task: TaskSnapshot): string | null {
  const result = task.result;
  if (!result) return null;

  const toolResult = result.tool_result as Record<string, unknown> | undefined;
  if (toolResult?.run_id) return String(toolResult.run_id);
  if (result.run_id) return String(result.run_id);

  return null;
}

function formatToolName(tool: string): string {
  return tool.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatResultSummary(result: Record<string, unknown>): string {
  if (result.stub === true) {
    return "Stub placeholder (no real work performed)";
  }

  const toolResult = result.tool_result as Record<string, unknown> | undefined;
  const tool = result.tool as string | undefined;

  if (toolResult && tool) {
    const ok = toolResult.ok as boolean;
    const toolLabel = formatToolName(tool);
    if (!ok) {
      const err = toolResult.error as Record<string, unknown> | undefined;
      return `${toolLabel} failed: ${(err?.message as string) ?? "Unknown error"}`;
    }
    const parts: string[] = [];
    if (toolResult.vendor) parts.push(String(toolResult.vendor));
    if (toolResult.status) parts.push(`status: ${toolResult.status}`);
    if (toolResult.elapsed_seconds) {
      const secs = Number(toolResult.elapsed_seconds);
      parts.push(secs >= 60 ? `${(secs / 60).toFixed(1)} min` : `${secs.toFixed(0)}s`);
    }
    return `${toolLabel} completed${parts.length > 0 ? ` (${parts.join(", ")})` : ""}`;
  }

  const summary = (result.summary as string) ?? (result.message as string);
  if (summary) return summary;

  const json = JSON.stringify(result);
  return json.length > 150 ? json.slice(0, 150) + "..." : json;
}

function TaskRow({ task }: { task: TaskSnapshot }) {
  const blazeMeterUrl = extractBlazeMeterUrl(task);
  const runId = extractRunId(task);
  const duration = formatDuration(task.submitted_at, task.completed_at);

  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium">
            {formatAgentName(task.agent_name)}
          </span>
          <StatusBadge status={task.status} />
        </div>
        {duration && (
          <span className="text-xs text-muted-foreground shrink-0">{duration}</span>
        )}
      </div>

      {/* Timestamps */}
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground">
        <span>Submitted: {formatTimestamp(task.submitted_at)}</span>
        {task.completed_at && (
          <span>Completed: {formatTimestamp(task.completed_at)}</span>
        )}
      </div>

      {/* BlazeMeter run_id */}
      {runId && (
        <div className="mt-1.5 text-xs text-muted-foreground">
          <span className="font-medium">BlazeMeter Run ID:</span> {runId}
        </div>
      )}

      {/* Result summary */}
      {task.result && (
        <div
          className={`mt-2 p-2 rounded text-xs ${
            (task.result.tool_result as Record<string, unknown>)?.ok === false
              ? "bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-300"
              : "bg-muted text-muted-foreground"
          }`}
        >
          {formatResultSummary(task.result)}
        </div>
      )}

      {/* Error */}
      {task.error && (
        <div className="mt-2 p-2 rounded text-xs bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-300">
          {(task.error.message as string)
            ?? (task.error.detail as string)
            ?? JSON.stringify(task.error).slice(0, 150)}
        </div>
      )}

      {/* BlazeMeter report link */}
      {blazeMeterUrl && (
        <a
          href={blazeMeterUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-flex items-center gap-1 text-xs text-primary hover:underline"
        >
          <ExternalLink className="h-3 w-3" />
          View BlazeMeter Report
        </a>
      )}

      {/* Task ID */}
      <div className="mt-2 text-[10px] text-muted-foreground font-mono">
        Task: {task.task_id.slice(0, 8)}...
      </div>
    </div>
  );
}

function deriveOverallStatus(tasks?: TaskSnapshot[]): TaskStatus {
  if (!tasks || tasks.length === 0) return "pending";
  if (tasks.some((t) => t.status === "failed")) return "failed";
  if (tasks.some((t) => t.status === "running")) return "running";
  if (tasks.some((t) => t.status === "pending")) return "pending";
  if (tasks.some((t) => t.status === "cancelled")) return "cancelled";
  return "completed";
}

interface RunDetailViewProps {
  run: RunDetail;
}

export function RunDetailView({ run }: RunDetailViewProps) {
  const overallStatus = deriveOverallStatus(run.tasks);

  const earliest = run.tasks.reduce<string | null>((min, t) => {
    if (!t.submitted_at) return min;
    if (!min) return t.submitted_at;
    return t.submitted_at < min ? t.submitted_at : min;
  }, null);

  const latest = run.tasks.reduce<string | null>((max, t) => {
    if (!t.completed_at) return max;
    if (!max) return t.completed_at;
    return t.completed_at > max ? t.completed_at : max;
  }, null);

  const totalDuration = formatDuration(earliest, latest);

  const anyBlazeMeterUrl = run.tasks
    .map(extractBlazeMeterUrl)
    .find((url) => url !== null) ?? null;

  const anyRunId = run.tasks
    .map(extractRunId)
    .find((id) => id !== null) ?? null;

  return (
    <div className="max-w-3xl mx-auto">
      {/* Back link */}
      <Link
        href="/runs"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-4"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back to Runs
      </Link>

      {/* Header card */}
      <div className="rounded-lg border bg-card shadow-sm p-5 mb-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-bold">{run.test_run_id}</h1>
            <p className="text-xs text-muted-foreground mt-0.5">PerfPilot Test Run ID</p>
          </div>
          <StatusBadge status={overallStatus} />
        </div>

        {/* Metadata grid */}
        <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <div>
            <span className="text-muted-foreground">Tasks:</span>{" "}
            <span className="font-medium">{run.task_count}</span>
          </div>
          {totalDuration && (
            <div>
              <span className="text-muted-foreground">Duration:</span>{" "}
              <span className="font-medium">{totalDuration}</span>
            </div>
          )}
          {earliest && (
            <div>
              <span className="text-muted-foreground">Started:</span>{" "}
              <span className="font-medium">{formatTimestamp(earliest)}</span>
            </div>
          )}
          {latest && (
            <div>
              <span className="text-muted-foreground">Completed:</span>{" "}
              <span className="font-medium">{formatTimestamp(latest)}</span>
            </div>
          )}
          {anyRunId && (
            <div>
              <span className="text-muted-foreground">BlazeMeter Run ID:</span>{" "}
              <span className="font-medium font-mono">{anyRunId}</span>
            </div>
          )}
        </div>

        {/* BlazeMeter report link (prominent) */}
        {anyBlazeMeterUrl && (
          <a
            href={anyBlazeMeterUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-4 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Open BlazeMeter Report
          </a>
        )}
      </div>

      {/* Tasks breakdown */}
      <h2 className="text-sm font-semibold mb-3">
        Task Breakdown ({run.task_count})
      </h2>
      <div className="space-y-3">
        {run.tasks.map((task) => (
          <TaskRow key={task.task_id} task={task} />
        ))}
      </div>

      {run.tasks.length === 0 && (
        <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
          No tasks found for this run.
        </div>
      )}
    </div>
  );
}
