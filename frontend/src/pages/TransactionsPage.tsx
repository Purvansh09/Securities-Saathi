import { useInfiniteQuery, useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ConfirmButton } from "../components/ConfirmButton";
import { TransactionForm } from "../components/TransactionForm";
import { api } from "../lib/api";
import { formatDateTimeIST, formatINR, formatQty } from "../lib/format";
import { keys } from "../lib/queries";
import type { Transaction } from "../lib/types";

export function TransactionsPage() {
  const queryClient = useQueryClient();
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const txns = useInfiniteQuery({
    queryKey: keys.transactions,
    queryFn: ({ pageParam }) => api.transactions(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const rows = txns.data?.pages.flatMap((p) => p.items) ?? [];

  // Transactions carry only the ISIN. Look each distinct one up once; the securities master
  // barely changes, so these stay cached for the session.
  const isins = [...new Set(rows.map((t) => t.isin))];
  const names = useQueries({
    queries: isins.map((isin) => ({
      queryKey: ["securities", "detail", isin],
      queryFn: () => api.security(isin),
      staleTime: Infinity,
    })),
  });
  const nameOf = (isin: string) => names[isins.indexOf(isin)]?.data;

  const remove = useMutation({
    mutationFn: (t: Transaction) => api.deleteTransaction(t.id),
    onMutate: () => setDeleteError(null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.transactions });
      queryClient.invalidateQueries({ queryKey: keys.portfolio });
    },
    // 422 here means a later SELL depends on this BUY -- the backend's ledger check (rule 5).
    onError: (e) => setDeleteError(e.message),
  });


  return (
    <div className="grid-txn">
      <div>
        <TransactionForm />
      </div>

      <section className="card flush">
        <h2 className="card-pad">Ledger</h2>
        {deleteError && <p className="alert alert-error card-pad-x">{deleteError}</p>}
        {txns.isPending && <p className="muted card-pad">Loading…</p>}
        {txns.isError && <p className="alert alert-error card-pad-x">{txns.error.message}</p>}
        {txns.isSuccess && rows.length === 0 && <p className="muted card-pad">No transactions yet.</p>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date (IST)</th>
                  <th>Security</th>
                  <th>Type</th>
                  <th className="num">Qty</th>
                  <th className="num">Price</th>
                  <th className="num">Charges</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => {
                  const sec = nameOf(t.isin);
                  return (
                    <tr key={t.id}>
                      <td className="nowrap">{formatDateTimeIST(t.executed_at)}</td>
                      <td>
                        <div className="sec-name">{sec?.symbol ?? sec?.name ?? t.isin}</div>
                        {t.notes && <div className="muted small ellipsis">{t.notes}</div>}
                      </td>
                      <td>
                        <span className={`pill ${t.txn_type.toLowerCase()}`}>{t.txn_type}</span>
                      </td>
                      <td className="num">{formatQty(t.quantity)}</td>
                      <td className="num">{formatINR(t.price)}</td>
                      <td className="num">{formatINR(t.charges)}</td>
                      <td className="num">
                        {/* Soft delete (rule 2): gone from holdings, kept in transaction_revisions. */}
                        <ConfirmButton
                          className="btn btn-ghost btn-sm"
                          question="Delete?"
                          confirmLabel="Delete"
                          danger
                          disabled={remove.isPending && remove.variables?.id === t.id}
                          onConfirm={() => remove.mutate(t)}
                        >
                          Delete
                        </ConfirmButton>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {txns.hasNextPage && (
          <div className="card-pad">
            <button className="btn btn-ghost" onClick={() => txns.fetchNextPage()} disabled={txns.isFetchingNextPage}>
              {txns.isFetchingNextPage ? "Loading…" : "Load more"}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
