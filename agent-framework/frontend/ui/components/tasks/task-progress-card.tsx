"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import {
  CheckCircle2,
  XCircle,
  Ban,
  Loader2,
  Clock,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { subscribeToTask } from "@/lib/sse";
import { TaskStep } from "./task-step";
import type { TaskSnapshot, TaskEvent, TaskStatus } from "@/lib/types";
import { TERMINAL_STATUSES } from "@/lib/types";

interface TaskProgressCardProps {
  taskId: string;
  initialSnapshot?: TaskSnapshot;
}

interface StepEntry {
  id: string;
  status: TaskStatus;
  progress: string | null;
  timestamp: string;
}

function StatusBadge({ status }: { status: TaskStatus }) {
  const config: Record<TaskStatus, { label: string; className: string; icon: React.ReactNode }> = {
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

  const { label, className, icon } = config[status] ?? config.pending;

  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${className}`}
    >
      {icon}
      {label}
    </span>
  );
}

function formatAgentName(name: string): string {
  return name
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function TaskProgressCard({ taskId, initialSnapshot }: TaskProgressCardProps) {
  const [agentName, setAgentName] = useState(initialSnapshot?.agent_name ?? "");
  const [currentStatus, setCurrentStatus] = useState<TaskStatus>(
    initialSnapshot?.status ?? "pending"
  );
  const [steps, setSteps] = useState<StepEntry[]>([]);
  const [result, setResult] = useState<Record<string, unknown> | null>(
    initialSnapshot?.result ?? null
  );
  const [error, setError] = useState<Record<string, unknown> | null>(
    initialSnapshot?.error ?? null
  );
  const [connectionError, setConnectionError] = useState(false);
  const [expanded, setExpanded] = useState(true);
  const stepsEndRef = useRef<HTMLDivElement>(null);
  const stepCounterRef = useRef(0);

  const addStep = useCallback((status: TaskStatus, progress: string | null, timestamp: string) => {
    stepCounterRef.current += 1;
    setSteps((prev) => [
      ...prev,
      {
        id: `step-${stepCounterRef.current}`,
        status,
        progress,
        timestamp,
      },
    ]);
  }, []);

  useEffect(() => {
    if (TERMINAL_STATUSES.has(currentStatus) && !initialSnapshot) return;

    const isAlreadyTerminal = initialSnapshot && TERMINAL_STATUSES.has(initialSnapshot.status);

    if (initialSnapshot && isAlreadyTerminal) {
      addStep(
        initialSnapshot.status,
        initialSnapshot.status === "completed" ? "Task completed" : `Task ${initialSnapshot.status}`,
        initialSnapshot.completed_at ?? initialSnapshot.started_at ?? ""
      );
      return;
    }

    const cleanup = subscribeToTask(taskId, {
      onSnapshot: (snapshot: TaskSnapshot) => {
        setAgentName(snapshot.agent_name);
        setCurrentStatus(snapshot.status);
        setResult(snapshot.result);
        setError(snapshot.error);
        setConnectionError(false);

        if (snapshot.status !== "pending") {
          addStep(
            snapshot.status,
            snapshot.status === "running" ? "Task started" : `Task ${snapshot.status}`,
            snapshot.started_at ?? snapshot.submitted_at ?? ""
          );
        }
      },
      onStateChange: (event: TaskEvent) => {
        setCurrentStatus(event.status);
        if (event.result) setResult(event.result);
        if (event.error) setError(event.error);
        setConnectionError(false);
        addStep(event.status, event.progress, event.timestamp);
      },
      onError: () => {
        setConnectionError(true);
      },
      onClose: () => {
        setConnectionError(false);
      },
    });

    return cleanup;
  }, [taskId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (stepsEndRef.current) {
      stepsEndRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [steps.length]);

  const isTerminal = TERMINAL_STATUSES.has(currentStatus);

  return (
    <div
      className={`rounded-lg border bg-card text-card-foreground shadow-sm ${
        currentStatus === "running" ? "border-blue-200 dark:border-blue-800" : ""
      }`}
    >
      {/* Card header */}
      <div
        className="flex items-center justify-between px-3 py-2 cursor-pointer select-none"
        onClick={() => setExpanded((prev) => !prev)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium truncate">
            {agentName ? formatAgentName(agentName) : "Task"}
          </span>
          <StatusBadge status={currentStatus} />
          {connectionError && !isTerminal && (
            <span className="text-xs text-amber-500">Reconnecting...</span>
          )}
        </div>
        <button className="shrink-0 p-0.5 text-muted-foreground hover:text-foreground">
          {expanded ? (
            <ChevronUp className="h-4 w-4" />
          ) : (
            <ChevronDown className="h-4 w-4" />
          )}
        </button>
      </div>

      {/* Steps timeline */}
      {expanded && (
        <div className="border-t px-1 py-1 max-h-48 overflow-y-auto">
          {steps.length === 0 && !isTerminal && (
            <div className="px-2 py-3 text-xs text-muted-foreground text-center">
              Waiting for events...
            </div>
          )}
          {steps.map((step, idx) => (
            <TaskStep
              key={step.id}
              status={step.status}
              progress={step.progress}
              timestamp={step.timestamp}
              isLatest={idx === steps.length - 1}
            />
          ))}
          <div ref={stepsEndRef} />

          {/* Terminal result/error summary */}
          {isTerminal && (result || error) && (
            <div
              className={`mx-2 mt-1 mb-1 p-2 rounded text-xs ${
                currentStatus === "completed"
                  ? "bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-300"
                  : "bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-300"
              }`}
            >
              {error && (
                <p>
                  <span className="font-medium">Error:</span>{" "}
                  {(error.message as string) ?? JSON.stringify(error)}
                </p>
              )}
              {result && !error && (
                <p>
                  <span className="font-medium">Result:</span>{" "}
                  {(result.summary as string) ??
                    (result.message as string) ??
                    JSON.stringify(result)}
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Task ID footer */}
      <div className="border-t px-3 py-1">
        <span className="text-[10px] text-muted-foreground font-mono">
          {taskId.slice(0, 8)}...
        </span>
      </div>
    </div>
  );
}
