"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Activity, RefreshCw, ArrowLeft } from "lucide-react";
import { fetchRunDetail } from "@/lib/api";
import { RunDetailView } from "@/components/runs/run-detail";
import type { RunDetail } from "@/lib/types";

type LoadState = "loading" | "loaded" | "error" | "not_found";

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  const testRunId = decodeURIComponent(params.id);

  const [run, setRun] = useState<RunDetail | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [refreshing, setRefreshing] = useState(false);

  const loadDetail = useCallback(
    async (isRefresh = false) => {
      if (isRefresh) setRefreshing(true);
      else setLoadState("loading");

      try {
        const data = await fetchRunDetail(testRunId);
        setRun(data);
        setLoadState("loaded");
      } catch (err) {
        console.error("Failed to fetch run detail:", err);
        const message = err instanceof Error ? err.message : "";
        setLoadState(message.includes("404") ? "not_found" : "error");
      } finally {
        setRefreshing(false);
      }
    },
    [testRunId]
  );

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

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
          <Link href="/runs" className="text-sm hover:underline">
            Test Runs
          </Link>
          <span className="text-muted-foreground">/</span>
          <span className="text-sm font-medium truncate max-w-[200px]">{testRunId}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => loadDetail(true)}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium hover:bg-muted text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <Link
            href="/runs"
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            All Runs
          </Link>
        </div>
      </header>

      {/* Content */}
      <main className="flex-1 overflow-y-auto p-6">
        {loadState === "loading" && (
          <div className="flex items-center justify-center py-20">
            <p className="text-sm text-muted-foreground animate-pulse">
              Loading run details...
            </p>
          </div>
        )}

        {loadState === "error" && (
          <div className="max-w-3xl mx-auto">
            <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center">
              <p className="text-sm text-destructive font-medium">
                Failed to load run details
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                Make sure the AG-UI backend is running on port 8002.
              </p>
              <button
                onClick={() => loadDetail()}
                className="mt-3 px-3 py-1 rounded-md text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                Retry
              </button>
            </div>
          </div>
        )}

        {loadState === "not_found" && (
          <div className="max-w-3xl mx-auto">
            <div className="rounded-lg border border-dashed p-8 text-center">
              <p className="text-sm font-medium">Run not found</p>
              <p className="text-xs text-muted-foreground mt-1">
                No test run with ID &ldquo;{testRunId}&rdquo; was found.
              </p>
              <Link
                href="/runs"
                className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                Back to Runs
              </Link>
            </div>
          </div>
        )}

        {loadState === "loaded" && run && <RunDetailView run={run} />}
      </main>
    </div>
  );
}
