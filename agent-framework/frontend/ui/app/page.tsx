"use client";

import { Header } from "@/components/layout/header";
import { ChatPanel } from "@/components/chat/chat-panel";

export default function HomePage() {
  return (
    <div className="flex flex-col h-screen">
      <Header />
      <ChatPanel />
    </div>
  );
}
