"use client";

import dynamic from "next/dynamic";
import QueryHistory from "@/components/QueryHistory";
import type { HistoryItem } from "@/types/app-types";

const RoughChart = dynamic(() => import("./rough-chart"), { ssr: false });

type ActiveChart = {
  chart_type: string;
  dataset: { entity: string; value: number }[];
  roughness?: number;
};

interface Props {
  chart: ActiveChart | null;
  onToggle: () => void;
  open: boolean;
  history: HistoryItem[];
  clearHistory: () => void;
  onHistorySelect: (query: string) => void;
}

export default function InsightsPanel({
  chart,
  open,
  onToggle,
  history,
  clearHistory,
  onHistorySelect,
}: Props) {
  if (!open) {
    return null;
  }

  return (
    <aside className="fixed inset-0 z-40 flex w-full flex-col border-l border-dashed border-[var(--blueprint-border)] bg-white/90 backdrop-blur-sm lg:static lg:inset-auto lg:z-auto lg:w-96 lg:bg-white lg:backdrop-blur-none">
      <div className="flex items-center justify-between border-b border-dashed border-[var(--blueprint-border)] px-4 py-3">
        <span className="font-mono text-xs uppercase tracking-widest text-blueprint/60">
          Insights Draft
        </span>
        <button
          aria-label="Close insights panel"
          className="rounded-none border border-transparent px-2 py-1 font-mono text-xs text-blueprint/50 transition-colors hover:border-blueprint/30 hover:text-blueprint"
          onClick={onToggle}
          type="button"
        >
          ✕ CLOSE
        </button>
      </div>

      <div className="flex flex-1 flex-col overflow-y-auto">
        <div className="p-4">
          {chart ? (
            <RoughChart
              chartType={chart.chart_type}
              dataset={chart.dataset}
              roughness={chart.roughness ?? 2}
            />
          ) : (
            <div className="flex h-32 items-center justify-center">
              <p className="text-center font-mono text-xs leading-relaxed text-blueprint/30">
                NO CHART DATA
                <br />
                <span className="text-blueprint/20">
                  awaiting render_chart signal
                </span>
              </p>
            </div>
          )}
        </div>
        <QueryHistory
          clearHistory={clearHistory}
          history={history}
          onSelect={onHistorySelect}
        />
      </div>
    </aside>
  );
}
