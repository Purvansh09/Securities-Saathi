import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "./api";
import type { AllocationBy, SecurityType } from "./types";

// Query keys in one place. Everything derived from the ledger lives under "portfolio", so a
// single invalidateQueries({ queryKey: ["portfolio"] }) after any transaction change refreshes
// the summary, holdings, allocation and holding detail together -- nothing is stored client-side.
export const keys = {
  me: ["me"] as const,
  portfolio: ["portfolio"] as const,
  summary: ["portfolio", "summary"] as const,
  holdings: (type?: SecurityType) => ["portfolio", "holdings", type ?? "ALL"] as const,
  holding: (isin: string) => ["portfolio", "holding", isin] as const,
  allocation: (by: AllocationBy) => ["portfolio", "allocation", by] as const,
  xirr: ["portfolio", "xirr"] as const, // under "portfolio": refreshed by any ledger change
  transactions: ["transactions"] as const,
  search: (q: string) => ["securities", "search", q] as const,
  price: (isin: string) => ["securities", "price", isin] as const,
};

export const useMe = () => useQuery({ queryKey: keys.me, queryFn: api.me });
export const useSummary = () => useQuery({ queryKey: keys.summary, queryFn: api.summary });
export const useHoldings = (type?: SecurityType) =>
  useQuery({ queryKey: keys.holdings(type), queryFn: () => api.holdings(type) });
export const useAllocation = (by: AllocationBy) =>
  useQuery({ queryKey: keys.allocation(by), queryFn: () => api.allocation(by) });

export const useXirr = () => useQuery({ queryKey: keys.xirr, queryFn: api.xirr });

export function useDebounced<T>(value: T, ms = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}
