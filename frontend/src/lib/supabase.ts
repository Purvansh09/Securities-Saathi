import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

if (!url || !key) {
  throw new Error("VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY must be set (see frontend/.env.example)");
}

// Used for auth only (CLAUDE.md rule 12): signup, login, token refresh, password reset.
// The frontend never queries Supabase tables directly -- all data goes through the FastAPI
// backend, which is where the ledger rules (sell validation, soft deletes) are enforced.
export const supabase = createClient(url, key, {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
});
