"use client";

import {
  Bar,
  BarChart,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartPayload } from "@/types/app-types";

const PALETTE = ["#1d4ed8", "#3b82f6", "#93c5fd", "#bfdbfe", "#dbeafe"];

interface Props {
  payload: ChartPayload;
}

export default function InsightsChart({ payload }: Props) {
  const { type: chartType, data, xKey, yKeys, title } = payload;

  return (
    <div className="w-full">
      {title ? (
        <p className="mb-2 font-mono text-xs text-blueprint/60">{title}</p>
      ) : null}
      <ResponsiveContainer height={260} width="100%">
        {chartType === "bar" ? (
          <BarChart data={data}>
            <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend />
            {yKeys.map((k, i) => (
              <Bar dataKey={k} fill={PALETTE[i % PALETTE.length]} key={k} />
            ))}
          </BarChart>
        ) : chartType === "line" ? (
          <LineChart data={data}>
            <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend />
            {yKeys.map((k, i) => (
              <Line
                dataKey={k}
                key={k}
                stroke={PALETTE[i % PALETTE.length]}
                type="monotone"
              />
            ))}
          </LineChart>
        ) : (
          <PieChart>
            <Pie data={data} dataKey={yKeys[0] ?? "value"} nameKey={xKey}>
              {data.map((_, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: static pie slices from chart data
                <Cell fill={PALETTE[i % PALETTE.length]} key={i} />
              ))}
            </Pie>
            <Tooltip />
            <Legend />
          </PieChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
