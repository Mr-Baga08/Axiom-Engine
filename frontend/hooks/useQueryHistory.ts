"use client";

import { useCallback, useEffect, useState } from "react";
import type { HistoryItem } from "@/types/app-types";

const STORAGE_KEY = "queryHistory";
const MAX_ITEMS = 20;

export function useQueryHistory() {
  const [history, setHistory] = useState<HistoryItem[]>([]);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        setHistory(JSON.parse(stored) as HistoryItem[]);
      }
    } catch {
      // localStorage unavailable or corrupted — start empty
    }
  }, []);

  const addHistoryItem = useCallback((item: HistoryItem) => {
    setHistory((prev) => {
      const next = [item, ...prev.filter((h) => h.id !== item.id)].slice(
        0,
        MAX_ITEMS
      );
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        // storage quota exceeded or unavailable
      }
      return next;
    });
  }, []);

  const clearHistory = useCallback(() => {
    setHistory([]);
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // storage unavailable
    }
  }, []);

  return { history, addHistoryItem, clearHistory };
}
