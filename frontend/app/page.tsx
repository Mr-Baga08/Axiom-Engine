"use client";

import { useChat } from "@ai-sdk/react";
import { useEffect, useRef, useState } from "react";
import ChatPanel from "@/components/chat-panel";
import InsightsPanel from "@/components/insights-panel";
import { useSession } from "@/lib/auth/use-session";
import { useQueryHistory } from "@/hooks/useQueryHistory";

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
  const { history, addHistoryItem, clearHistory } = useQueryHistory();
  const lastUserQueryRef = useRef("");
  const prevStatusRef = useRef("");

  const { messages, status, sendMessage } = useChat({
    id: "dumb-lens-session",
    onToolCall: ({ toolCall }) => {
      if (toolCall.toolName === "render_chart") {
        setActiveChart(toolCall.input as ActiveChart);
        setInsightsOpen(true);
      }
    },
  });

  // Record history entry when streaming completes
  useEffect(() => {
    if (prevStatusRef.current === "streaming" && status === "ready") {
      const lastAssistant = [...messages]
        .reverse()
        .find((m) => m.role === "assistant");
      if (lastAssistant && lastUserQueryRef.current) {
        const toolsUsed = (lastAssistant.parts ?? [])
          .filter((p) => p.type === "tool-call")
          .map((p) => (p as { toolName: string }).toolName);
        const answerText = (lastAssistant.parts ?? [])
          .filter((p) => p.type === "text")
          .map((p) => (p as { text: string }).text)
          .join("");
        addHistoryItem({
          id: lastAssistant.id,
          query: lastUserQueryRef.current,
          answerPreview: answerText.slice(0, 100),
          toolsUsed,
          timestamp: Date.now(),
        });
        lastUserQueryRef.current = "";
      }
    }
    prevStatusRef.current = status;
  }, [status, messages, addHistoryItem]);

  const isLoading = status === "streaming" || status === "submitted";

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!input.trim()) {
      return;
    }
    lastUserQueryRef.current = input;
    sendMessage({ text: input });
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
              className="transition-colors hover:text-blueprint"
              onClick={logout}
              type="button"
            >
              LOGOUT
            </button>
          </span>
        </header>
      ) : null}

      <section className="flex min-h-0 flex-1">
        <ChatPanel
          handleInputChange={handleInputChange}
          handleSubmit={handleSubmit}
          hasChart={activeChart !== null}
          input={input}
          isLoading={isLoading}
          messages={messages}
          onOpenInsights={() => setInsightsOpen(true)}
        />
        <InsightsPanel
          chart={activeChart}
          clearHistory={clearHistory}
          history={history}
          onHistorySelect={setInput}
          onToggle={() => setInsightsOpen((value) => !value)}
          open={insightsOpen}
        />
      </section>
    </main>
  );
}
