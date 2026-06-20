"use client";

import { useState, useEffect, useCallback } from "react";
import { RefreshCw, ListChecks, AlertCircle } from "lucide-react";
import { fetchRuns, fetchRunDetail } from "@/lib/api";
import { TaskProgressCard } from "./task-progress-card";
import type { TaskSnapshot } from "@/lib/types";
import { TERMINAL_STATUSES } from "@/lib/types";

const POLL_INTERVAL_MS = 15_000;

export function TaskProgressPanel() {
  const [tasks, setTasks] = useState<TaskSnapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const loadTasks = useCallback(async (showSpinner = false) => {
    if (showSpinner) setRefreshing(true);
    try {
      const runsData = await fetchRuns(10);
      const allTasks: TaskSnapshot[] = [];

      for (const run of runsData.runs) {
        try {
          const detail = await fetchRunDetail(run.test_run_id);
          allTasks.push(...detail.tasks);
        } catch {
          // Individual run detail fetch can fail if owner-filtered; skip
        }
      }

      // Sort: non-terminal first (active tasks at top), then by submitted_at desc
      allTasks.sort((a, b) => {
        const aTerminal = TERMINAL_STATUSES.has(a.status) ? 1 : 0;
        const bTerminal = TERMINAL_STATUSES.has(b.status) ? 1 : 0;
        if (aTerminal !== bTerminal) return aTerminal - bTerminal;

        const aTime = a.submitted_at ?? "";
        const bTime = b.submitted_at ?? "";
        return bTime.localeCompare(aTime);
      });

      setTasks(allTasks);
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load tasks";
      setError(message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadTasks();

    const interval = setInterval(() => {
      loadTasks();
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [loadTasks]);

  const handleRefresh = () => loadTasks(true);

  const activeTasks = tasks.filter((t) => !TERMINAL_STATUSES.has(t.status));
  const completedTasks = tasks.filter((t) => TERMINAL_STATUSES.has(t.status));

  return (
    <div className="flex flex-col h-full">
      {/* Panel header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b">
        <div className="flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold">Task Progress</h2>
          {activeTasks.length > 0 && (
            <span className="inline-flex items-center justify-center h-4 min-w-[16px] px-1 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300 text-[10px] font-bold">
              {activeTasks.length}
            </span>
          )}
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
          title="Refresh tasks"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
        </button>
      </div>

      {/* Panel body */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2">
        {loading && (
          <div className="flex items-center justify-center py-8">
            <p className="text-xs text-muted-foreground animate-pulse">
              Loading tasks...
            </p>
          </div>
        )}

        {error && !loading && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-300 text-xs">
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />
            <span>{error}</span>
            <button
              onClick={handleRefresh}
              className="ml-auto underline hover:no-underline text-xs"
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !error && tasks.length === 0 && (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <ListChecks className="h-8 w-8 text-muted-foreground/50 mb-2" />
            <p className="text-xs text-muted-foreground">
              No tasks yet. Tasks will appear here when an agent starts working.
            </p>
          </div>
        )}

        {/* Active tasks */}
        {activeTasks.length > 0 && (
          <div className="space-y-2">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold px-1">
              Active
            </p>
            {activeTasks.map((task) => (
              <TaskProgressCard
                key={task.task_id}
                taskId={task.task_id}
                initialSnapshot={task}
              />
            ))}
          </div>
        )}

        {/* Completed tasks */}
        {completedTasks.length > 0 && (
          <div className="space-y-2">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold px-1">
              Recent
            </p>
            {completedTasks.map((task) => (
              <TaskProgressCard
                key={task.task_id}
                taskId={task.task_id}
                initialSnapshot={task}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
