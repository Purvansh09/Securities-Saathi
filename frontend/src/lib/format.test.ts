import { describe, expect, it } from "vitest";

import { errorMessage } from "./errors";
import { formatPct, isDecimalString, istLocalToUtcIso, nowIstLocal, signClass } from "./format";

describe("IST handling (rule 11)", () => {
  it("reads datetime-local input as IST, whatever the browser timezone", () => {
    expect(istLocalToUtcIso("2026-09-29T10:30")).toBe("2026-09-29T05:00:00.000Z");
  });

  it("crosses midnight correctly", () => {
    // 02:00 IST on 1 April is still 31 March in UTC -- matters for financial-year boundaries.
    expect(istLocalToUtcIso("2026-04-01T02:00")).toBe("2026-03-31T20:30:00.000Z");
  });

  it("formats the current instant as IST wall-clock time", () => {
    expect(nowIstLocal(new Date("2026-09-28T20:00:00Z"))).toBe("2026-09-29T01:30");
  });
});

describe("decimal input validation (rule 4)", () => {
  it.each(["10", "0", "1450.50", "0.001", " 12 "])("accepts %j", (v) => expect(isDecimalString(v)).toBe(true));
  it.each(["", "-5", "1e3", "1,000", "12.", ".5", "NaN", "Infinity", "12.3.4"])("rejects %j", (v) =>
    expect(isDecimalString(v)).toBe(false),
  );
});

describe("gain/loss colouring", () => {
  it("colours by sign, decided on the string", () => {
    expect(signClass("12.5")).toBe("pos");
    expect(signClass("-0.01")).toBe("neg");
    expect(signClass("0.000000")).toBe("");
    expect(signClass("-0.000000")).toBe("");
    expect(signClass(null)).toBe("");
  });

  it("signs percentages", () => {
    expect(formatPct("12.3456")).toBe("+12.35%");
    expect(formatPct("-3.1")).toBe("-3.10%");
    expect(formatPct("40", { signed: false })).toBe("40.00%");
    expect(formatPct(null)).toBe("—");
  });
});

describe("API error messages", () => {
  it("passes our own detail strings through (e.g. a ledger violation)", () => {
    expect(errorMessage({ detail: "Sell exceeds holding" }, 422)).toBe("Sell exceeds holding");
  });

  it("flattens Pydantic field errors", () => {
    const body = {
      detail: [{ loc: ["body", "quantity"], msg: "Value error, quantity must be greater than 0", type: "value_error" }],
    };
    expect(errorMessage(body, 422)).toBe("quantity must be greater than 0");
  });

  it("names the field when the message doesn't", () => {
    const body = { detail: [{ loc: ["body", "isin"], msg: "Field required" }] };
    expect(errorMessage(body, 422)).toBe("isin: Field required");
  });

  it("falls back to the status for an unreadable body", () => {
    expect(errorMessage(null, 502)).toBe("Request failed (502)");
  });
});
