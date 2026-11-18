/** Turn a FastAPI error body into one readable sentence. `detail` is a string for errors we
 *  raise ourselves (e.g. a ledger violation), or a list of field errors when Pydantic
 *  rejects the request body. */
export function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((e: { loc?: unknown[]; msg?: string }) => {
        const field = e.loc?.[e.loc.length - 1];
        const msg = (e.msg ?? "is invalid").replace(/^Value error, /, "");
        return typeof field === "string" && !msg.toLowerCase().startsWith(field) ? `${field}: ${msg}` : msg;
      })
      .join("; ");
  }
  return `Request failed (${status})`;
}
