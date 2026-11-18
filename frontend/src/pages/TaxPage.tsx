import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { DownloadButtons } from "../components/DownloadButtons";
import { api } from "../lib/api";
import { formatDate, formatINR, formatQty, signClass } from "../lib/format";

export function TaxPage() {
  const [fy, setFy] = useState<string | undefined>(undefined);
  const report = useQuery({ queryKey: ["portfolio", "tax", fy ?? "latest"], queryFn: () => api.capitalGains(fy) });

  if (report.isPending) return <p className="muted">Working out your capital gains…</p>;
  if (report.isError) return <p className="alert alert-error">{report.error.message}</p>;
  const r = report.data;
  const s = r.summary;
  const years = r.years_available.includes(r.fy) ? r.years_available : [r.fy, ...r.years_available];
  const uncertain = r.lots.filter((l) => !l.classification_certain);
  const lotNotes = [...new Set(r.lots.flatMap((l) => l.notes))];

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>Capital gains tax</h1>
          <p className="muted small">
            Financial year {r.fy} · {formatDate(r.period_from)} to {formatDate(r.period_to)}
          </p>
        </div>
        <div className="head-actions">
          <label className="inline-select">
            <span className="muted small">Year</span>
            <select value={r.fy} onChange={(e) => setFy(e.target.value)}>
              {years.map((y) => (
                <option key={y} value={y}>
                  FY {y}
                </option>
              ))}
            </select>
          </label>
          <DownloadButtons path={`/reports/realised?fy=${r.fy}`} filename={`capital-gains-FY${r.fy}`} label="Statement" />
        </div>
      </div>

      {r.lots.length === 0 ? (
        <div className="card">
          <p>No sales in FY {r.fy}, so there are no capital gains to report.</p>
          <p className="muted small">
            Gains are only taxed when you sell. <Link to="/holdings">Your holdings</Link> show unrealised P/L.
          </p>
        </div>
      ) : (
        <>
          <section className="stats">
            <div className="stat stat-hero">
              <div className="stat-label">Estimated tax</div>
              <div className="stat-value">{formatINR(s.total_estimated_tax)}</div>
              <div className="stat-sub muted">incl. 4% cess ({formatINR(s.cess)}), before surcharge</div>
            </div>
            <div className="stat">
              <div className="stat-label">Equity LTCG exemption</div>
              <div className="stat-value">{formatINR(s.exemption_used)}</div>
              <div className="stat-sub muted">used of {formatINR(s.exemption_limit)} (s.112A)</div>
            </div>
            <div className="stat">
              <div className="stat-label">Taxed at your slab</div>
              <div className="stat-value">{formatINR(s.slab_income)}</div>
              <div className="stat-sub muted">added to your other income</div>
            </div>
            <div className="stat">
              <div className="stat-label">Losses to carry forward</div>
              <div className="stat-value">
                {formatINR(String(Number(s.short_term_loss_carried) + Number(s.long_term_loss_carried)))}
              </div>
              <div className="stat-sub muted">
                short {formatINR(s.short_term_loss_carried)} · long {formatINR(s.long_term_loss_carried)}
              </div>
            </div>
          </section>

          {uncertain.length > 0 && (
            <p className="alert alert-info">
              {uncertain.length} sale{uncertain.length > 1 ? "s are" : " is"} in a fund whose tax treatment depends on its
              portfolio. Check the classification against the fund's factsheet before relying on the estimate.
            </p>
          )}

          <section className="card flush">
            <h2 className="card-pad">How the tax is worked out</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Category</th>
                    <th className="num">Gains</th>
                    <th className="num">Losses</th>
                    <th className="num" title="Losses from other categories absorbed here">Set off</th>
                    <th className="num">Exemption</th>
                    <th className="num">Taxable</th>
                    <th className="num">Tax</th>
                  </tr>
                </thead>
                <tbody>
                  {r.buckets.map((b) => (
                    <tr key={b.label}>
                      <td>{b.label}</td>
                      <td className="num">{formatINR(b.gains)}</td>
                      <td className="num">{Number(b.losses) ? formatINR(b.losses) : "—"}</td>
                      <td className="num">{Number(b.set_off) ? `−${formatINR(b.set_off)}` : "—"}</td>
                      <td className="num">{Number(b.exemption) ? `−${formatINR(b.exemption)}` : "—"}</td>
                      <td className="num">{formatINR(b.taxable)}</td>
                      <td className="num">{b.tax === null ? <span className="muted">slab</span> : formatINR(b.tax)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td>Total before cess</td>
                    <td colSpan={5} />
                    <td className="num">{formatINR(s.special_rate_tax)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </section>

          <section className="card flush">
            <h2 className="card-pad">Every sale, matched to its purchase (FIFO)</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Security</th>
                    <th>Bought</th>
                    <th>Sold</th>
                    <th className="num">Held</th>
                    <th className="num">Qty</th>
                    <th className="num">Gain</th>
                    <th>Treatment</th>
                  </tr>
                </thead>
                <tbody>
                  {r.lots.map((l, i) => (
                    <tr key={`${l.isin}-${l.sold_on}-${i}`}>
                      <td>
                        <div className="sec-name">{l.symbol ?? l.name}</div>
                        <div className="muted small ellipsis" title={l.asset_basis}>
                          {l.asset_basis}
                          {!l.classification_certain && " ⚠"}
                        </div>
                      </td>
                      <td className="nowrap">{formatDate(l.acquired_on)}</td>
                      <td className="nowrap">{formatDate(l.sold_on)}</td>
                      <td className="num">{l.holding_days} d</td>
                      <td className="num">{formatQty(l.quantity)}</td>
                      <td className={`num ${signClass(l.gain)}`}>{formatINR(l.gain)}</td>
                      <td>
                        <span className={`pill ${l.term === "LONG" ? "term-long" : "term-short"}`}>
                          {l.term === "LONG" ? "Long term" : "Short term"}
                        </span>
                        <div className="small">
                          {l.rule} · {l.rate_pct === null ? "slab rate" : `${l.rate_pct}%`}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      <section className="muted small stack-tight">
        {[...s.notes, ...lotNotes].map((n) => (
          <p key={n}>{n}</p>
        ))}
        <p>
          Classified by fixed rules taken from the Income Tax Department's published guidance, last checked{" "}
          {formatDate(r.rules_verified_on)}:{" "}
          {r.sources.map((u, i) => (
            <span key={u}>
              <a href={u} target="_blank" rel="noreferrer">
                source {i + 1}
              </a>
              {i < r.sources.length - 1 ? ", " : ""}
            </span>
          ))}
          . This is an estimate to help you plan, not tax advice.
        </p>
      </section>
    </div>
  );
}
