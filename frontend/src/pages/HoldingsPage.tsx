import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { DownloadButtons } from "../components/DownloadButtons";
import { formatDate, formatINR, formatPct, formatQty, signClass, TYPE_LABELS } from "../lib/format";
import { useHoldings, useXirr } from "../lib/queries";
import type { SecurityType } from "../lib/types";

const FILTERS: (SecurityType | undefined)[] = [undefined, "STOCK", "ETF", "MF", "BOND"];

export function HoldingsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const raw = params.get("type");
  const type = FILTERS.includes(raw as SecurityType) ? (raw as SecurityType) : undefined;
  const holdings = useHoldings(type);
  const xirr = useXirr();
  const xirrOf = (isin: string) => xirr.data?.holdings.find((h) => h.isin === isin);

  return (
    <div className="stack-lg">
      <div className="page-head">
        <h1>Holdings</h1>
        <div className="head-actions">
          <DownloadButtons path="/reports/holdings" filename="holdings-statement" label="Statement" />
          <Link to="/transactions" className="btn btn-primary btn-sm">
            Add transaction
          </Link>
        </div>
      </div>

      <div className="tabs" role="tablist">
        {FILTERS.map((f) => (
          <button
            key={f ?? "ALL"}
            role="tab"
            aria-selected={type === f}
            className={type === f ? "on" : undefined}
            onClick={() => setParams(f ? { type: f } : {})}
          >
            {f ? TYPE_LABELS[f] : "All"}
          </button>
        ))}
      </div>

      {holdings.isPending && <p className="muted">Loading…</p>}
      {holdings.isError && <p className="alert alert-error">{holdings.error.message}</p>}
      {holdings.data?.length === 0 && (
        <p className="muted">No {type ? TYPE_LABELS[type].toLowerCase() : "holdings"} right now.</p>
      )}

      {holdings.data && holdings.data.length > 0 && (
        <div className="card flush">
          <div className="table-wrap">
            <table className="holdings">
              <thead>
                <tr>
                  <th>Security</th>
                  <th className="num">Qty</th>
                  <th className="num">Avg cost</th>
                  <th className="num">Invested</th>
                  <th className="num">Price</th>
                  <th className="num">Value</th>
                  <th className="num">Unrealised P/L</th>
                  <th className="num" title="Annualised return (XIRR)">XIRR</th>
                  <th className="num">Weight</th>
                </tr>
              </thead>
              <tbody>
                {holdings.data.map((h) => (
                  <tr key={h.isin} className="clickable" onClick={() => navigate(`/holdings/${h.isin}`)}>
                    <td>
                      <Link to={`/holdings/${h.isin}`} className="sec-name" onClick={(e) => e.stopPropagation()}>
                        {h.symbol ?? h.name}
                      </Link>
                      <div className="muted small ellipsis">
                        {h.symbol ? h.name : h.isin} · {TYPE_LABELS[h.type]}
                      </div>
                    </td>
                    <td className="num">{formatQty(h.quantity)}</td>
                    <td className="num">{formatINR(h.avg_cost)}</td>
                    <td className="num">{formatINR(h.invested)}</td>
                    <td className="num">
                      {h.price ? (
                        <>
                          {formatINR(h.price)}
                          {h.price_estimated && <span className="badge" title="Face value + accrued interest">est.</span>}
                          {h.price_date && <div className="muted small">{formatDate(h.price_date)}</div>}
                        </>
                      ) : (
                        <span className="badge badge-warn" title="No price yet, counted at cost">no price</span>
                      )}
                    </td>
                    <td className="num">{formatINR(h.market_value ?? h.invested)}</td>
                    <td className={`num ${signClass(h.unrealized_pnl)}`}>
                      {formatINR(h.unrealized_pnl)}
                      <div className="small">{formatPct(h.unrealized_pnl_pct)}</div>
                    </td>
                    {(() => {
                      const x = xirrOf(h.isin);
                      return (
                        <td
                          className={`num ${signClass(x?.xirr_pct)}`}
                          title={x?.short_period ? `Annualised from ${x.days} days; short periods exaggerate` : undefined}
                        >
                          {x ? formatPct(x.xirr_pct) : "…"}
                          {x?.short_period && x.xirr_pct !== null && <span className="badge">&lt;1y</span>}
                        </td>
                      );
                    })()}
                    <td className="num">{formatPct(h.weight_pct, { signed: false })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
