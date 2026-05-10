export interface SSEToolCallEvent {
  args: Record<string, unknown>;
  id: string;
  latency_ms: number;
  name: string;
  result_preview: string;
}

export interface ChartPayload {
  data: Record<string, unknown>[];
  title?: string;
  // "type" matches the field name in backend agent.py generate_chart spec
  type: "bar" | "line" | "pie" | "scatter";
  xKey: string;
  yKeys: string[];
}

export interface FilterState {
  genre: string;
  year: string;
}

export interface HistoryItem {
  answerPreview: string;
  id: string;
  query: string;
  timestamp: number;
  toolsUsed: string[];
  traceId?: string;
}
