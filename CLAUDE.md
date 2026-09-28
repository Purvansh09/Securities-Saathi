# CLAUDE.md — Portfolio Aggregator (minor project)

## Project
College minor project, topic T-2 (guide: Dr. Jigarkumar Shah): "Development of universal utility for analysis of investment and trading in listed securities on NSE/BSE".

Investors hold securities across many platforms and cannot see their total position in one place. We build a dashboard where a user records buys and sells (stocks, ETFs, mutual funds, bonds), either one by one or by uploading an Excel file, and then sees valuation, P/L, XIRR, statements and a tax classification.

The faculty guide confirmed the project does not need an ML component. Use AI only where it earns its place: parsing messy broker Excel files and writing plain-language explanations. Do not add ML for its own sake.

## Stack
- Backend: Python 3.11+, FastAPI, Pydantic v2, asyncpg with plain parameterized SQL (no ORM auto-migrations)
- Database, auth, file storage: Supabase (Postgres, Supabase Auth, Supabase Storage)
- Frontend: React + TypeScript + Vite, supabase-js (auth only), TanStack Query, Recharts
- AI: Claude API, only for the Excel-parsing fallback and plain-language explanations
- Deploy later: Render or Railway (backend), Vercel (frontend)

## Repo layout
```
CLAUDE.md
supabase/migrations/        # SQL migrations, the single source of truth for the schema
backend/
  app/
    main.py
    core/                   # config, auth (JWT verify), db pool
    api/v1/                 # routers: me, securities, transactions, portfolio, (later) imports, reports, tax
    services/               # ledger.py, fifo.py, valuation.py, securities_sync/
    schemas/                # Pydantic models
  tests/
frontend/
```

## Non-negotiable rules
1. **The ledger is the source of truth.** `transactions` holds every BUY and SELL. Holdings, average cost, P/L, XIRR and tax are derived from it. Never store a mutable "current holding" row, and never expose POST/PUT/PATCH on holdings.
2. **No hard deletes on user data.** DELETE /transactions sets `deleted_at`. PATCH edits the row, and a DB trigger snapshots the old row into `transaction_revisions`. Every query on `transactions` filters `deleted_at is null` unless it is an audit query.
3. **One `securities` table.** ISIN is the identifier in every table and API. Type-specific fields (bond coupon and maturity, MF scheme code and AMC, NSE and BSE symbols) go in the `metadata` jsonb column. Do not create per-asset-type tables.
4. **Money and quantity are `Decimal` / `numeric`, never float.** JSON carries them as strings. Round only at the presentation layer.
5. **Sell validation.** The running balance for a user + security must never go negative at any point in time. On every create, edit or delete of a transaction: take `pg_advisory_xact_lock(hashtextextended(user_id::text || security_id::text, 0))`, apply the change, call `ledger_balance_ok(user_id, security_id)`, and if it returns false roll back and return HTTP 422. This also catches back-dated edits that would invalidate later sells.
6. **FIFO lot matching** for realised P/L and holding period. Implement it as a pure function in `services/fifo.py` with unit tests: partial lot sales, multiple lots, and same-timestamp ordering (BUY before SELL).
7. **Excel imports never write to the ledger directly.** Upload creates an `imports` row and stores the file in Supabase Storage. Parsing fills `import_rows` (staging). The user reviews and fixes rows. `commit` moves confirmed rows into `transactions`. Commit must be idempotent (`transactions.import_row_id` is unique). Try rule-based pandas parsing first and call the LLM only for files the rules cannot map.
8. **Tax classification is a deterministic rule engine, not ML and not an LLM.** Holding-period thresholds, rates and exemptions live in a versioned config with effective dates, because they change. Verify the current rules on the Income Tax Department site before encoding them. The LLM may only turn the engine's output into plain language.
9. **Use XIRR, not CAGR,** for returns, because cash flows are irregular.
10. **Bonds are approximated.** Retail bonds are not priced continuously. Value them at face value plus accrued interest unless a price exists, and flag the value as `estimated` in API responses.
11. **Time.** Store `timestamptz` in UTC and display in IST. Financial years run 1 April to 31 March.
12. **Do not hand-roll auth.** Supabase Auth handles signup, login, refresh and password reset from the frontend. No security questions. The backend only verifies the JWT and reads `sub` as `user_id`.
13. **The backend's DB connection bypasses RLS,** so every query on a user table must be scoped by `user_id` from the verified JWT. Never take `user_id` from a request body. RLS stays enabled as defense in depth.
14. **Secrets** are never committed. Keep `.env` in `.gitignore` and maintain `.env.example`.

## Data model
Full DDL is in `supabase/migrations/20260928130000_phase1_schema.sql`, hardened by
`20260928134500_harden_functions.sql` (pins `search_path` on the SQL/PLpgSQL functions, revokes
direct RPC execution on the two trigger-only functions). Both are applied to the live "Securities
Saathi" project; the Supabase security advisor reports zero lints as of the second migration.

| Table / view | Purpose |
|---|---|
| `profiles` | One row per auth user, created by trigger on signup |
| `securities` | Master list of stocks, ETFs, MFs, bonds. Unique ISIN. `is_custom` rows are user-added and visible only to their creator |
| `security_prices` | Daily close price or NAV per security |
| `transactions` | The ledger. BUY/SELL, quantity, price, charges, `executed_at`, soft delete |
| `transaction_revisions` | Audit snapshots written by trigger on every update or soft delete |
| `imports`, `import_rows` | Excel upload tracking and staging rows (Phase 2 flow, tables exist now) |
| `latest_prices`, `v_holdings`, `v_holdings_valued` | Derived views. Cost basis of remaining lots still comes from FIFO in Python |

## API conventions
- Base path `/api/v1`. Bearer JWT on everything except the health check.
- Money and quantity as decimal strings. Timestamps as ISO 8601 UTC.
- Cursor or page pagination on every list endpoint from the start.
- Errors as `{ "detail": "...", "code": "SNAKE_CASE" }`. Use 422 for validation and ledger violations.
- File downloads stream, they do not return JSON.

Phase 1 endpoints:
- `GET/PATCH /me`
- `GET /securities?q=&type=&limit=&cursor=`, `GET /securities/{isin}`, `GET /securities/{isin}/price`, `GET /securities/{isin}/history?from=&to=`
- `POST/GET /transactions`, `GET/PATCH/DELETE /transactions/{id}` (DELETE is a soft delete)
- `GET /portfolio/summary`, `GET /portfolio/holdings?type=`, `GET /portfolio/holdings/{isin}`, `GET /portfolio/allocation?by=asset_class|sector`
- Internal, protected by a secret header: `POST /internal/sync-securities`, `POST /internal/refresh-prices`

Later phases: `/imports/*`, `/reports/*`, `/analysis/xirr`, `/tax/*`.

## Securities sync
Daily job merges these sources into `securities`, keyed by ISIN. URLs and file formats change often, so verify each one on the publisher's site before coding against it, and make the sync fail loudly if expected columns disappear.
- NSE equity list CSV (`EQUITY_L.csv` on nsearchives.nseindia.com). Needs a browser-like User-Agent header. Do not scrape the live NSE pages.
- BSE "List of Scrips" download (also gives industry, used as sector)
- ETFs: NSE ETF list plus BSE scrip list
- Mutual funds: AMFI `NAVAll.txt`. Each ISIN is its own security (growth and dividend options differ). Store the scheme code in `metadata`. Skip rows with no ISIN
- Bonds: NSE/BSE debt segment lists, plus user-added custom securities
- Prices: exchange bhavcopy or yfinance (`SYMBOL.NS`) for stocks and ETFs, AMFI NAV for MFs

Rules: dedupe on ISIN (a stock on both exchanges is one row, both symbols in `metadata`). Mark securities that vanish from the lists `is_active = false`, never delete them. If a sync inserts an ISIN that exists as a custom security, promote it (`is_custom = false`, `created_by = null`).

## Phase 1 scope and build order
1. ✅ Scaffold repo and config, create the Supabase project, apply the migration
2. ✅ Auth: JWT verification dependency, `GET/PATCH /me`
3. Securities sync (NSE, BSE, AMFI) and the securities search endpoints
   - Sync job: **not done, can't run from this sandbox** -- its egress is allowlisted to package
     registries and GitHub only; NSE/BSE/AMFI/Yahoo Finance are all outside it (confirmed
     directly). Stood in for now with `supabase/seed.sql`: 49 real, individually-verified
     securities (42 Nifty 50 stocks from NSE's own constituent CSV, 4 ETFs, 2 MFs, 1 bond).
     The real sync needs to run somewhere with normal internet -- your machine, or a GitHub
     Actions cron -- and should upsert on ISIN so it merges cleanly with the seed data.
   - ✅ Search endpoints (`app/api/v1/securities.py`): `GET /securities` (search by name/
     symbol/ISIN prefix, type filter, cursor pagination), `GET /securities/{isin}`,
     `GET /securities/{isin}/price`, `GET /securities/{isin}/history`. 14 tests against a
     fake connection, plus the actual search/filter/pagination SQL verified against the
     live seeded data (ticker match, name substring, ISIN prefix, type filter, and
     pagination landing exactly on all 49 rows).
4. Price refresh job, price and history endpoints (history endpoint exists; job doesn't --
   same network problem as the sync, tackle together)
5. Transaction CRUD with ledger validation (rule 5)
6. Portfolio: FIFO service, summary, holdings, allocation
7. Frontend: login, dashboard with totals by asset class, security search, add-transaction form, holdings table

Out of scope for Phase 1: Excel import, statements, XIRR, tax, risk score.

Later: Phase 2 is Excel upload with review step, downloadable P/L and holding statements, XIRR. Phase 3 is tax classifier plus LLM summary, dashboard polish, optional portfolio risk score.

Done when: migrations apply cleanly to a fresh Supabase project, tests pass, and a user can sign up, find a security, add BUY and SELL entries, and see holdings with total and per-asset-class valuation. A SELL larger than the holding is rejected, and a soft-deleted transaction disappears from holdings but appears in `transaction_revisions`.

## Working agreements
- Read this file first. Ask before deviating from the rules above.
- Change the schema only through new migration files. Never edit an applied migration.
- Write tests with the code and run them before calling anything done. FIFO, ledger validation and (later) XIRR need unit tests.
- Small commits, one concern each.
- Add short comments explaining non-obvious design choices. The author has to defend this project in a viva.
- Do not add dependencies without a reason.
- Update this file when a decision changes.

## Commands
```
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp backend/.env.example backend/.env   # then fill in the real secrets, see comments in that file
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload
cd backend && source .venv/bin/activate && pytest -q
```
New migrations are applied with the Supabase MCP `apply_migration` tool (project: Securities Saathi,
id `vmonemnomwyjwacagkuc`, region ap-south-1, org "purvansh playground") rather than the Supabase CLI,
since this was built without local Docker. Keep the SQL file in `supabase/migrations/` in sync with
whatever gets applied -- that file is the record of the schema, the live project is just where it runs.
