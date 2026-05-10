"use client";

import { formatDistanceToNow } from "date-fns";
import type { HistoryItem } from "@/types/app-types";

interface Props {
  clearHistory: () => void;
  history: HistoryItem[];
  onSelect: (query: string) => void;
}

export default function QueryHistory({
  history,
  clearHistory,
  onSelect,
}: Props) {
  if (history.length === 0) {
    return null;
  }

  return (
    <div className="border-t border-dashed border-blueprint/20 pt-2">
      <div className="flex items-center justify-between px-2 pb-1">
        <span className="font-mono text-xs uppercase tracking-widest text-blueprint/40">
          History
        </span>
        <button
          className="font-mono text-xs text-blueprint/30 transition-colors hover:text-blueprint"
          onClick={clearHistory}
          type="button"
        >
          CLEAR
        </button>
      </div>
      <ul className="max-h-64 overflow-y-auto space-y-0.5">
        {history.map((item) => (
          <li key={item.id}>
            <button
              className="w-full border border-dashed border-transparent px-2 py-1.5 text-left transition-colors hover:border-blueprint/20 hover:bg-blueprint/5"
              onClick={() => onSelect(item.query)}
              type="button"
            >
              <p className="truncate font-mono text-xs text-blueprint">
                {item.query}
              </p>
              {item.answerPreview ? (
                <p className="truncate font-mono text-xs text-blueprint/40">
                  {item.answerPreview}
                </p>
              ) : null}
              <div className="mt-0.5 flex flex-wrap items-center gap-1">
                {item.toolsUsed.map((t) => (
                  <span
                    className="bg-blueprint/10 px-1 font-mono text-xs text-blueprint/50"
                    key={t}
                  >
                    {t}
                  </span>
                ))}
                <span className="ml-auto font-mono text-xs text-blueprint/25">
                  {formatDistanceToNow(item.timestamp, { addSuffix: true })}
                </span>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
