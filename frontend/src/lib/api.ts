import { errorMessage } from "./errors";
import { supabase } from "./supabase";
import type {
  Allocation,
  AllocationBy,
  HoldingDetail,
  Holding,
  LatestPrice,
  Page,
  PortfolioSummary,
  Profile,
  SecuritySummary,
  SecurityType,
  Transaction,
  TransactionCreate,
  XirrReport,
  TaxReport,
  ImportDetail,
  ImportRow,
  ImportRowUpdate,
  ImportSummary,
} from "./types";

const API_URL = (import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  // getSession() returns the cached session and refreshes the access token if it has expired,
  // so every call carries a valid JWT without us tracking expiry ourselves.
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;

  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  // JSON bodies only; a FormData upload must let the browser set its multipart boundary.
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

  const res = await fetch(`${API_URL}/api/v1${path}`, { ...init, headers });
  if (res.status === 204) return undefined as T;

  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, errorMessage(body, res.status));
  return body as T;
}

/** Download a streamed file (statements). A plain <a href> can't send the bearer token, so fetch
 *  it as a blob and hand the browser an object URL to save. */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const { data } = await supabase.auth.getSession();
  const headers = new Headers();
  if (data.session?.access_token) headers.set("Authorization", `Bearer ${data.session.access_token}`);
  const res = await fetch(`${API_URL}/api/v1${path}`, { headers });
  if (!res.ok) throw new ApiError(res.status, errorMessage(await res.json().catch(() => null), res.status));
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const api = {
  me: () => request<Profile>("/me"),

  searchSecurities: (q: string, type?: SecurityType) =>
    request<Page<SecuritySummary>>(`/securities${query({ q, type, limit: 10 })}`),

  security: (isin: string) => request<SecuritySummary>(`/securities/${isin}`),

  /** null when the security has no price yet (the backend answers 404 for that). */
  latestPrice: async (isin: string): Promise<LatestPrice | null> => {
    try {
      return await request<LatestPrice>(`/securities/${isin}/price`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  },

  summary: () => request<PortfolioSummary>("/portfolio/summary"),

  /** Follows the cursor to the end. A personal portfolio is tens of holdings, not thousands,
   *  and the table sorts and totals across all of them, so one list is what the UI wants. */
  holdings: async (type?: SecurityType): Promise<Holding[]> => {
    const all: Holding[] = [];
    let cursor: string | null = null;
    do {
      const page: Page<Holding> = await request(`/portfolio/holdings${query({ type, limit: 200, cursor })}`);
      all.push(...page.items);
      cursor = page.next_cursor;
    } while (cursor);
    return all;
  },

  holding: (isin: string) => request<HoldingDetail>(`/portfolio/holdings/${isin}`),

  xirr: () => request<XirrReport>("/analysis/xirr"),

  capitalGains: (fy?: string) => request<TaxReport>(`/tax/capital-gains${query({ fy })}`),

  imports: () => request<Page<ImportSummary>>("/imports?limit=50"),
  importDetail: (id: string) => request<ImportDetail>(`/imports/${id}`),
  /** multipart upload: the browser sets the boundary, so no Content-Type header here. */
  uploadImport: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ImportDetail>("/imports", { method: "POST", body: form });
  },
  updateImportRow: (importId: string, rowId: string, body: ImportRowUpdate) =>
    request<ImportRow>(`/imports/${importId}/rows/${rowId}`, { method: "PATCH", body: JSON.stringify(body) }),
  commitImport: (id: string) =>
    request<{ transactions_created: number; status: string }>(`/imports/${id}/commit`, { method: "POST" }),
  discardImport: (id: string) => request<void>(`/imports/${id}`, { method: "DELETE" }),

  allocation: (by: AllocationBy) => request<Allocation>(`/portfolio/allocation${query({ by })}`),

  transactions: (cursor: string | null) =>
    request<Page<Transaction>>(`/transactions${query({ limit: 25, cursor })}`),

  createTransaction: (body: TransactionCreate) =>
    request<Transaction>("/transactions", { method: "POST", body: JSON.stringify(body) }),

  deleteTransaction: (id: string) => request<void>(`/transactions/${id}`, { method: "DELETE" }),
};
