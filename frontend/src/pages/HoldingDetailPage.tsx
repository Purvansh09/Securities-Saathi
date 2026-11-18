import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { api } from "../lib/api";
import { formatDate, formatDateTimeIST, formatINR, formatPct, formatQty, signClass, TYPE_LABELS } from "../lib/format";
import { keys } from "../lib/queries";

export function HoldingDetailPage() {
  const { isin = "" } = useParams();
  const detail = useQuery({ queryKey: keys.holding(isin), queryFn: () => api.holding(isin) });

  if (detail.isPending) return <p className="muted">Loading…</p>;
  if (detail.isError) return <p className="alert alert-error">{detail.error.message}</p>;
  const h = detail.data;
  const closed = Number(h.quantity) === 0;

  return (
    <div className="stack-lg">
      <div>
        <Link to="/holdings" className="muted small">
          ← Holdings
        </Link>
        <h1>{h.name}</h1>
        <p className="muted">{[h.symbol, TYPE_LABELS[h.type], h.sector, h.isin].filter(Boolean).join(" · ")}</p>
      </div>

      <section className="stats">
        <div className="stat">
          <div className="stat-label">Quantity</div>
          <div className="stat-value">{formatQty(h.quantity)}</div>
          <div className="stat-sub muted">{closed ? "Position closed" : `Avg cost ${formatINR(h.avg_cost)}`}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Value</div>
          <div className="stat-value">{formatINR(h.market_value ?? h.invested)}</div>
          <div className="stat-sub muted">
            {h.price
              ? `${formatINR(h.price)}${h.price_estimated ? " (est.)" : ""}${h.price_date ? ` on ${formatDate(h.price_date)}` : ""}`
              : "No price yet, shown at cost"}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Unrealised P/L</div>
          <div className={`stat-value ${signClass(h.unrealized_pnl)}`}>{formatINR(h.unrealized_pnl)}</div>
          <div className={`stat-sub ${signClass(h.unrealized_pnl_pct)}`}>{formatPct(h.unrealized_pnl_pct)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Realised P/L</div>
          <div className={`stat-value ${signClass(h.realized_pnl)}`}>{formatINR(h.realized_pnl)}</div>
          <div className="stat-sub muted">{h.realized.length} matched lot{h.realized.length === 1 ? "" : "s"}</div>
        </div>
      </section>

      <section className="card flush">
        <h2 className="card-pad">Open lots</h2>
        <p className="muted small card-pad">The units you still hold, oldest first. The next sell is matched against the top one (FIFO).</p>
        {h.open_lots.length === 0 ? (
          <p className="muted card-pad">None.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Bought</th>
                  <th className="num">Qty</th>
                  <th className="num">Unit cost</th>
                  <th className="num">Cost</th>
                </tr>
              </thead>
              <tbody>
                {h.open_lots.map((l) => (
                  <tr key={l.buy_transaction_id}>
                    <td>{formatDateTimeIST(l.acquired_at)}</td>
                    <td className="num">{formatQty(l.quantity)}</td>
                    <td className="num">{formatINR(l.unit_cost)}</td>
                    <td className="num">{formatINR(l.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card flush">
        <h2 className="card-pad">Realised (FIFO matches)</h2>
        {h.realized.length === 0 ? (
          <p className="muted card-pad">No sells yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Bought</th>
                  <th>Sold</th>
                  <th className="num">Held</th>
                  <th className="num">Qty</th>
                  <th className="num">Cost</th>
                  <th className="num">Proceeds</th>
                  <th className="num">Gain</th>
                </tr>
              </thead>
              <tbody>
                {h.realized.map((r, i) => (
                  <tr key={`${r.buy_transaction_id}-${r.sell_transaction_id}-${i}`}>
                    <td>{formatDateTimeIST(r.acquired_at)}</td>
                    <td>{formatDateTimeIST(r.sold_at)}</td>
                    <td className="num">{r.holding_days} d</td>
                    <td className="num">{formatQty(r.quantity)}</td>
                    <td className="num">{formatINR(r.cost)}</td>
                    <td className="num">{formatINR(r.proceeds)}</td>
                    <td className={`num ${signClass(r.gain)}`}>{formatINR(r.gain)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
