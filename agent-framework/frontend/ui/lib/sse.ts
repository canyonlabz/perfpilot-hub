import type { TaskSnapshot, TaskEvent } from "./types";
import { TERMINAL_STATUSES, type TaskStatus } from "./types";

// Reconnection backoff: 1s, 2s, 4s, 8s, 16s (cap)
const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 16000;
const BACKOFF_MULTIPLIER = 2;

export interface TaskStreamCallbacks {
  onSnapshot: (snapshot: TaskSnapshot) => void;
  onStateChange: (event: TaskEvent) => void;
  onError: (error: Event | Error) => void;
  onClose: () => void;
}

/**
 * Subscribe to SSE task events for a given task_id.
 *
 * The backend emits:
 *   event: snapshot  — initial task state on connect/reconnect
 *   event: state     — each state transition (status + progress)
 *   event: ping      — keepalive heartbeat (ignored)
 *
 * Returns a cleanup function that closes the EventSource and stops reconnection.
 */
export function subscribeToTask(
  taskId: string,
  callbacks: TaskStreamCallbacks
): () => void {
  let eventSource: EventSource | null = null;
  let backoffMs = INITIAL_BACKOFF_MS;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let disposed = false;
  let lastStatus: TaskStatus | null = null;

  function connect() {
    if (disposed) return;

    eventSource = new EventSource(`/api/events?task_id=${encodeURIComponent(taskId)}`);

    eventSource.addEventListener("snapshot", (e: MessageEvent) => {
      if (disposed) return;
      backoffMs = INITIAL_BACKOFF_MS;
      try {
        const snapshot: TaskSnapshot = JSON.parse(e.data);
        lastStatus = snapshot.status;
        callbacks.onSnapshot(snapshot);
        if (TERMINAL_STATUSES.has(snapshot.status)) {
          cleanup();
          callbacks.onClose();
        }
      } catch (err) {
        callbacks.onError(err instanceof Error ? err : new Error("Failed to parse snapshot"));
      }
    });

    eventSource.addEventListener("state", (e: MessageEvent) => {
      if (disposed) return;
      backoffMs = INITIAL_BACKOFF_MS;
      try {
        const event: TaskEvent = JSON.parse(e.data);
        lastStatus = event.status;
        callbacks.onStateChange(event);
        if (TERMINAL_STATUSES.has(event.status)) {
          cleanup();
          callbacks.onClose();
        }
      } catch (err) {
        callbacks.onError(err instanceof Error ? err : new Error("Failed to parse state event"));
      }
    });

    // ping events are heartbeats — no action needed

    eventSource.onerror = (e: Event) => {
      if (disposed) return;

      // If we already received a terminal status, the server closed intentionally
      if (lastStatus && TERMINAL_STATUSES.has(lastStatus)) {
        cleanup();
        callbacks.onClose();
        return;
      }

      callbacks.onError(e);
      eventSource?.close();
      eventSource = null;

      // Reconnect with exponential backoff
      reconnectTimer = setTimeout(() => {
        if (!disposed) connect();
      }, backoffMs);
      backoffMs = Math.min(backoffMs * BACKOFF_MULTIPLIER, MAX_BACKOFF_MS);
    };
  }

  function cleanup() {
    disposed = true;
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  connect();
  return cleanup;
}
