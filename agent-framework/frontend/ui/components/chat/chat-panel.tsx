"use client";

import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";

export function ChatPanel() {
  return (
    <div className="flex-1 flex flex-col h-full">
      <CopilotChat
        className="h-full"
        labels={{
          title: "PerfPilot",
          initial: "Hi! I'm PerfPilot, your AI performance testing assistant. How can I help you today?",
          placeholder: "Ask PerfPilot anything about performance testing...",
        }}
      />
    </div>
  );
}
