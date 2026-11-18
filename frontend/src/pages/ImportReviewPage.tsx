import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ConfirmButton } from "../components/ConfirmButton";
import { SecurityPicker } from "../components/SecurityPicker";
import { api } from "../lib/api";
import {
  formatDateTimeIST,
  formatINR,
  formatQty,
  isDecimalString,
  istLocalToUtcIso,
  nowIstLocal,
  utcToIstLocal,
} from "../lib/format";
import { keys } from "../lib/queries";
import type { ImportDetail, ImportRow, ImportRowStatus, ImportRowUpdate, SecuritySummary, TxnType } from "../lib/types";
import { IMPORT_STATUS_LABEL } from "./ImportsPage";

type Filter = "attention" | "ready" | "excluded" | "all";
const FILTERS: { key: Filter; label: string; statuses: ImportRowStatus[] }[] = [
  { key: "attention", label: "Needs attention", statuses: ["PENDING"] },
  { key: "ready", label: "Ready", statuses: ["CONFIRMED"] },
  { key: "excluded", label: "Excluded", statuses: ["REJECTED"] },
  { key: "all", label: "All", statuses: ["PENDING", "CONFIRMED", "REJECTED", "COMMITTED"] },
];
const WARNING = "Warning: ";

export function ImportReviewPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const detail = useQuery({ queryKey: ["imports", id], queryFn: () => api.importDetail(id) });
  const [filter, setFilter] = useState<Filter | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  // Patch one row in the cached detail instead of refetching the whole import.
  const replaceRow = (row: ImportRow) =>
    queryClient.setQueryData<ImportDetail>(["imports", id], (old) => {
      if (!old) return old;
      const rows = old.rows.map((r) => (r.id === row.id ? row : r));
      const counts: ImportDetail["counts"] = {};
      for (const r of rows) counts[r.status] = (counts[r.status] ?? 0) + 1;
      return { ...old, rows, counts };
    });

  const update = useMutation({
    mutationFn: ({ rowId, body }: { rowId: string; body: ImportRowUpdate }) => api.updateImportRow(id, rowId, body),
    onMutate: () => setActionError(null),
    onSuccess: (row) => {
      replaceRow(row);
      setEditing(null);
    },
    onError: (e) => setActionError(e.message),
  });

  const commit = useMutation({
    mutationFn: () => api.commitImport(id),
    onMutate: () => setActionError(null),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["imports"] });
      queryClient.invalidateQueries({ queryKey: keys.portfolio });
      queryClient.invalidateQueries({ queryKey: keys.transactions });
      setDone(`${res.transactions_created} transaction${res.transactions_created === 1 ? "" : "s"} added to your ledger.`);
    },
    onError: (e) => setActionError(e.message),
  });

  const discard = useMutation({
    mutationFn: () => api.discardImport(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["imports"] });
      navigate("/import");
    },
    onError: (e) => setActionError(e.message),
  });

  if (detail.isPending) return <p className="muted">Loading…</p>;
  if (detail.isError) return <p className="alert alert-error">{detail.error.message}</p>;
  const d = detail.data;
  const c = d.counts;
  const reviewing = d.status === "PARSED";
  const active = filter ?? (c.PENDING ? "attention" : "all");
  const shown = d.rows.filter((r) => FILTERS.find((f) => f.key === active)!.statuses.includes(r.status));

  return (
    <div className="stack-lg">
      <div>
        <Link to="/import" className="muted small">
          ← Imports
        </Link>
        <h1 className="ellipsis-wide">{d.original_filename}</h1>
        <p className="muted">
          {IMPORT_STATUS_LABEL[d.status] ?? d.status}
          {d.profile && ` · ${d.profile}`}
          {d.parser === "LLM" && " · columns identified by AI, check them carefully"}
          {` · uploaded ${formatDateTimeIST(d.created_at)}`}
        </p>
      </div>

      {d.status === "FAILED" && (
        <div className="stack">
          <p className="alert alert-error">{d.error}</p>
          <div>
            <button className="btn btn-ghost" onClick={() => discard.mutate()} disabled={discard.isPending}>
              Remove from list
            </button>
          </div>
        </div>
      )}

      {done && (
        <p className="alert alert-ok">
          {done} <Link to="/holdings">View holdings →</Link>
        </p>
      )}
      {actionError && <p className="alert alert-error">{actionError}</p>}

      {d.status !== "FAILED" && (
        <>
          <div className="tabs" role="tablist">
            {FILTERS.map((f) => {
              const n = f.statuses.reduce((s, st) => s + (c[st] ?? 0), 0);
              return (
                <button key={f.key} role="tab" aria-selected={active === f.key} className={active === f.key ? "on" : undefined} onClick={() => setFilter(f.key)}>
                  {f.label} <span className="muted">{n}</span>
                </button>
              );
            })}
          </div>

          <section className="card flush">
            {shown.length === 0 ? (
              <p className="muted card-pad">Nothing here.</p>
            ) : (
              <div className="table-wrap">
                <table className="review">
                  <thead>
                    <tr>
                      <th className="num">Row</th>
                      <th>Security</th>
                      <th>Side</th>
                      <th className="num">Qty</th>
                      <th className="num">Price</th>
                      <th className="num">Charges</th>
                      <th>Date (IST)</th>
                      <th>Check</th>
                      {reviewing && <th aria-label="Actions" />}
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r) =>
                      editing === r.id ? (
                        <RowEditor
                          key={r.id}
                          row={r}
                          saving={update.isPending}
                          onCancel={() => setEditing(null)}
                          onSave={(body) => update.mutate({ rowId: r.id, body })}
                        />
                      ) : (
                        <RowView
                          key={r.id}
                          row={r}
                          reviewing={reviewing}
                          busy={update.isPending && update.variables?.rowId === r.id}
                          onEdit={() => setEditing(r.id)}
                          onStatus={(status) => update.mutate({ rowId: r.id, body: { status } })}
                        />
                      ),
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {reviewing && (
            <div className="commit-bar">
              <div>
                <strong>{c.CONFIRMED ?? 0} ready to import</strong>
                {c.PENDING ? <span className="muted"> · {c.PENDING} still need attention (they'll be skipped)</span> : null}
              </div>
              <div className="commit-actions">
                <ConfirmButton
                  question="Discard this import? Nothing has been added yet."
                  confirmLabel="Discard"
                  danger
                  disabled={discard.isPending || commit.isPending}
                  onConfirm={() => discard.mutate()}
                >
                  Discard
                </ConfirmButton>
                <ConfirmButton
                  className="btn btn-primary"
                  question={`Add ${c.CONFIRMED} transaction${c.CONFIRMED === 1 ? "" : "s"} to your ledger?`}
                  confirmLabel="Add to ledger"
                  disabled={!c.CONFIRMED || commit.isPending}
                  onConfirm={() => commit.mutate()}
                >
                  {commit.isPending ? "Importing…" : `Import ${c.CONFIRMED ?? 0}`}
                </ConfirmButton>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function RowView({
  row: r,
  reviewing,
  busy,
  onEdit,
  onStatus,
}: {
  row: ImportRow;
  reviewing: boolean;
  busy: boolean;
  onEdit: () => void;
  onStatus: (s: "CONFIRMED" | "REJECTED") => void;
}) {
  const blocking = r.issues.filter((i) => !i.startsWith(WARNING));
  const warnings = r.issues.filter((i) => i.startsWith(WARNING)).map((i) => i.slice(WARNING.length));
  const original = Object.entries(r.raw)
    .filter(([, v]) => v)
    .map(([k, v]) => `${k}: ${v}`)
    .join("\n");

  return (
    <tr className={`row-${r.status.toLowerCase()}`}>
      <td className="num muted" title={original}>
        {r.row_number}
      </td>
      <td>
        {r.security_name ? (
          <>
            <div className="sec-name">{r.security_symbol ?? r.security_name}</div>
            <div className="muted small ellipsis">{r.security_symbol ? r.security_name : r.isin}</div>
          </>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td>{r.txn_type ? <span className={`pill ${r.txn_type.toLowerCase()}`}>{r.txn_type}</span> : <span className="muted">—</span>}</td>
      <td className="num">{r.quantity ? formatQty(r.quantity) : "—"}</td>
      <td className="num">{formatINR(r.price)}</td>
      <td className="num">{formatINR(r.charges)}</td>
      <td className="nowrap">{r.executed_at ? formatDateTimeIST(r.executed_at) : "—"}</td>
      <td className="issues">
        {r.status === "COMMITTED" && <span className="pos small">Imported</span>}
        {r.status !== "COMMITTED" && blocking.length === 0 && warnings.length === 0 && r.status !== "REJECTED" && (
          <span className="pos small">OK</span>
        )}
        {(r.status === "REJECTED" ? r.issues : blocking).map((i) => (
          <div key={i} className={r.status === "REJECTED" ? "muted small" : "neg small"}>
            {i}
          </div>
        ))}
        {r.status !== "REJECTED" &&
          warnings.map((w) => (
            <div key={w} className="warn small">
              ⚠ {w}
            </div>
          ))}
      </td>
      {reviewing && (
        <td className="num nowrap">
          {r.status !== "COMMITTED" && (
            <>
              <button className="btn btn-ghost btn-sm" onClick={onEdit} disabled={busy}>
                Edit
              </button>{" "}
              {r.status === "REJECTED" ? (
                <button className="btn btn-ghost btn-sm" onClick={() => onStatus("CONFIRMED")} disabled={busy}>
                  Include
                </button>
              ) : r.status === "PENDING" && blocking.length === 0 ? (
                <button className="btn btn-primary btn-sm" onClick={() => onStatus("CONFIRMED")} disabled={busy}>
                  Confirm
                </button>
              ) : (
                <button className="btn btn-ghost btn-sm" onClick={() => onStatus("REJECTED")} disabled={busy}>
                  Exclude
                </button>
              )}
            </>
          )}
        </td>
      )}
    </tr>
  );
}

function RowEditor({
  row,
  saving,
  onCancel,
  onSave,
}: {
  row: ImportRow;
  saving: boolean;
  onCancel: () => void;
  onSave: (body: ImportRowUpdate) => void;
}) {
  const [security, setSecurity] = useState<SecuritySummary | null>(
    row.isin && row.security_name
      ? { isin: row.isin, name: row.security_name, symbol: row.security_symbol, type: "STOCK", exchange: null, sector: null, is_custom: false }
      : null,
  );
  const [side, setSide] = useState<TxnType | "">(row.txn_type ?? "");
  const [qty, setQty] = useState(row.quantity ? row.quantity.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") : "");
  const [price, setPrice] = useState(row.price ? row.price.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") : "");
  const [charges, setCharges] = useState(row.charges ? row.charges.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") : "0");
  const [when, setWhen] = useState(row.executed_at ? utcToIstLocal(row.executed_at) : "");
  const [err, setErr] = useState<string | null>(null);

  const save = () => {
    const problem = !security
      ? "Choose the security."
      : !side
        ? "Choose buy or sell."
        : !isDecimalString(qty) || Number(qty) === 0
          ? "Quantity must be a number above 0."
          : !isDecimalString(price)
            ? "Price must be a number."
            : !isDecimalString(charges || "0")
              ? "Charges must be a number."
              : !when
                ? "Enter the date and time."
                : null;
    setErr(problem);
    if (problem || !security || !side) return;
    onSave({
      isin: security.isin,
      txn_type: side,
      quantity: qty.trim(),
      price: price.trim(),
      charges: (charges || "0").trim(),
      executed_at: istLocalToUtcIso(when),
    });
  };

  return (
    <tr className="editing">
      <td className="num muted">{row.row_number}</td>
      <td colSpan={8}>
        <div className="row-editor">
          <div className="field">
            <span id={`sec-${row.id}`} className="field-label">
              Security
            </span>
            <SecurityPicker labelledBy={`sec-${row.id}`} value={security} onChange={setSecurity} />
          </div>
          <div className="row-editor-grid">
            <label>
              Side
              <select value={side} onChange={(e) => setSide(e.target.value as TxnType)}>
                <option value="">—</option>
                <option value="BUY">Buy</option>
                <option value="SELL">Sell</option>
              </select>
            </label>
            <label>
              Quantity
              <input inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />
            </label>
            <label>
              Price (₹)
              <input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
            </label>
            <label>
              Charges (₹)
              <input inputMode="decimal" value={charges} onChange={(e) => setCharges(e.target.value)} />
            </label>
            <label>
              Date &amp; time (IST)
              <input type="datetime-local" value={when} max={nowIstLocal()} onChange={(e) => setWhen(e.target.value)} />
            </label>
          </div>
          {err && <p className="alert alert-error">{err}</p>}
          <div className="row-editor-actions">
            <button className="btn btn-ghost btn-sm" onClick={onCancel}>
              Cancel
            </button>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </td>
    </tr>
  );
}
