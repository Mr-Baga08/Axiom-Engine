"use client";

import { useChat } from "@ai-sdk/react";
import { useState } from "react";
import ChatPanel from "@/components/chat-panel";
import InsightsPanel from "@/components/insights-panel";
import { useSession } from "@/lib/auth/use-session";

type ActiveChart = {
  chart_type: string;
  dataset: { entity: string; value: number }[];
  roughness?: number;
};

export default function Page() {
  const [input, setInput] = useState("");
  const [insightsOpen, setInsightsOpen] = useState(false);
  const [activeChart, setActiveChart] = useState<ActiveChart | null>(null);
  const { session, logout } = useSession();

  const { messages, status, sendMessage } = useChat({
    id: 'dumb-lens-session',
    onToolCall: ({ toolCall }) => {
      if (toolCall.toolName === "render_chart") {
        setActiveChart(toolCall.input as ActiveChart);
        setInsightsOpen(true);
      }
    },
  });

  const isLoading = status === "streaming" || status === "submitted";

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!input.trim()) {
      return;
    }
    void sendMessage({ text: input });
    setInput("");
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInput(e.target.value);
  };

  return (
    <main className="flex h-screen flex-col bg-white text-blueprint">
      {session && session !== "loading" ? (
        <header className="flex items-center justify-between border-b border-dashed border-blueprint/20 px-4 py-2 font-mono text-xs text-blueprint/50">
          <span>AXIOM ENGINE</span>
          <span className="flex items-center gap-4">
            <span>{session.uid}</span>
            <button
              type="button"
              onClick={logout}
              className="transition-colors hover:text-blueprint"
            >
              LOGOUT
            </button>
          </span>
        </header>
      ) : null}

      <section className="flex min-h-0 flex-1">
        <ChatPanel
          messages={messages}
          input={input}
          isLoading={isLoading}
          handleInputChange={handleInputChange}
          handleSubmit={handleSubmit}
          onOpenInsights={() => setInsightsOpen(true)}
          hasChart={activeChart !== null}
        />
        <InsightsPanel
          chart={activeChart}
          open={insightsOpen}
          onToggle={() => setInsightsOpen((value) => !value)}
        />
      </section>
    </main>
  );
}
