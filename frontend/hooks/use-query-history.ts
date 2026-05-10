"use client";

// Conversation history hook — backend-persisted via Redis, falls back to
// localStorage so the UI always works even when the API is unavailable.
//
// Strategy:
//   - On mount: fetch /history from the backend, merge with localStorage
//   - addHistoryItem: update local state immediately; backend pushes
//     automatically when the agent responds (via routers/history.py)
//   - clearHistory: DELETE /history on backend + clear localStorage

import { useCallback, useEffect, useState } from "react";
import type { HistoryItem } from "@/types/app-types";

const STORAGE_KEY = "queryHistory";
const MAX_ITEMS   = 50;

async function fetchBackendHistory(): Promise<HistoryItem[]> {
  try {
    const res = await fetch("/api/history", { credentials: "include" });
    if (!res.ok) return [];
    const data = (await res.json()) as { history: Array<{
      query: string;
      answer_preview: string;
      tools_used: string[];
      timestamp: number;
    }> };
    return (data.history ?? []).map((e, i) => ({
      id:            `backend-${e.timestamp}-${i}`,
      query:         e.query,
      answerPreview: e.answer_preview,
      toolsUsed:     e.tools_used,
      timestamp:     e.timestamp,
    }));
  } catch {
    return [];
  }
}

async function deleteBackendHistory(): Promise<void> {
  try {
    await fetch("/api/history", { method: "DELETE", credentials: "include" });
  } catch {
    // Redis unavailable — local clear still proceeds
  }
}

export function useQueryHistory() {
  const [history, setHistory] = useState<HistoryItem[]>([]);

  const loadHistory = useCallback(() => {
    let local: HistoryItem[] = [];
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) local = JSON.parse(stored) as HistoryItem[];
    } catch {
      // localStorage unavailable
    }

    fetchBackendHistory().then((backend) => {
      const seen = new Set<string>();
      const merged: HistoryItem[] = [];
      for (const item of [...backend, ...local]) {
        const key = `${item.query}:${item.timestamp}`;
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(item);
        }
      }
      const sorted = merged
        .sort((a, b) => b.timestamp - a.timestamp)
        .slice(0, MAX_ITEMS);
      setHistory(sorted);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(sorted));
      } catch {
        // quota exceeded
      }
    });
  }, []);

  // Initial load
  useEffect(() => { loadHistory(); }, [loadHistory]);

  // Re-fetch when the tab becomes visible — catches the case where the user
  // logged in on another page and navigated back here with the cookie already set.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") loadHistory();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [loadHistory]);

  const addHistoryItem = useCallback((item: HistoryItem) => {
    setHistory((prev) => {
      const next = [item, ...prev.filter((h) => h.id !== item.id)].slice(0, MAX_ITEMS);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        // quota exceeded
      }
      return next;
    });
  }, []);

  const clearHistory = useCallback(() => {
    setHistory([]);
    try { localStorage.removeItem(STORAGE_KEY); } catch { /* unavailable */ }
    deleteBackendHistory();
  }, []);

  return { history, addHistoryItem, clearHistory, refetchHistory: loadHistory };
}
