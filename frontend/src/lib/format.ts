// Presentation-layer formatting. This is the only place decimal strings from the API become
// JS numbers, and only to be rounded for display (CLAUDE.md rule 4: round only at the
// presentation layer). Nothing computed here is ever sent back to the backend.

const IST = "Asia/Kolkata";

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const qty = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 4 });
const pct = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function formatINR(value: string | null | undefined): string {
  return value == null ? "—" : inr.format(Number(value));
}

export function formatQty(value: string): string {
  return qty.format(Number(value));
}

/** "12.3456" -> "+12.35%". Percentages from the API are already multiplied by 100. */
export function formatPct(value: string | null | undefined, { signed = true } = {}): string {
  if (value == null) return "—";
  const n = Number(value);
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${pct.format(n)}%`;
}

/** CSS class for a gain/loss figure. Decided on the string, so "-0.000000" isn't coloured red. */
export function signClass(value: string | null | undefined): "pos" | "neg" | "" {
  if (value == null || /^-?0*\.?0*$/.test(value)) return "";
  return value.startsWith("-") ? "neg" : "pos";
}

/** An API timestamp (UTC) shown in IST (rule 11). */
export function formatDateTimeIST(iso: string): string {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: IST,
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

/** A calendar date ("2026-09-28", e.g. a price date). Formatted without any timezone shift,
 *  because it's a date, not an instant. */
export function formatDate(date: string): string {
  const [y, m, d] = date.split("-").map(Number);
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }).format(
    new Date(Date.UTC(y, m - 1, d)),
  );
}

/** The value of a <input type="datetime-local"> ("2026-09-29T10:30"), read as IST wall-clock
 *  time regardless of the browser's own timezone, converted to a UTC ISO string for the API. */
export function istLocalToUtcIso(local: string): string {
  return new Date(`${local}:00+05:30`).toISOString();
}

/** A UTC ISO timestamp as an IST datetime-local value, for editing an existing time. */
export function utcToIstLocal(iso: string): string {
  return nowIstLocal(new Date(iso));
}

/** Current IST time in datetime-local format, used as the form's default and max. */
export function nowIstLocal(now: Date = new Date()): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: IST,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(now)
      .map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

/** Non-negative plain decimal, as the backend accepts it: no sign, exponent, commas or spaces.
 *  Checked here so the user gets instant feedback; the backend validates again regardless. */
export function isDecimalString(value: string): boolean {
  return /^\d+(\.\d+)?$/.test(value.trim());
}

export const TYPE_LABELS: Record<string, string> = {
  STOCK: "Stocks",
  ETF: "ETFs",
  MF: "Mutual funds",
  BOND: "Bonds",
};
