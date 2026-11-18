import { useQuery } from "@tanstack/react-query";
import { useId, useState, type KeyboardEvent } from "react";

import { api } from "../lib/api";
import { TYPE_LABELS } from "../lib/format";
import { keys, useDebounced } from "../lib/queries";
import type { SecuritySummary } from "../lib/types";

interface Props {
  value: SecuritySummary | null;
  onChange: (security: SecuritySummary | null) => void;
  /** id of the visible caption. Must not be rendered inside a <label> (see TransactionForm). */
  labelledBy?: string;
}

/** Search-as-you-type over GET /securities (name, symbol or ISIN prefix). */
export function SecurityPicker({ value, onChange, labelledBy }: Props) {
  const listId = useId();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const q = useDebounced(text.trim());

  const results = useQuery({
    queryKey: keys.search(q),
    queryFn: () => api.searchSecurities(q),
    enabled: q.length > 0,
    staleTime: 5 * 60_000, // the securities master changes once a day at most
  });
  const items = results.data?.items ?? [];

  const pick = (s: SecuritySummary) => {
    onChange(s);
    setText("");
    setOpen(false);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!open || items.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % items.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + items.length) % items.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(items[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  if (value) {
    return (
      <div className="picked">
        <div>
          <div className="picked-name">{value.name}</div>
          <div className="muted small">
            {[value.symbol, value.exchange, TYPE_LABELS[value.type], value.isin].filter(Boolean).join(" · ")}
          </div>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange(null)}>
          Change
        </button>
      </div>
    );
  }

  return (
    <div className="combo">
      <input
        role="combobox"
        aria-expanded={open && q.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-labelledby={labelledBy}
        placeholder="Search by name, symbol or ISIN"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)} // let a click on an option land first
        onKeyDown={onKeyDown}
      />
      {open && q.length > 0 && (
        <ul className="combo-list" id={listId} role="listbox">
          {results.isFetching && items.length === 0 && <li className="combo-empty">Searching…</li>}
          {results.isError && <li className="combo-empty">Search failed: {results.error.message}</li>}
          {!results.isFetching && results.isSuccess && items.length === 0 && (
            <li className="combo-empty">No security matches “{q}”</li>
          )}
          {items.map((s, i) => (
            <li
              key={s.isin}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : undefined}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(s)}
              onMouseEnter={() => setActive(i)}
            >
              <span className="combo-name">{s.name}</span>
              <span className="muted small">
                {[s.symbol, TYPE_LABELS[s.type], s.isin].filter(Boolean).join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
