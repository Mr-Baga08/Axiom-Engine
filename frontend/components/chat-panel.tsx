"use client";

import type { UIMessage } from "@ai-sdk/react";
import dynamic from "next/dynamic";
import ReactMarkdown from "react-markdown";
import FilterBar from "@/components/filter-bar";
import ToolTrace from "@/components/tool-trace";
import type {
  ChartPayload,
  FilterState,
  SSEToolCallEvent,
} from "@/types/app-types";

const InsightsChart = dynamic(() => import("@/components/insights-chart"), {
  ssr: false,
});

// Runtime shape of tool-call parts (AI SDK v3 types don't surface this directly)
interface ToolCallPart {
  input: unknown;
  toolCallId: string;
  toolName: string;
  type: "tool-call";
}

function tryParseChartPayload(input: unknown): ChartPayload | null {
  if (!input || typeof input !== "object") {
    return null;
  }
  const obj = input as Record<string, unknown>;
  // Backend sends "type" (not "chartType") — verified in agent.py generate_chart
  if (
    typeof obj.type === "string" &&
    ["bar", "line", "pie", "scatter"].includes(obj.type) &&
    Array.isArray(obj.data) &&
    typeof obj.xKey === "string" &&
    Array.isArray(obj.yKeys)
  ) {
    return obj as unknown as ChartPayload;
  }
  return null;
}

interface Props {
  filters: FilterState;
  handleInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  handleSubmit: (e: React.FormEvent<HTMLFormElement>) => void;
  hasChart: boolean;
  input: string;
  isLoading: boolean;
  messages: UIMessage[];
  onFilterChange: (filters: FilterState) => void;
  onOpenInsights: () => void;
}

export default function ChatPanel({
  filters,
  handleInputChange,
  handleSubmit,
  hasChart,
  input,
  isLoading,
  messages,
  onFilterChange,
  onOpenInsights,
}: Props) {
  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <FilterBar filters={filters} onChange={onFilterChange} />
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {/* Thinking indicator — shows while the agent is running */}
        {isLoading && (
          <div className="flex items-center gap-2 font-mono text-xs text-blueprint/40">
            <span className="animate-pulse">●</span>
            <span className="animate-pulse" style={{ animationDelay: "0.2s" }}>●</span>
            <span className="animate-pulse" style={{ animationDelay: "0.4s" }}>●</span>
            <span className="ml-1">AXIOM ENGINE THINKING…</span>
          </div>
        )}

        {messages.map((m) => {
          const text =
            (m.parts ?? [])
              .filter((p) => p.type === "text")
              .map((p) => (p as { type: string; text?: string }).text ?? "")
              .join("");

          // ai@6: tool parts have type "tool-{toolName}" (e.g. "tool-query_sql")
          const toolCallParts: ToolCallPart[] =
            m.role === "user"
              ? []
              : (m.parts ?? [])
                  .filter((p) => (p.type as string).startsWith("tool-") && "toolCallId" in p)
                  .map((p) => p as unknown as ToolCallPart);

          const toolEvents: SSEToolCallEvent[] = toolCallParts
            .filter((p) => p.toolName !== "show_reasoning")
            .map((p) => ({
              id: p.toolCallId,
              name: p.toolName,
              args: p.input as Record<string, unknown>,
              result_preview: "",
              latency_ms: 0,
            }));

          const chartPart = toolCallParts.find(
            (p) => p.toolName === "generate_chart"
          );
          const chartPayload = chartPart
            ? tryParseChartPayload(chartPart.input)
            : null;

          return (
            <div className="space-y-2" key={m.id}>
              <div
                className={[
                  "max-w-3xl border border-dashed px-3 py-2 font-mono text-sm",
                  m.role === "user"
                    ? "ml-auto border-blueprint bg-blueprint text-white"
                    : "border-[var(--blueprint-border)] text-blueprint",
                ].join(" ")}
              >
                {m.role === "user" ? (
                  <p>{text}</p>
                ) : (
                  <div className="prose prose-sm max-w-none prose-p:my-1">
                    <ReactMarkdown>{text}</ReactMarkdown>
                  </div>
                )}
              </div>

              {m.parts
                ?.filter(
                  (
                    p
                  ): p is Extract<
                    NonNullable<typeof m.parts>[number],
                    { type: "tool-call" }
                  > => p.type === "tool-call"
                )
                .map((p, i) => {
                  const toolCall = p as Extract<
                    NonNullable<typeof m.parts>[number],
                    { type: "tool-call"; toolCallId: string }
                  >;
                  const { toolName, input: args, toolCallId } = toolCall;
                  if (toolName !== "show_reasoning") {
                    return null;
                  }

                  const trace = Array.isArray(
                    (args as { trace?: unknown }).trace
                  )
                    ? ((args as { trace: string[] }).trace ?? [])
                    : [];

                  return (
                    <details
                      className="max-w-3xl border border-dashed border-blueprint/30 p-2"
                      key={toolCallId ?? `${m.id}-tool-${i}`}
                    >
                      <summary className="cursor-pointer font-mono text-xs text-blueprint">
                        ▸ Show reasoning trace
                      </summary>
                      <ul className="mt-2 list-inside list-disc space-y-1 font-mono text-xs text-gray-500">
                        {trace.map((step, j) => (
                          // biome-ignore lint/suspicious/noArrayIndexKey: Traces are static, append-only string arrays
                          <li key={`${m.id}-trace-${j}`}>{step}</li>
                        ))}
                      </ul>
                    </details>
                  );
                })}

              {m.role === "user" ? null : (
                <>
                  <ToolTrace events={toolEvents} />
                  {chartPayload ? (
                    <InsightsChart payload={chartPayload} />
                  ) : null}
                </>
              )}
            </div>
          );
        })}
      </div>

      <form
        className="flex items-center gap-2 border-t border-dashed border-[var(--blueprint-border)] p-4"
        onSubmit={handleSubmit}
      >
        <input
          className="w-full border border-dashed border-[var(--blueprint-border)] px-3 py-2 font-mono text-sm text-blueprint outline-none focus:border-blueprint"
          onChange={handleInputChange}
          placeholder="Ask about strategic futures..."
          value={input}
        />
        {hasChart ? (
          <button
            className="hidden rounded-none border border-dashed border-blueprint px-3 py-2 font-mono text-xs text-blueprint transition-colors hover:bg-blueprint hover:text-white lg:block"
            onClick={onOpenInsights}
            type="button"
          >
            INSIGHTS ↗
          </button>
        ) : null}
        <button
          className="border border-dashed border-blueprint px-3 py-2 font-mono text-xs text-blueprint transition-colors hover:bg-blueprint hover:text-white disabled:cursor-not-allowed disabled:opacity-60"
          disabled={isLoading}
          type="submit"
        >
          {isLoading ? "..." : "SEND"}
        </button>
      </form>
    </section>
  );
}
