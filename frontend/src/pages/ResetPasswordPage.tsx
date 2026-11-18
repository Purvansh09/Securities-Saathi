import { useState, type FormEvent } from "react";

import { useAuth } from "../auth/AuthProvider";
import { supabase } from "../lib/supabase";

/** Shown after the user follows a password-reset email link. Supabase has already signed
 *  them in with a short-lived recovery session; this sets the new password on it. */
export function ResetPasswordPage() {
  const { finishRecovery } = useAuth();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { error } = await supabase.auth.updateUser({ password });
    setBusy(false);
    if (error) setError(error.message);
    else finishRecovery();
  };

  return (
    <main className="auth-shell">
      <div className="auth-card">
        <div className="brand brand-lg">Securities Saathi</div>
        <h1>Choose a new password</h1>
        <form onSubmit={onSubmit} className="stack">
          <label>
            New password
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          {error && <p className="alert alert-error">{error}</p>}
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Saving…" : "Save password"}
          </button>
        </form>
      </div>
    </main>
  );
}
