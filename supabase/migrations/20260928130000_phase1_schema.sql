-- Phase 1 schema: portfolio aggregator for NSE/BSE securities.
-- Target: Supabase Postgres (uses auth.users, auth.uid()).
--
-- Design rules (see CLAUDE.md):
--   * transactions is the ledger; holdings are always derived from it
--   * one securities table, ISIN is the identifier, type-specific data in metadata jsonb
--   * money and quantity are numeric, never float
--   * no hard deletes on user data (soft delete + revision snapshots)

create schema if not exists extensions;
create extension if not exists pg_trgm with schema extensions;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
create type public.security_type as enum ('STOCK', 'MF', 'ETF', 'BOND');
create type public.txn_type as enum ('BUY', 'SELL');
create type public.import_status as enum ('UPLOADED', 'PARSING', 'PARSED', 'COMMITTED', 'FAILED', 'DISCARDED');
create type public.import_row_status as enum ('PENDING', 'CONFIRMED', 'REJECTED', 'COMMITTED');

-- ---------------------------------------------------------------------------
-- Shared trigger function
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end
$$;

-- ---------------------------------------------------------------------------
-- profiles (one row per auth user, created automatically on signup)
-- ---------------------------------------------------------------------------
create table public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  full_name   text,
  preferences jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create trigger trg_profiles_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, full_name)
  values (new.id, new.raw_user_meta_data ->> 'full_name')
  on conflict (id) do nothing;
  return new;
end
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- securities (master list; written by the sync job, or by users for custom entries)
-- ---------------------------------------------------------------------------
create table public.securities (
  id             uuid primary key default gen_random_uuid(),
  isin           text not null,
  name           text not null,
  type           public.security_type not null,
  symbol         text,                       -- primary trading symbol; null for most MFs
  exchange       text,                       -- primary listing: 'NSE', 'BSE', 'AMFI'
  sector         text,
  is_active      boolean not null default true,
  is_custom      boolean not null default false,
  created_by     uuid references public.profiles (id) on delete cascade,
  metadata       jsonb not null default '{}'::jsonb,  -- bond coupon/maturity, MF scheme code/AMC, both exchange symbols
  last_synced_at timestamptz,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint securities_isin_key unique (isin),
  constraint securities_isin_format check (isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
  constraint securities_custom_has_owner check (not is_custom or created_by is not null)
);

comment on table public.securities is
  'Master list. Custom rows (is_custom) are visible only to created_by. A later sync that inserts the same ISIN promotes the row: is_custom=false, created_by=null.';

create index securities_type_idx        on public.securities (type) where is_active;
create index securities_isin_prefix_idx on public.securities (isin text_pattern_ops);
create index securities_name_trgm_idx   on public.securities using gin (name extensions.gin_trgm_ops);
create index securities_symbol_trgm_idx on public.securities using gin (symbol extensions.gin_trgm_ops);
create index securities_created_by_idx  on public.securities (created_by) where created_by is not null;

create trigger trg_securities_updated_at
  before update on public.securities
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- security_prices (daily close price or NAV)
-- ---------------------------------------------------------------------------
create table public.security_prices (
  security_id uuid not null references public.securities (id) on delete cascade,
  price_date  date not null,
  price       numeric(20, 6) not null check (price >= 0),  -- closing price, or NAV for mutual funds
  source      text not null,                                -- 'NSE', 'BSE', 'AMFI', 'YFINANCE', 'MANUAL'
  created_at  timestamptz not null default now(),
  primary key (security_id, price_date)
);

create view public.latest_prices
with (security_invoker = true) as
select distinct on (security_id)
  security_id,
  price_date,
  price,
  source
from public.security_prices
order by security_id, price_date desc;

-- ---------------------------------------------------------------------------
-- imports + import_rows (Excel upload staging; API arrives in Phase 2)
-- ---------------------------------------------------------------------------
create table public.imports (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references public.profiles (id) on delete cascade,
  original_filename text not null,
  storage_path      text not null,      -- object path in Supabase Storage, never the file itself
  file_sha256       text not null,
  status            public.import_status not null default 'UPLOADED',
  parser            text check (parser in ('RULES', 'LLM')),
  error             text,
  created_at        timestamptz not null default now(),
  parsed_at         timestamptz,
  committed_at      timestamptz
);

-- The same file cannot be active twice, but can be re-uploaded after a discard or failure.
create unique index imports_user_file_active_idx
  on public.imports (user_id, file_sha256)
  where status not in ('DISCARDED', 'FAILED');

create index imports_user_idx on public.imports (user_id, created_at desc);

create table public.import_rows (
  id          uuid primary key default gen_random_uuid(),
  import_id   uuid not null references public.imports (id) on delete cascade,
  user_id     uuid not null references public.profiles (id) on delete cascade,
  row_number  integer not null,
  raw         jsonb not null,                  -- original row as read from the file
  isin        text,
  security_id uuid references public.securities (id),
  txn_type    public.txn_type,
  quantity    numeric(20, 6),
  price       numeric(20, 6),
  charges     numeric(20, 4),
  executed_at timestamptz,
  status      public.import_row_status not null default 'PENDING',
  issues      text[] not null default '{}',    -- validation problems shown in the review screen
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (import_id, row_number)
);

create index import_rows_import_idx on public.import_rows (import_id);

create trigger trg_import_rows_updated_at
  before update on public.import_rows
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- transactions (THE LEDGER)
-- ---------------------------------------------------------------------------
create table public.transactions (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references public.profiles (id) on delete cascade,
  security_id   uuid not null references public.securities (id),
  txn_type      public.txn_type not null,
  quantity      numeric(20, 6) not null check (quantity > 0),
  price         numeric(20, 6) not null check (price >= 0),   -- per unit
  charges       numeric(20, 4) not null default 0 check (charges >= 0),  -- brokerage, STT, etc., total for this trade
  executed_at   timestamptz not null,
  notes         text,
  source        text not null default 'MANUAL' check (source in ('MANUAL', 'IMPORT')),
  import_row_id uuid unique references public.import_rows (id),  -- unique: a staged row can be committed only once
  client_ref    text,                                            -- optional client-generated key against double submits
  deleted_at    timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint transactions_import_source_consistent
    check ((source = 'IMPORT') = (import_row_id is not null))
);

create unique index transactions_client_ref_idx
  on public.transactions (user_id, client_ref)
  where client_ref is not null;

create index transactions_user_security_idx
  on public.transactions (user_id, security_id, executed_at)
  where deleted_at is null;

create index transactions_user_executed_idx
  on public.transactions (user_id, executed_at desc)
  where deleted_at is null;

create trigger trg_transactions_updated_at
  before update on public.transactions
  for each row execute function public.set_updated_at();

-- Audit trail: every update (including a soft delete) stores the row as it was before the change.
create table public.transaction_revisions (
  id             bigint generated always as identity primary key,
  transaction_id uuid not null,
  user_id        uuid not null references public.profiles (id) on delete cascade,
  operation      text not null check (operation in ('UPDATE', 'SOFT_DELETE')),
  snapshot       jsonb not null,
  revised_at     timestamptz not null default now()
);

create index transaction_revisions_txn_idx on public.transaction_revisions (transaction_id, revised_at desc);

create or replace function public.log_transaction_revision()
returns trigger
language plpgsql
security definer          -- so the insert works even though users have no INSERT policy on revisions
set search_path = public
as $$
begin
  insert into public.transaction_revisions (transaction_id, user_id, operation, snapshot)
  values (
    old.id,
    old.user_id,
    case when new.deleted_at is not null and old.deleted_at is null then 'SOFT_DELETE' else 'UPDATE' end,
    to_jsonb(old)
  );
  return new;
end
$$;

create trigger trg_transactions_revision
  before update on public.transactions
  for each row
  when (old.* is distinct from new.*)
  execute function public.log_transaction_revision();

-- ---------------------------------------------------------------------------
-- Ledger helper functions (used by the backend for sell validation)
-- ---------------------------------------------------------------------------

-- Quantity held for one user + security as of a moment in time.
create or replace function public.quantity_held(
  p_user     uuid,
  p_security uuid,
  p_as_of    timestamptz default now()
)
returns numeric
language sql
stable
as $$
  select coalesce(sum(case when txn_type = 'BUY' then quantity else -quantity end), 0)
  from public.transactions
  where user_id = p_user
    and security_id = p_security
    and deleted_at is null
    and executed_at <= p_as_of;
$$;

-- True when the running balance never goes negative across the whole timeline.
-- On identical timestamps, BUYs are applied before SELLs.
create or replace function public.ledger_balance_ok(p_user uuid, p_security uuid)
returns boolean
language sql
stable
as $$
  select coalesce(bool_and(running >= 0), true)
  from (
    select sum(case when txn_type = 'BUY' then quantity else -quantity end)
             over (order by executed_at, case when txn_type = 'BUY' then 0 else 1 end, created_at, id) as running
    from public.transactions
    where user_id = p_user
      and security_id = p_security
      and deleted_at is null
  ) t;
$$;

-- ---------------------------------------------------------------------------
-- Derived views (cost basis of remaining lots is computed with FIFO in the backend)
-- ---------------------------------------------------------------------------
create view public.v_holdings
with (security_invoker = true) as
select
  t.user_id,
  t.security_id,
  s.isin,
  s.name,
  s.type,
  s.sector,
  sum(case when t.txn_type = 'BUY' then t.quantity else -t.quantity end)                        as net_quantity,
  sum(case when t.txn_type = 'BUY' then t.quantity * t.price + t.charges else 0 end)             as gross_buy_cost,
  sum(case when t.txn_type = 'SELL' then t.quantity * t.price - t.charges else 0 end)            as gross_sell_proceeds,
  min(t.executed_at)                                                                              as first_txn_at,
  max(t.executed_at)                                                                              as last_txn_at
from public.transactions t
join public.securities s on s.id = t.security_id
where t.deleted_at is null
group by t.user_id, t.security_id, s.isin, s.name, s.type, s.sector
having sum(case when t.txn_type = 'BUY' then t.quantity else -t.quantity end) <> 0;

create view public.v_holdings_valued
with (security_invoker = true) as
select
  h.*,
  lp.price      as latest_price,
  lp.price_date as latest_price_date,
  h.net_quantity * lp.price as current_value
from public.v_holdings h
left join public.latest_prices lp on lp.security_id = h.security_id;

-- ---------------------------------------------------------------------------
-- Row level security
-- The FastAPI backend connects with a role that bypasses RLS and scopes every query by user_id.
-- These policies are defense in depth for any direct client access.
-- There are deliberately no DELETE policies: user data is never hard-deleted.
-- ---------------------------------------------------------------------------
alter table public.profiles              enable row level security;
alter table public.securities            enable row level security;
alter table public.security_prices       enable row level security;
alter table public.transactions          enable row level security;
alter table public.transaction_revisions enable row level security;
alter table public.imports               enable row level security;
alter table public.import_rows           enable row level security;

-- profiles: read and update your own row (insert is done by the signup trigger)
create policy profiles_select_own on public.profiles
  for select to authenticated using (id = (select auth.uid()));
create policy profiles_update_own on public.profiles
  for update to authenticated
  using (id = (select auth.uid()))
  with check (id = (select auth.uid()));

-- securities: everyone sees the master list plus their own custom entries; users may only add custom ones
create policy securities_select on public.securities
  for select to authenticated
  using (created_by is null or created_by = (select auth.uid()));
create policy securities_insert_custom on public.securities
  for insert to authenticated
  with check (is_custom and created_by = (select auth.uid()));

-- security_prices: readable by any signed-in user; written only by the sync job (service role)
create policy security_prices_select on public.security_prices
  for select to authenticated using (true);

-- transactions: full read/insert/update on your own rows (soft delete is an update)
create policy transactions_select_own on public.transactions
  for select to authenticated using (user_id = (select auth.uid()));
create policy transactions_insert_own on public.transactions
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy transactions_update_own on public.transactions
  for update to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));

-- transaction_revisions: read-only for the owner
create policy transaction_revisions_select_own on public.transaction_revisions
  for select to authenticated using (user_id = (select auth.uid()));

-- imports and import_rows: own rows only
create policy imports_select_own on public.imports
  for select to authenticated using (user_id = (select auth.uid()));
create policy imports_insert_own on public.imports
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy imports_update_own on public.imports
  for update to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));

create policy import_rows_select_own on public.import_rows
  for select to authenticated using (user_id = (select auth.uid()));
create policy import_rows_insert_own on public.import_rows
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy import_rows_update_own on public.import_rows
  for update to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));
