import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import { formatINR, formatPct, TYPE_LABELS } from "../lib/format";
import type { Allocation } from "../lib/types";

// Categorical palette, defined as CSS variables in index.css so light and dark themes each get
// a tuned set. Order is fixed so a slice keeps its colour when the data changes.
const COLORS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7"].map((v) => `var(${v})`);
const MAX_SLICES = 6; // beyond this a donut stops being readable; the rest is folded into "Other"

export function AllocationChart({ data }: { data: Allocation }) {
  const label = (key: string) => (data.by === "asset_class" ? (TYPE_LABELS[key] ?? key) : key);

  let rows = data.buckets.map((b) => ({ name: label(b.key), value: Number(b.value), raw: b }));
  if (rows.length > MAX_SLICES + 1) {
    const rest = rows.slice(MAX_SLICES);
    rows = [
      ...rows.slice(0, MAX_SLICES),
      {
        name: `Other (${rest.length})`,
        value: rest.reduce((s, r) => s + r.value, 0),
        raw: {
          key: "Other",
          value: String(rest.reduce((s, r) => s + r.value, 0)),
          weight_pct: String(rest.reduce((s, r) => s + Number(r.raw.weight_pct), 0)),
          holdings_count: rest.reduce((s, r) => s + r.raw.holdings_count, 0),
        },
      },
    ];
  }

  return (
    <div className="alloc">
      <div className="alloc-chart">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={rows}
              dataKey="value"
              nameKey="name"
              innerRadius="62%"
              outerRadius="100%"
              paddingAngle={rows.length > 1 ? 1.5 : 0}
              stroke="none"
              isAnimationActive={false}
            >
              {rows.map((r, i) => (
                <Cell key={r.name} fill={COLORS[i % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip
              formatter={(v) => formatINR(String(v))}
              contentStyle={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                color: "var(--text)",
              }}
              itemStyle={{ color: "var(--text)" }}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="legend">
        {rows.map((r, i) => (
          <li key={r.name}>
            <span className="swatch" style={{ background: COLORS[i % COLORS.length] }} />
            <span className="legend-name">{r.name}</span>
            <span className="num">{formatPct(r.raw.weight_pct, { signed: false })}</span>
            <span className="num muted">{formatINR(r.raw.value)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
