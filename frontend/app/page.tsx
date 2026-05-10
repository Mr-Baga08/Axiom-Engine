"use client";

import { useChat } from "@ai-sdk/react";
import { useEffect, useRef, useState } from "react";
import ChatPanel from "@/components/chat-panel";
import InsightsPanel from "@/components/insights-panel";
import { useQueryHistory } from "@/hooks/use-query-history";
import { useSession } from "@/lib/auth/use-session";
import type { FilterState } from "@/types/app-types";

type ActiveChart = {
  chart_type: string;
  dataset: { entity: string; value: number }[];
  roughness?: number;
};

export default function Page() {
  const [input, setInput] = useState("");
  const [insightsOpen, setInsightsOpen] = useState(false);
  const [activeChart, setActiveChart] = useState<ActiveChart | null>(null);
  const [filters, setFilters] = useState<FilterState>({
    genre: "All",
    year: "All",
  });
  const { session, logout } = useSession();
  const { history, addHistoryItem, clearHistory, refetchHistory } = useQueryHistory();
  const lastUserQueryRef = useRef("");
  const prevStatusRef = useRef("");

  const { messages, status, sendMessage } = useChat({
    id: "dumb-lens-session",
    onToolCall: ({ toolCall }) => {
      if (toolCall.toolName === "generate_chart") {
        setActiveChart(toolCall.input as ActiveChart);
        setInsightsOpen(true);
      }
    },
    onError: (error) => {
      // 401 means the api_token expired or was never issued (mock-only login).
      // Force a clean logout so the user re-authenticates rather than seeing
      // silent failures.
      if (
        error.message.includes("401") ||
        error.message.toLowerCase().includes("unauthorized")
      ) {
        logout();
      }
    },
  });

  // Re-fetch backend history once the session is confirmed (post-login)
  useEffect(() => {
    if (session && session !== "loading") {
      refetchHistory();
    }
  }, [session, refetchHistory]);

  // Record history entry when streaming completes
  useEffect(() => {
    if (prevStatusRef.current === "streaming" && status === "ready") {
      const lastAssistant = [...messages]
        .reverse()
        .find((m) => m.role === "assistant");
      if (lastAssistant && lastUserQueryRef.current) {
        // ai@6: tool parts have type "tool-{toolName}", text parts have type "text"
        const toolsUsed = (lastAssistant.parts ?? [])
          .filter((p) => (p.type as string).startsWith("tool-") && "toolName" in p)
          .map((p) => (p as { toolName: string }).toolName);
        const answerText = (lastAssistant.parts ?? [])
          .filter((p) => p.type === "text")
          .map((p) => (p as { text?: string }).text ?? "")
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
    const active: string[] = [];
    if (filters.genre !== "All") {
      active.push(`genre=${filters.genre}`);
    }
    if (filters.year !== "All") {
      active.push(`year=${filters.year}`);
    }
    const prefix = active.length > 0 ? `[Scope: ${active.join(", ")}] ` : "";
    lastUserQueryRef.current = input;
    sendMessage({ text: `${prefix}${input}` });
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
            <button
              className="border border-dashed border-blueprint/30 px-2 py-1 transition-colors hover:border-blueprint hover:text-blueprint"
              onClick={() => setInsightsOpen((v) => !v)}
              type="button"
            >
              {insightsOpen ? "CLOSE PANEL" : "INSIGHTS / HISTORY / UPLOAD ↗"}
            </button>
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
          filters={filters}
          handleInputChange={handleInputChange}
          handleSubmit={handleSubmit}
          hasChart={activeChart !== null}
          input={input}
          isLoading={isLoading}
          messages={messages}
          onFilterChange={setFilters}
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
