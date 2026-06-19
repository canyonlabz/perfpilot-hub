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
