-- Harden functions flagged by the Supabase security advisor after the Phase 1 migration.
--
-- 1. Pin search_path on SQL/PLpgSQL functions that didn't already set one.
--    Without this, a function can be tricked into resolving an unqualified
--    name (a type, operator, or another function) against a schema the
--    caller controls. All our functions already qualify tables as
--    public.<table>, but pinning search_path closes the hole for good.
--
-- 2. Revoke direct EXECUTE on the two trigger-only functions from
--    public/anon/authenticated. Postgres grants EXECUTE to PUBLIC by
--    default, which PostgREST turns into a callable RPC endpoint
--    (/rest/v1/rpc/handle_new_user, etc.) for a SECURITY DEFINER function
--    -- letting anyone invoke it directly instead of only via its trigger.
--    Revoking EXECUTE does not stop the trigger from firing: trigger
--    invocation doesn't go through the caller's EXECUTE privilege.

alter function public.set_updated_at()                                  set search_path = public, pg_temp;
alter function public.quantity_held(uuid, uuid, timestamptz)            set search_path = public, pg_temp;
alter function public.ledger_balance_ok(uuid, uuid)                     set search_path = public, pg_temp;

revoke execute on function public.handle_new_user()          from public, anon, authenticated;
revoke execute on function public.log_transaction_revision() from public, anon, authenticated;
