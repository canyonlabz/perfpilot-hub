"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { Activity, RefreshCw, ArrowLeft, Inbox } from "lucide-react";
import { fetchRuns, type RunsResponse } from "@/lib/api";
import { RunCard } from "@/components/runs/run-card";

type LoadState = "loading" | "loaded" | "error";

export default function RunsListPage() {
  const [runs, setRuns] = useState<RunsResponse | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [refreshing, setRefreshing] = useState(false);

  const loadRuns = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoadState("loading");

    try {
      const data = await fetchRuns(50);
      setRuns(data);
      setLoadState("loaded");
    } catch (err) {
      console.error("Failed to fetch runs:", err);
      setLoadState("error");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  return (
    <div className="flex flex-col h-screen">
      {/* Mini header */}
      <header className="flex items-center justify-between px-4 py-2 border-b bg-card">
        <div className="flex items-center gap-2">
          <Link href="/" className="flex items-center gap-2 hover:opacity-80 transition-opacity">
            <Activity className="h-5 w-5 text-primary" />
            <span className="font-semibold text-lg">PerfPilot</span>
          </Link>
          <span className="text-muted-foreground">/</span>
          <span className="text-sm font-medium">Test Runs</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => loadRuns(true)}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium hover:bg-muted text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <Link
            href="/"
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to Chat
          </Link>
        </div>
      </header>

      {/* Content */}
      <main className="flex-1 overflow-y-auto p-6">
        <div className="max-w-3xl mx-auto">
          {loadState === "loading" && (
            <div className="flex items-center justify-center py-20">
              <p className="text-sm text-muted-foreground animate-pulse">
                Loading test runs...
              </p>
            </div>
          )}

          {loadState === "error" && (
            <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center">
              <p className="text-sm text-destructive font-medium">
                Failed to load test runs
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                Make sure the AG-UI backend is running on port 8002.
              </p>
              <button
                onClick={() => loadRuns()}
                className="mt-3 px-3 py-1 rounded-md text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                Retry
              </button>
            </div>
          )}

          {loadState === "loaded" && runs && runs.runs.length === 0 && (
            <div className="rounded-lg border border-dashed p-12 text-center">
              <Inbox className="h-10 w-10 mx-auto text-muted-foreground/40" />
              <h2 className="mt-4 text-sm font-semibold">No test runs yet</h2>
              <p className="mt-1.5 text-xs text-muted-foreground max-w-md mx-auto">
                Test runs appear here after the orchestrator executes a
                performance test with a <code className="font-mono bg-muted px-1 py-0.5 rounded">test_run_id</code>.
                Tasks without a <code className="font-mono bg-muted px-1 py-0.5 rounded">test_run_id</code> are
                only visible in the thread-scoped Tasks panel.
              </p>
              <Link
                href="/"
                className="mt-4 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                Start a test from Chat
              </Link>
            </div>
          )}

          {loadState === "loaded" && runs && runs.runs.length > 0 && (
            <>
              <div className="flex items-center justify-between mb-4">
                <p className="text-xs text-muted-foreground">
                  {runs.total ?? runs.runs.length} {(runs.total ?? runs.runs.length) === 1 ? "run" : "runs"} total
                </p>
              </div>
              <div className="space-y-3">
                {runs.runs.map((run) => (
                  <RunCard key={run.test_run_id} run={run} />
                ))}
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
