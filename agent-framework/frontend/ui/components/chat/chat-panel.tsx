"use client";

import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";

interface ChatPanelProps {
  threadId: string | null;
}

export function ChatPanel({ threadId }: ChatPanelProps) {
  if (!threadId) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground">
        <p className="text-sm">Select a thread or create a new one to start chatting.</p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-full">
      <CopilotChat
        key={threadId}
        className="h-full"
        labels={{
          title: "PerfPilot",
          initial:
            "Hi! I'm PerfPilot, your AI performance testing assistant. How can I help you today?",
          placeholder: "Ask PerfPilot anything about performance testing...",
        }}
      />
    </div>
  );
}
