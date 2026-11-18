import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { api, ApiError } from "../lib/api";
import { formatDate, formatINR, formatQty, isDecimalString, istLocalToUtcIso, nowIstLocal } from "../lib/format";
import { keys, useHoldings } from "../lib/queries";
import type { SecuritySummary, TxnType } from "../lib/types";
import { SecurityPicker } from "./SecurityPicker";

export function TransactionForm() {
  const queryClient = useQueryClient();
  const [security, setSecurity] = useState<SecuritySummary | null>(null);
  const [txnType, setTxnType] = useState<TxnType>("BUY");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [charges, setCharges] = useState("0");
  const [executedAt, setExecutedAt] = useState(nowIstLocal);
  const [notes, setNotes] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const lastPrice = useQuery({
    queryKey: keys.price(security?.isin ?? ""),
    queryFn: () => api.latestPrice(security!.isin),
    enabled: !!security,
  });

  // Prefill the price with the last close/NAV the first time one arrives, but never
  // overwrite something the user has typed -- the actual trade price is usually different.
  useEffect(() => {
    // Functional update: `price` is deliberately not a dependency, this runs only when a new price arrives.
    if (lastPrice.data) {
      const suggested = lastPrice.data.price.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
      setPrice((current) => (current === "" ? suggested : current));
    }
  }, [lastPrice.data]);

  // Shown on SELL so the user can see the limit before the backend enforces it.
  const holdings = useHoldings();
  const held = security ? holdings.data?.find((h) => h.isin === security.isin)?.quantity : undefined;

  const create = useMutation({
    mutationFn: api.createTransaction,
    onSuccess: (txn) => {
      queryClient.invalidateQueries({ queryKey: keys.portfolio });
      queryClient.invalidateQueries({ queryKey: keys.transactions });
      setSaved(`${txn.txn_type === "BUY" ? "Bought" : "Sold"} ${formatQty(txn.quantity)} × ${security?.name}`);
      setSecurity(null);
      setQuantity("");
      setPrice("");
      setCharges("0");
      setNotes("");
    },
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setSaved(null);
    create.reset();

    const problem = !security
      ? "Choose a security."
      : !isDecimalString(quantity) || Number(quantity) === 0
        ? "Quantity must be a number greater than 0."
        : !isDecimalString(price)
          ? "Price must be a number (0 or more)."
          : !isDecimalString(charges || "0")
            ? "Charges must be a number (0 or more)."
            : !executedAt
              ? "Enter the trade date and time."
              : executedAt > nowIstLocal()
                ? "The trade time can't be in the future."
                : null;
    setFieldError(problem);
    if (problem || !security) return;

    create.mutate({
      isin: security.isin,
      txn_type: txnType,
      // Sent exactly as typed (trimmed): decimal strings, never parsed to float (rule 4).
      quantity: quantity.trim(),
      price: price.trim(),
      charges: (charges || "0").trim(),
      executed_at: istLocalToUtcIso(executedAt),
      notes: notes.trim() || null,
    });
  };

  // A preview only (presentation rounding); the backend does the real arithmetic in Decimal.
  const total =
    isDecimalString(quantity) && isDecimalString(price) && isDecimalString(charges || "0")
      ? Number(quantity) * Number(price) + (txnType === "BUY" ? 1 : -1) * Number(charges || "0")
      : null;

  const serverError = create.error instanceof ApiError ? create.error.message : create.error?.message;

  return (
    <form className="card stack" onSubmit={onSubmit}>
      <h2>Add transaction</h2>

      <div className="segmented" role="radiogroup" aria-label="Transaction type">
        {(["BUY", "SELL"] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="radio"
            aria-checked={txnType === t}
            className={txnType === t ? `on ${t.toLowerCase()}` : undefined}
            onClick={() => setTxnType(t)}
          >
            {t === "BUY" ? "Buy" : "Sell"}
          </button>
        ))}
      </div>

      {/* Deliberately a div, not a <label>: a label forwards every click inside it to its first
          control, so picking an option also "clicked" the Change button that replaces the list,
          which immediately cleared the selection. */}
      <div className="field">
        <span id="security-label" className="field-label">
          Security
        </span>
        <SecurityPicker
          labelledBy="security-label"
          value={security}
          onChange={(s) => {
            setSecurity(s);
            setPrice("");
          }}
        />
      </div>
      {security && (
        <p className="muted small hint">
          {lastPrice.data
            ? `Last price ${formatINR(lastPrice.data.price)} on ${formatDate(lastPrice.data.price_date)}`
            : lastPrice.isSuccess
              ? "No price on record yet."
              : null}
          {txnType === "SELL" && ` · You hold ${held ? formatQty(held) : "0"}`}
        </p>
      )}

      <div className="grid-2">
        <label>
          Quantity
          <input inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} placeholder="10" />
        </label>
        <label>
          Price per unit (₹)
          <input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="1450.50" />
        </label>
        <label>
          Charges (₹)
          <input inputMode="decimal" value={charges} onChange={(e) => setCharges(e.target.value)} />
          <span className="muted small">Brokerage, STT, stamp duty, fees</span>
        </label>
        <label>
          Date &amp; time (IST)
          <input
            type="datetime-local"
            value={executedAt}
            max={nowIstLocal()}
            onChange={(e) => setExecutedAt(e.target.value)}
          />
        </label>
      </div>

      <label>
        Notes <span className="muted small">(optional)</span>
        <input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={500} />
      </label>

      {total !== null && (
        <p className="total-line">
          {txnType === "BUY" ? "Total cost" : "Net proceeds"} <strong>{formatINR(String(total))}</strong>
        </p>
      )}

      {fieldError && <p className="alert alert-error">{fieldError}</p>}
      {serverError && <p className="alert alert-error">{serverError}</p>}
      {saved && <p className="alert alert-ok">{saved}</p>}

      <button type="submit" className="btn btn-primary" disabled={create.isPending}>
        {create.isPending ? "Saving…" : txnType === "BUY" ? "Record buy" : "Record sell"}
      </button>
    </form>
  );
}
