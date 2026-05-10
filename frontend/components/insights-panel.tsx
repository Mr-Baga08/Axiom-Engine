"use client";

import dynamic from "next/dynamic";
import { useRef, useState } from "react";
import QueryHistory from "@/components/query-history";
import type { HistoryItem } from "@/types/app-types";

const RoughChart = dynamic(() => import("./rough-chart"), { ssr: false });

type ActiveChart = {
  chart_type: string;
  dataset: { entity: string; value: number }[];
  roughness?: number;
};

interface Props {
  chart: ActiveChart | null;
  clearHistory: () => void;
  history: HistoryItem[];
  onHistorySelect: (query: string) => void;
  onToggle: () => void;
  open: boolean;
}

type UploadState = "idle" | "uploading" | "success" | "error";

export default function InsightsPanel({
  chart,
  open,
  onToggle,
  history,
  clearHistory,
  onHistorySelect,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploadState, setUploadState] = useState<UploadState>("idle");
  const [uploadMsg, setUploadMsg] = useState("");

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) {
      return;
    }

    setUploadState("uploading");
    setUploadMsg(`Uploading ${file.name}…`);

    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch("/api/ingest/pdf", {
        method: "POST",
        body: form,
      });
      if (res.ok) {
        const data = (await res.json()) as {
          chunks_indexed: number;
          filename: string;
        };
        setUploadState("success");
        setUploadMsg(
          `✓ ${data.filename} — ${data.chunks_indexed} chunks indexed`
        );
      } else if (res.status === 401 || res.status === 403) {
        setUploadState("error");
        setUploadMsg("Executive role required to upload documents");
      } else {
        const err = (await res
          .json()
          .catch(() => ({ error: "Unknown error" }))) as { error: string };
        setUploadState("error");
        setUploadMsg(`Upload failed: ${err.error}`);
      }
    } catch {
      setUploadState("error");
      setUploadMsg("Network error — could not reach backend");
    } finally {
      // Reset file input so the same file can be re-uploaded
      if (fileRef.current) {
        fileRef.current.value = "";
      }
      setTimeout(() => {
        setUploadState("idle");
        setUploadMsg("");
      }, 5000);
    }
  }

  if (!open) {
    return null;
  }

  return (
    <aside className="fixed inset-0 z-40 flex w-full flex-col border-l border-dashed border-[var(--blueprint-border)] bg-white/90 backdrop-blur-sm lg:static lg:inset-auto lg:z-auto lg:w-96 lg:bg-white lg:backdrop-blur-none">
      {/* Header */}
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
        {/* Chart area */}
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

        {/* Document upload */}
        <div className="border-t border-dashed border-[var(--blueprint-border)] px-4 py-3">
          <p className="mb-2 font-mono text-xs uppercase tracking-widest text-blueprint/60">
            Add Document
          </p>
          <input
            accept=".pdf"
            className="hidden"
            onChange={handleFileChange}
            ref={fileRef}
            type="file"
          />
          <button
            className="w-full border border-dashed border-blueprint/40 px-3 py-2 font-mono text-xs text-blueprint/60 transition-colors hover:border-blueprint hover:text-blueprint disabled:cursor-not-allowed disabled:opacity-40"
            disabled={uploadState === "uploading"}
            onClick={() => fileRef.current?.click()}
            type="button"
          >
            {uploadState === "uploading" ? "UPLOADING…" : "UPLOAD PDF ↑"}
          </button>
          {uploadMsg && (
            <p
              className={[
                "mt-2 font-mono text-xs",
                uploadState === "success" ? "text-green-600" : "text-red-500",
              ].join(" ")}
            >
              {uploadMsg}
            </p>
          )}
        </div>

        {/* Query history */}
        <QueryHistory
          clearHistory={clearHistory}
          history={history}
          onSelect={onHistorySelect}
        />
      </div>
    </aside>
  );
}
