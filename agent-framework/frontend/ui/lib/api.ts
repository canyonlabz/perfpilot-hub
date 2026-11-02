import type {
  Thread,
  ThreadsResponse,
  MessagesResponse,
  Agent,
  AgentCatalogResponse,
  RunSummary,
  RunDetail,
  TasksResponse,
  HitlApproval,
  HitlTaskResponse,
} from "./types";
import {
  focusAreaForComponent,
  type SecretComponent,
  type SettingsSecret,
} from "./settings-catalog";

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

// --- Settings API ---

export interface WebUISettings {
  context_indicator: {
    enabled: boolean;
  };
}

const DEFAULT_SETTINGS: WebUISettings = {
  context_indicator: { enabled: true },
};

export async function fetchSettings(): Promise<WebUISettings> {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) return DEFAULT_SETTINGS;
    return res.json();
  } catch {
    return DEFAULT_SETTINGS;
  }
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

// --- Thread-scoped Tasks API (BUG-09) ---

export async function fetchTasksForThread(
  threadId: string,
  limit = 20,
  offset = 0
): Promise<TasksResponse> {
  const params = new URLSearchParams({
    thread_id: threadId,
    limit: String(limit),
    offset: String(offset),
  });
  const res = await fetch(`/api/tasks?${params}`);
  if (!res.ok) throw new Error(`Failed to fetch tasks: ${res.status}`);
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

// --- HITL Approval API ---

export async function fetchHitlForTask(
  taskId: string
): Promise<HitlTaskResponse> {
  const res = await fetch(`/api/hitl/tasks/${encodeURIComponent(taskId)}`);
  if (!res.ok)
    throw new Error(`Failed to fetch HITL approvals: ${res.status}`);
  return res.json();
}

export async function approveHitl(
  approvalId: number,
  decidedBy?: string
): Promise<HitlApproval> {
  const res = await fetch("/api/hitl/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approval_id: approvalId, decided_by: decidedBy }),
  });
  if (!res.ok) throw new Error(`Failed to approve HITL: ${res.status}`);
  return res.json();
}

export async function rejectHitl(
  approvalId: number,
  feedback?: string,
  decidedBy?: string
): Promise<HitlApproval> {
  const res = await fetch("/api/hitl/reject", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      approval_id: approvalId,
      feedback: feedback || null,
      decided_by: decidedBy,
    }),
  });
  if (!res.ok) throw new Error(`Failed to reject HITL: ${res.status}`);
  return res.json();
}

// --- Settings persistence (Profile / Secrets / Projects) ---

async function settingsError(res: Response): Promise<Error> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") {
      return new Error(body.detail);
    }
  } catch {
    /* ignore non-JSON */
  }
  return new Error(`Request failed: ${res.status}`);
}

export type ProfileRecord = {
  display_name: string | null;
  email: string | null;
};

export async function fetchProfile(): Promise<ProfileRecord> {
  const res = await fetch("/api/me", { credentials: "include" });
  if (!res.ok) throw await settingsError(res);
  return res.json();
}

export async function saveProfile(body: {
  display_name: string;
  email: string;
}): Promise<ProfileRecord> {
  const res = await fetch("/api/me", {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await settingsError(res);
  return res.json();
}

type SecretApiRow = {
  id: string;
  name: string;
  component: SecretComponent;
  focus_area?: string;
  updated_at: string | null;
};

export function secretFromApi(row: SecretApiRow): SettingsSecret {
  return {
    id: row.id,
    sectionId: focusAreaForComponent(row.component, row.focus_area),
    name: row.name,
    component: row.component,
    masked: "••••••••••••",
    added: row.updated_at ? row.updated_at.slice(0, 10) : "Saved",
  };
}

export async function fetchSecrets(): Promise<SettingsSecret[]> {
  const res = await fetch("/api/secrets", { credentials: "include" });
  if (!res.ok) throw await settingsError(res);
  const data = await res.json();
  return (data.secrets || []).map(secretFromApi);
}

export async function createSecret(body: {
  component: SecretComponent;
  name: string;
  fields: Record<string, string>;
}): Promise<SettingsSecret> {
  const res = await fetch("/api/secrets", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await settingsError(res);
  return secretFromApi(await res.json());
}

export async function deleteSecret(id: string): Promise<void> {
  const res = await fetch(`/api/secrets/${id}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw await settingsError(res);
}

export type ProjectBinding = {
  project_key: string;
  name: string;
  is_enabled: boolean;
  is_active: boolean;
  confluence_token_name: string | null;
  blazemeter_token_name: string | null;
  datadog_token_name: string | null;
  github_token_name: string | null;
};

export async function fetchProjectBindings(): Promise<ProjectBinding[]> {
  const res = await fetch("/api/project-bindings", { credentials: "include" });
  if (!res.ok) throw await settingsError(res);
  const data = await res.json();
  return data.projects || [];
}

export async function saveProjectBindings(
  bindings: ProjectBinding[]
): Promise<ProjectBinding[]> {
  const res = await fetch("/api/project-bindings", {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      bindings: bindings.map((row) => ({
        project_key: row.project_key,
        is_enabled: row.is_enabled,
        is_active: row.is_active,
        confluence_token_name: row.confluence_token_name,
        blazemeter_token_name: row.blazemeter_token_name,
        datadog_token_name: row.datadog_token_name,
        github_token_name: row.github_token_name,
      })),
    }),
  });
  if (!res.ok) throw await settingsError(res);
  const data = await res.json();
  return data.projects || [];
}
