"use client";

import type { SSEToolCallEvent } from "@/types/app-types";

interface Props {
  events: SSEToolCallEvent[];
}

export default function ToolTrace({ events }: Props) {
  if (events.length === 0) return null;

  return (
    <details className="max-w-3xl border border-dashed border-blueprint/30 p-2">
      <summary className="cursor-pointer font-mono text-xs text-blueprint/60">
        ▸ Tool trace ({events.length})
      </summary>
      <div className="mt-2 space-y-1">
        {events.map((ev) => (
          <details className="border-l-2 border-blueprint/20 pl-2" key={ev.id}>
            <summary className="cursor-pointer font-mono text-xs text-blueprint/50">
              {ev.name}
              {ev.latency_ms > 0 ? ` · ${ev.latency_ms}ms` : ""}
            </summary>
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs text-blueprint/40">
              {JSON.stringify(
                ev.result_preview
                  ? { args: ev.args, result: ev.result_preview }
                  : { args: ev.args },
                null,
                2
              )}
            </pre>
          </details>
        ))}
      </div>
    </details>
  );
}
