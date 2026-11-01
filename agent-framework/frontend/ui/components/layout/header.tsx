"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  BookOpen,
  ListChecks,
  FlaskConical,
  Github,
  Settings,
} from "lucide-react";
import { fetchHealth } from "@/lib/api";

type HealthStatus = "loading" | "healthy" | "unavailable";

interface HeaderProps {
  showCatalog?: boolean;
  onToggleCatalog?: () => void;
  showTasks?: boolean;
  onToggleTasks?: () => void;
  showGitHubCreds?: boolean;
  onToggleGitHubCreds?: () => void;
  gitHubCredsAttached?: boolean;
  settingsActive?: boolean;
}

export function Header({
  showCatalog,
  onToggleCatalog,
  showTasks,
  onToggleTasks,
  showGitHubCreds,
  onToggleGitHubCreds,
  gitHubCredsAttached,
  settingsActive,
}: HeaderProps) {
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
    <header className="pp-header">
      <Link href="/" className="pp-brand">
        <span className="pp-brand-logo" aria-hidden="true">
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M5 20V4h8a5 5 0 0 1 0 10H5" />
          </svg>
        </span>
        PerfPilot
      </Link>

      <div className="pp-header-cluster">
        <nav className="pp-nav" aria-label="Primary">
          <Link href="/runs" className="pp-nav-item" title="Test Runs">
            <FlaskConical className="h-3.5 w-3.5" />
            Runs
          </Link>
          <button
            type="button"
            className="pp-nav-item"
            data-active={showCatalog ? "true" : undefined}
            onClick={onToggleCatalog}
            disabled={!onToggleCatalog}
            title="Agent Catalog"
          >
            <BookOpen className="h-3.5 w-3.5" />
            Agents
          </button>
          <button
            type="button"
            className="pp-nav-item"
            data-active={showTasks ? "true" : undefined}
            onClick={onToggleTasks}
            disabled={!onToggleTasks}
            title="Task Progress"
          >
            <ListChecks className="h-3.5 w-3.5" />
            Tasks
          </button>
          <button
            type="button"
            className="pp-nav-item"
            data-active={showGitHubCreds ? "true" : undefined}
            onClick={onToggleGitHubCreds}
            disabled={!onToggleGitHubCreds}
            title={
              gitHubCredsAttached
                ? "GitHub credentials attached — click to review or clear"
                : "Attach a GitHub repo for script pushes (session only)"
            }
          >
            <Github className="h-3.5 w-3.5" />
            GitHub
            {gitHubCredsAttached && (
              <span
                className="h-1.5 w-1.5 rounded-full bg-[var(--success)]"
                aria-hidden
              />
            )}
          </button>
        </nav>

        <Link
          href="/settings"
          className="pp-nav-item"
          data-active={settingsActive ? "true" : undefined}
          title="Settings"
          aria-current={settingsActive ? "page" : undefined}
        >
          <Settings className="h-3.5 w-3.5" />
          Settings
        </Link>
        <HealthIndicator status={status} />
      </div>
    </header>
  );
}

function HealthIndicator({ status }: { status: HealthStatus }) {
  const label =
    status === "healthy"
      ? "Connected"
      : status === "unavailable"
        ? "Disconnected"
        : "Connecting...";

  return (
    <div className="pp-status" data-state={status}>
      <span className="pp-status-dot" aria-hidden="true" />
      {label}
    </div>
  );
}
