export interface Thread {
  thread_id: string;
  user_id: string;
  external_thread_id: string | null;
  source: string;
  title: string | null;
  status: "active" | "archived" | "deleted";
  metadata: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
  last_message_at: string | null;
}

export interface ThreadsResponse {
  threads: Thread[];
  limit: number;
  offset: number;
}

export interface Message {
  id: string;
  thread_id: string;
  agent_name: string;
  role: "user" | "assistant";
  content: string | Record<string, unknown>;
  created_at: string | null;
}

export interface MessagesResponse {
  thread_id: string;
  messages: Message[];
  total: number;
  limit: number;
  offset: number;
}

// --- Agent Catalog ---

export interface Skill {
  name: string;
  description: string;
}

export interface Agent {
  name: string;
  display_name: string;
  description: string;
  version: string;
  status: "available" | "in_development";
  skills: Skill[];
  metadata?: Record<string, unknown>;
}

export interface AgentCatalogResponse {
  enabled_agents: string[];
  known_agents: string[];
}

// --- Task Streaming ---

export type TaskStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export const TERMINAL_STATUSES: ReadonlySet<TaskStatus> = new Set([
  "completed",
  "failed",
  "cancelled",
]);

export interface TaskSnapshot {
  task_id: string;
  session_id: string | null;
  external_session_id: string | null;
  agent_name: string;
  status: TaskStatus;
  test_run_id: string | null;
  result: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  submitted_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface TaskEvent {
  task_id: string;
  session_id: string | null;
  external_session_id: string | null;
  agent_name: string;
  status: TaskStatus;
  progress: string | null;
  result: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  timestamp: string;
}

export interface RunSummary {
  test_run_id: string;
  task_count: number;
  statuses: Record<string, number>;
  agents: string[];
  earliest_submitted: string | null;
  latest_completed: string | null;
}

export interface RunDetail {
  test_run_id: string;
  task_count: number;
  tasks: TaskSnapshot[];
}
