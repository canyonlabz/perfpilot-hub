"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, CheckCircle2, XCircle, BookOpen, ListChecks, FlaskConical } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { fetchHealth } from "@/lib/api";

type HealthStatus = "loading" | "healthy" | "unavailable";

interface HeaderProps {
  showCatalog?: boolean;
  onToggleCatalog?: () => void;
  showTasks?: boolean;
  onToggleTasks?: () => void;
}

export function Header({ showCatalog, onToggleCatalog, showTasks, onToggleTasks }: HeaderProps) {
  const [status, setStatus] = useState<HealthStatus>("loading");

  useEffect(() => {
    let mounted = true;

    async function checkHealth() {
      try {
        await fetchHealth();
        if (mounted) setStatus("healthy");
      } catch {
        if (mounted) setStatus("unavailable");
      }
    }

    checkHealth();
    const interval = setInterval(checkHealth, 30000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  return (
    <header className="flex items-center justify-between px-4 py-2 border-b bg-card">
      <div className="flex items-center gap-2">
        <Activity className="h-5 w-5 text-primary" />
        <span className="font-semibold text-lg">PerfPilot</span>
      </div>
      <div className="flex items-center gap-3">
        <Link
          href="/runs"
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          title="Test Runs"
        >
          <FlaskConical className="h-3.5 w-3.5" />
          Runs
        </Link>
        {onToggleCatalog && (
          <button
            onClick={onToggleCatalog}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
              showCatalog
                ? "bg-primary text-primary-foreground"
                : "hover:bg-muted text-muted-foreground hover:text-foreground"
            }`}
            title="Agent Catalog"
          >
            <BookOpen className="h-3.5 w-3.5" />
            Agents
          </button>
        )}
        {onToggleTasks && (
          <button
            onClick={onToggleTasks}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
              showTasks
                ? "bg-primary text-primary-foreground"
                : "hover:bg-muted text-muted-foreground hover:text-foreground"
            }`}
            title="Task Progress"
          >
            <ListChecks className="h-3.5 w-3.5" />
            Tasks
          </button>
        )}
        <HealthIndicator status={status} />
      </div>
    </header>
  );
}

function HealthIndicator({ status }: { status: HealthStatus }) {
  switch (status) {
    case "loading":
      return <Badge variant="secondary" className="text-xs">Connecting...</Badge>;
    case "healthy":
      return (
        <Badge variant="success" className="flex items-center gap-1 text-xs">
          <CheckCircle2 className="h-3 w-3" />
          Connected
        </Badge>
      );
    case "unavailable":
      return (
        <Badge variant="destructive" className="flex items-center gap-1 text-xs">
          <XCircle className="h-3 w-3" />
          Disconnected
        </Badge>
      );
  }
}
