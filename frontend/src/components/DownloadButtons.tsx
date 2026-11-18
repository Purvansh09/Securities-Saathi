import { useState } from "react";

import { downloadFile } from "../lib/api";

/** "Excel" and "PDF" buttons for one statement. `path` is the API path without the format. */
export function DownloadButtons({ path, filename, label }: { path: string; filename: string; label: string }) {
  const [busy, setBusy] = useState<"xlsx" | "pdf" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const get = async (format: "xlsx" | "pdf") => {
    setBusy(format);
    setError(null);
    try {
      const sep = path.includes("?") ? "&" : "?";
      await downloadFile(`${path}${sep}format=${format}`, `${filename}.${format}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="downloads">
      <span className="muted small">{label}</span>
      <button className="btn btn-ghost btn-sm" onClick={() => get("xlsx")} disabled={busy !== null}>
        {busy === "xlsx" ? "Preparing…" : "Excel"}
      </button>
      <button className="btn btn-ghost btn-sm" onClick={() => get("pdf")} disabled={busy !== null}>
        {busy === "pdf" ? "Preparing…" : "PDF"}
      </button>
      {error && <span className="neg small">{error}</span>}
    </div>
  );
}
