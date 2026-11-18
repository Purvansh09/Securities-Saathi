// Mirrors backend/app/schemas/*.py. Money, quantity and percentage fields are decimal
// strings (CLAUDE.md rule 4) and stay strings here -- they are only turned into numbers
// at the moment they are formatted for display (lib/format.ts).

export type SecurityType = "STOCK" | "ETF" | "MF" | "BOND";
export type TxnType = "BUY" | "SELL";

export interface Profile {
  id: string;
  full_name: string | null;
  preferences: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SecuritySummary {
  isin: string;
  name: string;
  type: SecurityType;
  symbol: string | null;
  exchange: string | null;
  sector: string | null;
  is_custom: boolean;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface LatestPrice {
  isin: string;
  price_date: string;
  price: string;
  source: string;
}

export interface Transaction {
  id: string;
  isin: string;
  txn_type: TxnType;
  quantity: string;
  price: string;
  charges: string;
  executed_at: string;
  notes: string | null;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface TransactionCreate {
  isin: string;
  txn_type: TxnType;
  quantity: string;
  price: string;
  charges: string;
  executed_at: string; // ISO 8601 UTC
  notes: string | null;
}

export interface Holding {
  isin: string;
  symbol: string | null;
  name: string;
  type: SecurityType;
  sector: string | null;
  quantity: string;
  avg_cost: string | null;
  invested: string;
  price: string | null;
  price_date: string | null;
  price_estimated: boolean;
  market_value: string | null;
  unrealized_pnl: string | null;
  unrealized_pnl_pct: string | null;
  realized_pnl: string;
  weight_pct: string;
}

export interface OpenLot {
  buy_transaction_id: string;
  acquired_at: string;
  quantity: string;
  unit_cost: string;
  cost: string;
}

export interface RealizedLot {
  buy_transaction_id: string;
  sell_transaction_id: string;
  acquired_at: string;
  sold_at: string;
  quantity: string;
  cost: string;
  proceeds: string;
  gain: string;
  holding_days: number;
}

export interface HoldingDetail extends Holding {
  open_lots: OpenLot[];
  realized: RealizedLot[];
}

export interface PortfolioSummary {
  as_of: string;
  invested: string;
  current_value: string;
  unrealized_pnl: string;
  unrealized_pnl_pct: string | null;
  realized_pnl: string;
  holdings_count: number;
  unpriced_holdings: number;
  estimated_holdings: number;
}

export interface XirrResult {
  xirr_pct: string | null;
  invested: string;
  received: string;
  current_value: string;
  first_date: string | null;
  days: number;
  short_period: boolean;
  includes_unpriced: boolean;
}

export interface XirrHolding extends XirrResult {
  isin: string;
  symbol: string | null;
  name: string;
  type: SecurityType;
  is_open: boolean;
}

export interface XirrReport {
  as_of: string;
  portfolio: XirrResult;
  holdings: XirrHolding[];
}

export interface TaxLot {
  isin: string;
  symbol: string | null;
  name: string;
  type: SecurityType;
  asset_basis: string;
  classification_certain: boolean;
  acquired_on: string;
  sold_on: string;
  holding_days: number;
  quantity: string;
  cost: string;
  proceeds: string;
  gain: string;
  term: "SHORT" | "LONG";
  rule: string;
  rate_pct: string | null;
  notes: string[];
}

export interface TaxBucket {
  label: string;
  term: "SHORT" | "LONG";
  category: "EQUITY" | "OTHER";
  rate_pct: string | null;
  gains: string;
  losses: string;
  net: string;
  set_off: string;
  exemption: string;
  taxable: string;
  tax: string | null;
}

export interface TaxReport {
  fy: string;
  period_from: string;
  period_to: string;
  years_available: string[];
  rules_verified_on: string;
  sources: string[];
  lots: TaxLot[];
  buckets: TaxBucket[];
  summary: {
    exemption_limit: string;
    exemption_used: string;
    special_rate_tax: string;
    cess: string;
    total_estimated_tax: string;
    slab_income: string;
    short_term_loss_carried: string;
    long_term_loss_carried: string;
    notes: string[];
  };
}

export type ImportStatus = "UPLOADED" | "PARSING" | "PARSED" | "COMMITTED" | "FAILED" | "DISCARDED";
export type ImportRowStatus = "PENDING" | "CONFIRMED" | "REJECTED" | "COMMITTED";

export interface ImportRow {
  id: string;
  row_number: number;
  raw: Record<string, string>;
  isin: string | null;
  security_name: string | null;
  security_symbol: string | null;
  txn_type: TxnType | null;
  quantity: string | null;
  price: string | null;
  charges: string | null;
  executed_at: string | null;
  status: ImportRowStatus;
  issues: string[];
}

export interface ImportSummary {
  id: string;
  original_filename: string;
  status: ImportStatus;
  parser: "RULES" | "LLM" | null;
  profile: string | null;
  error: string | null;
  created_at: string;
  committed_at: string | null;
  counts: Partial<Record<ImportRowStatus, number>>;
}

export interface ImportDetail extends ImportSummary {
  rows: ImportRow[];
}

export interface ImportRowUpdate {
  isin?: string;
  txn_type?: TxnType;
  quantity?: string;
  price?: string;
  charges?: string;
  executed_at?: string;
  status?: "CONFIRMED" | "REJECTED" | "PENDING";
}

export type AllocationBy = "asset_class" | "sector";

export interface Allocation {
  by: AllocationBy;
  total_value: string;
  buckets: { key: string; value: string; weight_pct: string; holdings_count: number }[];
}
