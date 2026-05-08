"use client";

import type { UIMessage } from "@ai-sdk/react";
import ReactMarkdown from "react-markdown";

interface Props {
  messages: UIMessage[];
  input: string;
  isLoading: boolean;
  hasChart: boolean;
  handleInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  handleSubmit: (e: React.FormEvent<HTMLFormElement>) => void;
  onOpenInsights: () => void;
}

export default function ChatPanel({
  messages,
  input,
  isLoading,
  hasChart,
  handleInputChange,
  handleSubmit,
  onOpenInsights,
}: Props) {
  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {messages.map((m) => {
          const text =
            m.parts
              ?.filter((p): p is Extract<typeof p, { type: "text" }> => p.type === "text")
              .map((p) => p.text)
              .join("") ?? "";

          return (
            <div key={m.id} className="space-y-2">
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
                    p,
                  ): p is Extract<NonNullable<typeof m.parts>[number], { type: "tool-call" }> =>
                    p.type === "tool-call",
                )
                .map((p, i) => {
                  const { toolName, input: args } = p;
                  if (toolName !== "show_reasoning") {
                    return null;
                  }

                  const trace = Array.isArray((args as { trace?: unknown }).trace)
                    ? ((args as { trace: string[] }).trace ?? [])
                    : [];

                  return (
                    <details
                      key={`${m.id}-${i}`}
                      className="max-w-3xl border border-dashed border-blueprint/30 p-2"
                    >
                      <summary className="cursor-pointer font-mono text-xs text-blueprint">
                        ▸ Show reasoning trace
                      </summary>
                      <ul className="mt-2 list-inside list-disc space-y-1 font-mono text-xs text-gray-500">
                        {trace.map((step, j) => (
                          <li key={`${m.id}-trace-${j}`}>{step}</li>
                        ))}
                      </ul>
                    </details>
                  );
                })}
            </div>
          );
        })}
      </div>

      <form
        onSubmit={handleSubmit}
        className="flex items-center gap-2 border-t border-dashed border-[var(--blueprint-border)] p-4"
      >
        <input
          value={input}
          onChange={handleInputChange}
          placeholder="Ask about strategic futures..."
          className="w-full border border-dashed border-[var(--blueprint-border)] px-3 py-2 font-mono text-sm text-blueprint outline-none focus:border-blueprint"
        />
        {hasChart ? (
          <button
            type="button"
            onClick={onOpenInsights}
            className="hidden rounded-none border border-dashed border-blueprint px-3 py-2 font-mono text-xs text-blueprint transition-colors hover:bg-blueprint hover:text-white lg:block"
          >
            INSIGHTS ↗
          </button>
        ) : null}
        <button
          type="submit"
          disabled={isLoading}
          className="border border-dashed border-blueprint px-3 py-2 font-mono text-xs text-blueprint transition-colors hover:bg-blueprint hover:text-white disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isLoading ? "..." : "SEND"}
        </button>
      </form>
    </section>
  );
}
