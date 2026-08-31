"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Github,
  ShieldCheck,
  Trash2,
  Save,
  AlertTriangle,
  Info,
} from "lucide-react";
import {
  clearGitHubCreds,
  getGitHubCredsMetadata,
  saveGitHubCreds,
  subscribeToCredsChanges,
  type GitHubCredsMetadata,
} from "@/lib/github-creds";

/**
 * GitHubCredsCard — session-scoped GitHub credential capture.
 *
 * Presents a form for the current chat user to attach a GitHub repository
 * (URL + PAT + optional branch / path) that the PerfPilot Script Agent may
 * use to push a freshly generated JMX script. Credentials are AES-GCM
 * encrypted and stored in the browser session only. See
 * {@link ../../lib/github-creds.ts} for the encryption contract.
 */
export function GitHubCredsCard() {
  const [metadata, setMetadata] = useState<GitHubCredsMetadata | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [branch, setBranch] = useState("");
  const [path, setPath] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    const current = await getGitHubCredsMetadata();
    setMetadata(current);
    setLoaded(true);
    if (current) {
      setUrl(current.url);
      setBranch(current.branch);
      setPath(current.path);
    }
  }, []);

  useEffect(() => {
    refresh();
    const unsubscribe = subscribeToCredsChanges(() => {
      refresh();
    });
    return unsubscribe;
  }, [refresh]);

  const handleSave = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setSaving(true);
      setError(null);
      setNotice(null);
      try {
        const result = await saveGitHubCreds({
          url,
          token,
          branch,
          path,
        });
        if (!result.ok) {
          setError(result.error ?? "Failed to save credentials");
          return;
        }
        setToken("");
        setShowToken(false);
        setMetadata(result.metadata ?? null);
        setNotice(
          "Saved to this browser session only. Credentials will be wiped when the tab is closed."
        );
      } finally {
        setSaving(false);
      }
    },
    [url, token, branch, path]
  );

  const handleClear = useCallback(() => {
    clearGitHubCreds();
    setUrl("");
    setToken("");
    setBranch("");
    setPath("");
    setMetadata(null);
    setError(null);
    setNotice("Credentials cleared from this session.");
  }, []);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <div className="flex items-center gap-2">
          <Github className="h-4 w-4 text-primary" />
          <span className="font-semibold text-sm">GitHub credentials</span>
        </div>
        <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          Session only
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          <div className="flex items-start gap-2">
            <ShieldCheck className="h-4 w-4 mt-0.5 flex-shrink-0" />
            <div className="space-y-1">
              <p className="font-medium">Privacy disclosure</p>
              <ul className="list-disc list-inside space-y-0.5">
                <li>
                  Credentials are AES-256-GCM encrypted in your browser
                  session only. Nothing is written to disk.
                </li>
                <li>
                  Closing this tab wipes the token. Reopening the app
                  requires you to attach the token again.
                </li>
                <li>
                  When you request a Git push, PerfPilot forwards the
                  token to the local Script Agent for a single push
                  operation. It is not stored on the server.
                </li>
                <li>
                  Use a fine-scoped personal access token
                  (Contents: Read/Write on this repo, no admin rights).
                </li>
              </ul>
            </div>
          </div>
        </div>

        {loaded && metadata && (
          <div className="rounded-md border bg-muted/40 p-3 text-xs space-y-1">
            <div className="flex items-center gap-1.5">
              <Info className="h-3 w-3 text-muted-foreground" />
              <span className="font-medium">Current session target</span>
            </div>
            <div className="pl-4 text-muted-foreground space-y-0.5">
              <div>
                <span className="font-mono">
                  {metadata.owner || "?"}/{metadata.repo || "?"}
                </span>
              </div>
              {metadata.branch && (
                <div>
                  Branch: <span className="font-mono">{metadata.branch}</span>
                </div>
              )}
              {metadata.path && (
                <div>
                  Path: <span className="font-mono">{metadata.path}</span>
                </div>
              )}
              <div>
                Token attached:{" "}
                <span className="font-mono">
                  {metadata.has_token ? "yes" : "no"}
                </span>
              </div>
              <div>
                Updated:{" "}
                <span className="font-mono">
                  {new Date(metadata.updated_at).toLocaleTimeString()}
                </span>
              </div>
            </div>
          </div>
        )}

        <form onSubmit={handleSave} className="space-y-3">
          <div className="space-y-1">
            <label className="text-xs font-medium">
              Repository URL
              <span className="ml-1 text-muted-foreground font-normal">
                (https://github.com/owner/repo)
              </span>
            </label>
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://github.com/your-org/your-perf-repo"
              className="w-full rounded-md border bg-background px-2 py-1.5 text-xs
                         placeholder:text-muted-foreground focus-visible:outline-none
                         focus-visible:ring-1 focus-visible:ring-ring"
              required
            />
          </div>

          <div className="space-y-1">
            <label className="text-xs font-medium">
              Personal access token
            </label>
            <div className="flex gap-1">
              <input
                type={showToken ? "text" : "password"}
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={
                  metadata?.has_token
                    ? "Re-enter to replace the current token"
                    : "Paste your fine-grained or classic personal access token"
                }
                autoComplete="off"
                className="flex-1 rounded-md border bg-background px-2 py-1.5 text-xs
                           placeholder:text-muted-foreground focus-visible:outline-none
                           focus-visible:ring-1 focus-visible:ring-ring"
              />
              <button
                type="button"
                onClick={() => setShowToken((v) => !v)}
                className="rounded-md border px-2 py-1.5 text-[10px] font-medium
                           hover:bg-muted text-muted-foreground hover:text-foreground
                           transition-colors"
              >
                {showToken ? "Hide" : "Show"}
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <label className="text-xs font-medium">Branch (optional)</label>
              <input
                type="text"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                placeholder="perf/{test_run_id}"
                className="w-full rounded-md border bg-background px-2 py-1.5 text-xs
                           placeholder:text-muted-foreground focus-visible:outline-none
                           focus-visible:ring-1 focus-visible:ring-ring"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium">Path (optional)</label>
              <input
                type="text"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="performance/{jmx}"
                className="w-full rounded-md border bg-background px-2 py-1.5 text-xs
                           placeholder:text-muted-foreground focus-visible:outline-none
                           focus-visible:ring-1 focus-visible:ring-ring"
              />
            </div>
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {notice && !error && (
            <div className="rounded-md border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
              {notice}
            </div>
          )}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={saving || !url || (!token && !metadata?.has_token)}
              className="flex-1 flex items-center justify-center gap-1.5 rounded-md
                         bg-primary px-3 py-1.5 text-xs font-medium
                         text-primary-foreground hover:bg-primary/90
                         disabled:opacity-50 disabled:cursor-not-allowed
                         transition-colors"
            >
              <Save className="h-3.5 w-3.5" />
              {saving
                ? "Saving..."
                : metadata?.has_token
                ? "Update credentials"
                : "Save for this session"}
            </button>
            <button
              type="button"
              onClick={handleClear}
              disabled={!metadata}
              className="flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs
                         font-medium hover:bg-muted text-muted-foreground
                         hover:text-foreground disabled:opacity-50
                         disabled:cursor-not-allowed transition-colors"
              title="Clear GitHub credentials from this session"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Clear
            </button>
          </div>
        </form>

        <div className="rounded-md border border-dashed p-3 text-[11px] text-muted-foreground space-y-1">
          <div className="flex items-center gap-1.5 font-medium text-foreground">
            <Info className="h-3 w-3" />
            How PerfPilot uses this
          </div>
          <p>
            When you ask the orchestrator to build a new JMeter script,
            it detects an attached repo and delegates to the Script Agent
            with an <code className="font-mono">scm</code> block. The
            Script Agent runs a local smoke test, pushes the JMX to the
            branch you configured, and the Execution Agent then provisions
            a new BlazeMeter test (subject to your HITL gates).
          </p>
        </div>
      </div>
    </div>
  );
}
