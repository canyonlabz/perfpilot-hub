"use client";

import { useEffect, useState } from "react";
import { RefreshCw, AlertCircle } from "lucide-react";
import { AgentCard } from "./agent-card";
import { fetchAgentCatalog, fetchAgentCard } from "@/lib/api";
import type { Agent } from "@/lib/types";

type LoadState = "loading" | "loaded" | "error";

export function CatalogPanel() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);

  async function loadCatalog() {
    setLoadState("loading");
    setError(null);
    try {
      const catalog = await fetchAgentCatalog();
      const allAgentNames = catalog.known_agents ?? catalog.enabled_agents;

      const cards = await Promise.allSettled(
        allAgentNames.map((name) => fetchAgentCard(name))
      );

      const resolved: Agent[] = cards
        .filter(
          (r): r is PromiseFulfilledResult<Agent> => r.status === "fulfilled"
        )
        .map((r) => r.value);

      resolved.sort((a, b) => {
        if (a.status === "available" && b.status !== "available") return -1;
        if (a.status !== "available" && b.status === "available") return 1;
        return a.display_name.localeCompare(b.display_name);
      });

      setAgents(resolved);
      setLoadState("loaded");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load catalog");
      setLoadState("error");
    }
  }

  useEffect(() => {
    loadCatalog();
  }, []);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <h2 className="text-sm font-semibold">Agent Catalog</h2>
        <button
          onClick={loadCatalog}
          disabled={loadState === "loading"}
          className="p-1.5 rounded-md hover:bg-muted transition-colors disabled:opacity-50"
          title="Refresh catalog"
        >
          <RefreshCw
            className={`h-4 w-4 ${loadState === "loading" ? "animate-spin" : ""}`}
          />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {loadState === "loading" && (
          <div className="flex items-center justify-center h-32">
            <p className="text-sm text-muted-foreground animate-pulse">
              Loading agents...
            </p>
          </div>
        )}

        {loadState === "error" && (
          <div className="flex flex-col items-center justify-center h-32 gap-2">
            <AlertCircle className="h-5 w-5 text-destructive" />
            <p className="text-sm text-destructive">{error}</p>
            <button
              onClick={loadCatalog}
              className="text-xs text-muted-foreground hover:text-foreground underline"
            >
              Retry
            </button>
          </div>
        )}

        {loadState === "loaded" && agents.length === 0 && (
          <div className="flex items-center justify-center h-32">
            <p className="text-sm text-muted-foreground">
              No agents registered.
            </p>
          </div>
        )}

        {loadState === "loaded" && agents.length > 0 && (
          <div className="grid gap-3">
            {agents.map((agent) => (
              <AgentCard key={agent.name} agent={agent} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
