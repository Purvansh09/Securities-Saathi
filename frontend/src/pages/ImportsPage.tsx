import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../lib/api";
import { formatDateTimeIST } from "../lib/format";
import type { ImportSummary } from "../lib/types";

export const IMPORT_STATUS_LABEL: Record<string, string> = {
  PARSED: "Ready for review",
  COMMITTED: "Imported",
  FAILED: "Couldn't read",
  PARSING: "Reading…",
  UPLOADED: "Uploaded",
  DISCARDED: "Discarded",
};

export function countsLine(s: ImportSummary): string {
  const c = s.counts;
  const parts = [
    c.COMMITTED && `${c.COMMITTED} imported`,
    c.CONFIRMED && `${c.CONFIRMED} ready`,
    c.PENDING && `${c.PENDING} need attention`,
    c.REJECTED && `${c.REJECTED} excluded`,
  ].filter(Boolean);
  return parts.join(" · ") || "No rows";
}

export function ImportsPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const list = useQuery({ queryKey: ["imports"], queryFn: api.imports });

  const upload = useMutation({
    mutationFn: api.uploadImport,
    onSuccess: (detail) => {
      queryClient.invalidateQueries({ queryKey: ["imports"] });
      navigate(`/import/${detail.id}`);
    },
  });

  const pick = (file: File | undefined) => {
    if (file) upload.mutate(file);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    pick(e.dataTransfer.files[0]);
  };

  return (
    <div className="stack-lg">
      <div className="page-head">
        <h1>Import trades</h1>
      </div>

      <section className="card stack">
        <div
          className={`dropzone${dragging ? " over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => input.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        >
          <input
            ref={input}
            type="file"
            accept=".csv,.xlsx"
            hidden
            onChange={(e) => {
              pick(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          <strong>{upload.isPending ? "Reading your file…" : "Drop a trade file here, or click to choose"}</strong>
          <span className="muted small">.xlsx or .csv, up to 5 MB</span>
        </div>
        {upload.error && <p className="alert alert-error">{upload.error.message}</p>}
        <div className="muted small stack-tight">
          <p>
            Works with tradebook / order-history exports from <b>Zerodha</b>, <b>Groww</b>, <b>Upstox</b> and{" "}
            <b>Angel One</b>. Only completed cash-market trades are imported; F&amp;O and cancelled orders are
            left out.
          </p>
          <p>
            If the columns aren't recognised, the first 15 rows of the file are sent to Claude (Anthropic's AI)
            just to identify which column is which. Nothing is added to your portfolio until you review the rows
            and press Import.
          </p>
        </div>
      </section>

      <section className="card flush">
        <h2 className="card-pad">Previous imports</h2>
        {list.isPending && <p className="muted card-pad">Loading…</p>}
        {list.isError && <p className="alert alert-error card-pad-x">{list.error.message}</p>}
        {list.data?.items.length === 0 && <p className="muted card-pad">None yet.</p>}
        {list.data && list.data.items.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>File</th>
                  <th>Uploaded (IST)</th>
                  <th>Status</th>
                  <th>Rows</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((i) => (
                  <tr key={i.id}>
                    <td>
                      <Link to={`/import/${i.id}`} className="sec-name">
                        {i.original_filename}
                      </Link>
                      {i.profile && <div className="muted small">{i.profile}</div>}
                    </td>
                    <td className="nowrap">{formatDateTimeIST(i.created_at)}</td>
                    <td>
                      <span className={`pill status-${i.status.toLowerCase()}`}>{IMPORT_STATUS_LABEL[i.status] ?? i.status}</span>
                    </td>
                    <td className="muted small">{i.status === "FAILED" ? i.error : countsLine(i)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
