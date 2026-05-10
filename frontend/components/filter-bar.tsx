"use client";

import type { FilterState } from "@/types/app-types";

const GENRES = [
  "All",
  "Action",
  "Drama",
  "Comedy",
  "Sci-Fi",
  "Thriller",
  "Romance",
];
const YEARS = ["All", "2023", "2024", "2025"];

interface Props {
  filters: FilterState;
  onChange: (filters: FilterState) => void;
}

export default function FilterBar({ filters, onChange }: Props) {
  const active = filters.genre !== "All" || filters.year !== "All";

  return (
    <div className="flex items-center gap-3 border-b border-dashed border-[var(--blueprint-border)] px-4 py-2">
      <span className="font-mono text-xs uppercase tracking-widest text-blueprint/40">
        FILTER
      </span>
      <select
        className="border border-dashed border-[var(--blueprint-border)] bg-white px-2 py-1 font-mono text-xs text-blueprint outline-none focus:border-blueprint"
        onChange={(e) => onChange({ ...filters, genre: e.target.value })}
        value={filters.genre}
      >
        {GENRES.map((g) => (
          <option key={g} value={g}>
            {g === "All" ? "Genre: All" : g}
          </option>
        ))}
      </select>
      <select
        className="border border-dashed border-[var(--blueprint-border)] bg-white px-2 py-1 font-mono text-xs text-blueprint outline-none focus:border-blueprint"
        onChange={(e) => onChange({ ...filters, year: e.target.value })}
        value={filters.year}
      >
        {YEARS.map((y) => (
          <option key={y} value={y}>
            {y === "All" ? "Year: All" : y}
          </option>
        ))}
      </select>
      {active ? (
        <button
          className="font-mono text-xs text-blueprint/40 transition-colors hover:text-blueprint"
          onClick={() => onChange({ genre: "All", year: "All" })}
          type="button"
        >
          CLEAR ×
        </button>
      ) : null}
    </div>
  );
}
