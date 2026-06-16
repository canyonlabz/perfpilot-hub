"use client";

import { useEffect, useState } from "react";
import { Activity, CheckCircle2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { fetchHealth } from "@/lib/api";

type HealthStatus = "loading" | "healthy" | "unavailable";

export function Header() {
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
      <HealthIndicator status={status} />
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
