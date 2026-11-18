import { Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "./auth/AuthProvider";
import { Layout } from "./components/Layout";
import { AuthPage } from "./pages/AuthPage";
import { DashboardPage } from "./pages/DashboardPage";
import { HoldingDetailPage } from "./pages/HoldingDetailPage";
import { HoldingsPage } from "./pages/HoldingsPage";
import { ImportReviewPage } from "./pages/ImportReviewPage";
import { ImportsPage } from "./pages/ImportsPage";
import { TaxPage } from "./pages/TaxPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { TransactionsPage } from "./pages/TransactionsPage";

export function App() {
  const { session, loading, recovering } = useAuth();

  if (loading) return null; // one frame while supabase-js restores the session from storage
  if (recovering) return <ResetPasswordPage />;
  if (!session) return <AuthPage />;

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="holdings" element={<HoldingsPage />} />
        <Route path="holdings/:isin" element={<HoldingDetailPage />} />
        <Route path="transactions" element={<TransactionsPage />} />
        <Route path="tax" element={<TaxPage />} />
        <Route path="import" element={<ImportsPage />} />
        <Route path="import/:id" element={<ImportReviewPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
