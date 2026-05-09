export interface SSEToolCallEvent {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result_preview: string;
  latency_ms: number;
}

export interface ChartPayload {
  // "type" matches the field name in backend agent.py generate_chart spec
  type: "bar" | "line" | "pie" | "scatter";
  data: Record<string, unknown>[];
  xKey: string;
  yKeys: string[];
  title?: string;
}

export interface HistoryItem {
  id: string;
  query: string;
  answerPreview: string;
  toolsUsed: string[];
  timestamp: number;
  traceId?: string;
}
