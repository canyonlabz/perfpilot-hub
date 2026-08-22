"use client";

import Link from "next/link";
import {
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  Ban,
  ChevronRight,
  ExternalLink,
} from "lucide-react";
import type { RunSummary, TaskStatus } from "@/lib/types";

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

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
}

function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)} ms`;
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

function KpiTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col items-center px-3 py-1.5 rounded-md bg-muted/50">
      <span className="text-sm font-semibold tabular-nums">{value}</span>
      <span className="text-[10px] text-muted-foreground leading-tight">{label}</span>
    </div>
  );
}

interface RunCardProps {
  run: RunSummary;
}

export function RunCard({ run }: RunCardProps) {
  const overallStatus = deriveOverallStatus(run.statuses);
  const { label, className, icon } = STATUS_CONFIG[overallStatus] ?? STATUS_CONFIG.pending;
  const hasKpis = run.avg_response_time_ms != null || run.max_virtual_users != null;

  return (
    <Link
      href={`/runs/${encodeURIComponent(run.test_run_id)}`}
      className="block rounded-lg border bg-card text-card-foreground shadow-sm hover:shadow-md hover:border-primary/30 transition-all"
    >
      <div className="px-4 py-3">
        {/* Top row: test_run_id + test_name + status badge */}
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold truncate">
              {run.test_run_id}
              {run.test_name && (
                <span className="font-normal text-muted-foreground">
                  {" \u2014 "}{run.test_name}
                </span>
              )}
            </h3>
          </div>
          <span
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium shrink-0 ${className}`}
          >
            {icon}
            {label}
          </span>
        </div>

        {/* KPI tiles row (only when data is available) */}
        {hasKpis && (
          <div className="mt-2.5 flex flex-wrap gap-2">
            {run.max_virtual_users != null && (
              <KpiTile label="Max VU" value={String(run.max_virtual_users)} />
            )}
            {run.avg_throughput != null && (
              <KpiTile label="Throughput" value={`${run.avg_throughput} Hit/s`} />
            )}
            {run.error_rate != null && (
              <KpiTile label="Errors" value={`${run.error_rate}%`} />
            )}
            {run.avg_response_time_ms != null && (
              <KpiTile label="Avg RT" value={formatMs(run.avg_response_time_ms)} />
            )}
            {run.p90_response_time_ms != null && (
              <KpiTile label="P90 RT" value={formatMs(run.p90_response_time_ms)} />
            )}
          </div>
        )}

        {/* Bottom row: metadata */}
        <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
          {run.duration_seconds != null && (
            <>
              <span>Test: {formatDuration(run.duration_seconds)}</span>
              <span className="text-border">|</span>
            </>
          )}
          <span>
            {run.task_count} {run.task_count === 1 ? "task" : "tasks"}
          </span>
          {run.samples_total != null && (
            <>
              <span className="text-border">|</span>
              <span>{run.samples_total} samples</span>
            </>
          )}
          {run.agents.length > 0 && !hasKpis && (
            <>
              <span className="text-border">|</span>
              <span className="truncate">
                {run.agents.map(formatAgentName).join(", ")}
              </span>
            </>
          )}
          {run.avg_bandwidth_bytes != null && (
            <>
              <span className="text-border">|</span>
              <span>{run.avg_bandwidth_bytes.toFixed(1)} KiB/s</span>
            </>
          )}
        </div>

        {/* Timestamp + report link + arrow */}
        <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
          <div className="flex items-center gap-3">
            <span>
              {run.earliest_submitted
                ? formatRelativeTime(run.earliest_submitted)
                : "No timestamp"}
            </span>
            {run.public_url && (
              <span
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  window.open(run.public_url!, "_blank", "noopener,noreferrer");
                }}
                className="inline-flex items-center gap-0.5 text-primary hover:underline cursor-pointer"
              >
                <ExternalLink className="h-3 w-3" />
                Report
              </span>
            )}
          </div>
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/50" />
        </div>
      </div>
    </Link>
  );
}
