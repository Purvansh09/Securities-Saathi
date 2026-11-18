import { useState } from "react";
import { Link } from "react-router-dom";

import { AllocationChart } from "../components/AllocationChart";
import { formatDate, formatINR, formatPct, signClass, TYPE_LABELS } from "../lib/format";
import { useAllocation, useSummary, useXirr } from "../lib/queries";
import type { AllocationBy, XirrResult } from "../lib/types";

/** The caveat to show under an XIRR figure, if any. */
export function xirrNote(r: XirrResult): string {
  if (r.xirr_pct === null) return r.days === 0 ? "Needs at least a day of history" : "Not defined for these cash flows";
  if (r.short_period) return `Annualised from ${r.days} day${r.days === 1 ? "" : "s"}, read with care`;
  return `Annualised over ${(r.days / 365).toFixed(1)} years`;
}

export function DashboardPage() {
  const summary = useSummary();
  const xirr = useXirr();
  const [by, setBy] = useState<AllocationBy>("asset_class");
  const allocation = useAllocation(by);
  const byClass = useAllocation("asset_class");

  if (summary.isPending) return <p className="muted">Loading your portfolio…</p>;
  if (summary.isError) return <p className="alert alert-error">Couldn't load the portfolio: {summary.error.message}</p>;

  const s = summary.data;

  if (s.holdings_count === 0 && Number(s.realized_pnl) === 0) {
    return (
      <div className="empty">
        <h1>Your portfolio is empty</h1>
        <p className="muted">Record a buy to start. Holdings, P/L and allocation are all worked out from your transactions.</p>
        <Link to="/transactions" className="btn btn-primary">
          Add your first transaction
        </Link>
      </div>
    );
  }

  return (
    <div className="stack-lg">
      <div className="page-head">
        <h1>Dashboard</h1>
        <span className="muted small">Valued as of {formatDate(s.as_of)}</span>
      </div>

      <section className="stats">
        <div className="stat stat-hero">
          <div className="stat-label">Current value</div>
          <div className="stat-value">{formatINR(s.current_value)}</div>
          <div className="stat-sub muted">
            Invested {formatINR(s.invested)} · <Link to="/holdings">{s.holdings_count} holdings</Link>
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Unrealised P/L</div>
          <div className={`stat-value ${signClass(s.unrealized_pnl)}`}>{formatINR(s.unrealized_pnl)}</div>
          <div className={`stat-sub ${signClass(s.unrealized_pnl_pct)}`}>{formatPct(s.unrealized_pnl_pct)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Realised P/L</div>
          <div className={`stat-value ${signClass(s.realized_pnl)}`}>{formatINR(s.realized_pnl)}</div>
          <div className="stat-sub muted">From completed sells, FIFO</div>
        </div>
        <div className="stat">
          <div className="stat-label">
            XIRR <span className="muted" title="Annualised return that accounts for when each rupee went in and came out">ⓘ</span>
          </div>
          <div className={`stat-value ${signClass(xirr.data?.portfolio.xirr_pct)}`}>
            {xirr.isPending ? "…" : formatPct(xirr.data?.portfolio.xirr_pct)}
          </div>
          <div className="stat-sub muted">
            {xirr.data && xirrNote(xirr.data.portfolio)}
          </div>
        </div>
      </section>

      {(s.unpriced_holdings > 0 || s.estimated_holdings > 0) && (
        <p className="alert alert-info">
          {s.unpriced_holdings > 0 &&
            `${s.unpriced_holdings} holding${s.unpriced_holdings > 1 ? "s have" : " has"} no price yet and ${s.unpriced_holdings > 1 ? "are" : "is"} counted at cost. `}
          {s.estimated_holdings > 0 &&
            `${s.estimated_holdings} bond${s.estimated_holdings > 1 ? "s are" : " is"} valued at face value plus accrued interest (estimated).`}
        </p>
      )}

      <div className="grid-dash">
        <section className="card">
          <div className="card-head">
            <h2>Allocation</h2>
            <div className="segmented segmented-sm" role="radiogroup" aria-label="Group allocation by">
              {(["asset_class", "sector"] as const).map((k) => (
                <button key={k} type="button" role="radio" aria-checked={by === k} className={by === k ? "on" : undefined} onClick={() => setBy(k)}>
                  {k === "asset_class" ? "Asset class" : "Sector"}
                </button>
              ))}
            </div>
          </div>
          {allocation.isPending && <p className="muted">Loading…</p>}
          {allocation.isError && <p className="alert alert-error">{allocation.error.message}</p>}
          {allocation.data && allocation.data.buckets.length > 0 && <AllocationChart data={allocation.data} />}
        </section>

        <section className="card">
          <h2>Totals by asset class</h2>
          {byClass.data && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Asset class</th>
                    <th className="num">Holdings</th>
                    <th className="num">Value</th>
                    <th className="num">Weight</th>
                  </tr>
                </thead>
                <tbody>
                  {byClass.data.buckets.map((b) => (
                    <tr key={b.key}>
                      <td>
                        <Link to={`/holdings?type=${b.key}`}>{TYPE_LABELS[b.key] ?? b.key}</Link>
                      </td>
                      <td className="num">{b.holdings_count}</td>
                      <td className="num">{formatINR(b.value)}</td>
                      <td className="num">{formatPct(b.weight_pct, { signed: false })}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td>Total</td>
                    <td className="num">{s.holdings_count}</td>
                    <td className="num">{formatINR(byClass.data.total_value)}</td>
                    <td className="num">100.00%</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
