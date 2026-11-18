import { useState, type FormEvent } from "react";

import { supabase } from "../lib/supabase";

type Mode = "signin" | "signup" | "forgot";

// All of this is Supabase Auth (CLAUDE.md rule 12). We never see or store a password;
// the backend only ever receives the JWT Supabase issues.
export function AuthPage() {
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
    setNotice(null);
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "signin") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      } else if (mode === "signup") {
        const { data, error } = await supabase.auth.signUp({
          email,
          password,
          // full_name is read by the handle_new_user trigger into profiles.full_name.
          options: { data: { full_name: fullName.trim() || null }, emailRedirectTo: window.location.origin },
        });
        if (error) throw error;
        // With email confirmation on (Supabase's default) there's no session until the link is clicked.
        if (!data.session) setNotice("Check your inbox for a confirmation link, then sign in.");
      } else {
        const { error } = await supabase.auth.resetPasswordForEmail(email, { redirectTo: window.location.origin });
        if (error) throw error;
        setNotice("If that email has an account, a reset link is on its way.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const title = { signin: "Sign in", signup: "Create account", forgot: "Reset password" }[mode];

  return (
    <main className="auth-shell">
      <div className="auth-card">
        <div className="brand brand-lg">Securities Saathi</div>
        <p className="muted">Every holding across every platform, in one ledger.</p>

        <h1>{title}</h1>
        <form onSubmit={onSubmit} className="stack">
          {mode === "signup" && (
            <label>
              Full name
              <input value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" />
            </label>
          )}
          <label>
            Email
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </label>
          {mode !== "forgot" && (
            <label>
              Password
              <input
                type="password"
                required
                minLength={mode === "signup" ? 8 : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "signup" ? "new-password" : "current-password"}
              />
            </label>
          )}

          {error && <p className="alert alert-error">{error}</p>}
          {notice && <p className="alert alert-info">{notice}</p>}

          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Please wait…" : { signin: "Sign in", signup: "Create account", forgot: "Send reset link" }[mode]}
          </button>
        </form>

        <div className="auth-links">
          {mode === "signin" && (
            <>
              <button className="link" onClick={() => switchMode("forgot")}>Forgot password?</button>
              <span>
                New here? <button className="link" onClick={() => switchMode("signup")}>Create an account</button>
              </span>
            </>
          )}
          {mode !== "signin" && (
            <button className="link" onClick={() => switchMode("signin")}>Back to sign in</button>
          )}
        </div>
      </div>
    </main>
  );
}
