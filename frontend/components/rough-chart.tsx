"use client";

import { useMemo } from "react";
import {
  Area as RoughArea,
  Bar as RoughBar,
  Line as RoughLine,
} from "react-roughviz";

type Datum = {
  entity: string;
  value: number;
};

interface Props {
  chartType: string;
  dataset: Datum[];
  roughness: number;
}

export default function RoughChart({ chartType, dataset, roughness }: Props) {
  const labels = useMemo(() => dataset.map((d) => String(d.entity)), [dataset]);
  const values = useMemo(() => dataset.map((d) => d.value), [dataset]);

  const sharedProps = {
    data: { labels, values },
    roughness,
    axisRoughness: roughness,
    stroke: "var(--blueprint)",
    fillStyle: "zigzag-line",
    fillWeight: 1,
    fill: "rgba(29,78,216,0.2)",
    margin: { top: 20, right: 20, bottom: 40, left: 40 },
    height: 320,
  };

  if (chartType === "line") {
    return <RoughLine {...sharedProps} />;
  }

  if (chartType === "area") {
    return <RoughArea {...sharedProps} />;
  }

  return <RoughBar {...sharedProps} />;
}
