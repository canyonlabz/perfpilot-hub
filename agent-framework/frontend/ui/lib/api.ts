import type {
  Thread,
  ThreadsResponse,
  MessagesResponse,
  Agent,
  AgentCatalogResponse,
  RunSummary,
  RunDetail,
} from "./types";

export interface HealthResponse {
  service: string;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch("/health");
  if (!res.ok) {
    throw new Error(`Health check failed: ${res.status}`);
  }
  return res.json();
}

// --- Thread API ---

export async function listThreads(
  includeArchived = false
): Promise<ThreadsResponse> {
  const params = new URLSearchParams();
  if (includeArchived) params.set("include_archived", "true");
  const res = await fetch(`/api/threads?${params}`);
  if (!res.ok) throw new Error(`Failed to list threads: ${res.status}`);
  return res.json();
}

export async function createThread(title?: string): Promise<Thread> {
  const res = await fetch("/api/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title || null, metadata: {} }),
  });
  if (!res.ok) throw new Error(`Failed to create thread: ${res.status}`);
  return res.json();
}

export async function renameThread(
  threadId: string,
  title: string
): Promise<Thread> {
  const res = await fetch(`/api/threads/${threadId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`Failed to rename thread: ${res.status}`);
  return res.json();
}

export async function archiveThread(threadId: string): Promise<Thread> {
  const res = await fetch(`/api/threads/${threadId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: "archived" }),
  });
  if (!res.ok) throw new Error(`Failed to archive thread: ${res.status}`);
  return res.json();
}

export async function deleteThread(threadId: string): Promise<void> {
  const res = await fetch(`/api/threads/${threadId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete thread: ${res.status}`);
}

export async function fetchMessages(
  threadId: string
): Promise<MessagesResponse> {
  const res = await fetch(`/api/threads/${threadId}/messages`);
  if (!res.ok) throw new Error(`Failed to fetch messages: ${res.status}`);
  return res.json();
}

// --- Agent Catalog API (A2A server, proxied via /a2a/*) ---

export async function fetchAgentCatalog(): Promise<AgentCatalogResponse> {
  const res = await fetch("/a2a/agents");
  if (!res.ok) throw new Error(`Failed to fetch agent catalog: ${res.status}`);
  return res.json();
}

export async function fetchAgentCard(agentName: string): Promise<Agent> {
  const res = await fetch(
    `/a2a/agents/${agentName}/.well-known/agent.json`
  );
  if (!res.ok)
    throw new Error(`Failed to fetch agent card for ${agentName}: ${res.status}`);
  return res.json();
}

// --- Runs / Tasks API ---

export interface RunsResponse {
  runs: RunSummary[];
  total: number;
  limit: number;
  offset: number;
}

export async function fetchRuns(
  limit = 20,
  offset = 0
): Promise<RunsResponse> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  const res = await fetch(`/api/runs?${params}`);
  if (!res.ok) throw new Error(`Failed to fetch runs: ${res.status}`);
  return res.json();
}

export async function fetchRunDetail(testRunId: string): Promise<RunDetail> {
  const res = await fetch(`/api/runs/${encodeURIComponent(testRunId)}`);
  if (!res.ok)
    throw new Error(`Failed to fetch run detail: ${res.status}`);
  return res.json();
}

export interface SessionInfo {
  session_id: string;
  user_id: string | null;
  source: string | null;
  started_at: string | null;
}

export async function fetchCurrentSession(): Promise<SessionInfo> {
  const res = await fetch("/api/sessions/me");
  if (!res.ok)
    throw new Error(`Failed to fetch current session: ${res.status}`);
  return res.json();
}
