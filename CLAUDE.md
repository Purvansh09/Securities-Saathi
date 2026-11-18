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
- ~~BSE "List of Scrips" download~~ -- **blocked** (403 from BSE's bot protection even with
  browser headers, checked 2026-09-29). Replaced by: BSE bhavcopy (UDiFF CSV) for BSE symbols/codes
  and BSE-only equities (ISIN security-type `01` only, so NCDs/bonds stay out), and NSE's
  `ind_nifty500list.csv` "Industry" column for sector (top 500 companies; others Unclassified).
- ETFs: NSE `eq_etfseclist.csv`. An ETF is also an AMFI scheme; it stays type ETF, AMFI adds its scheme code/AMC to metadata
- Mutual funds: AMFI `NAVAll.txt`. Each ISIN is its own security (growth and dividend options differ). Store the scheme code in `metadata`. Skip rows with no ISIN.
  Format as of 2026-09: 8 `;` columns, now with separate **Plan** and **Option** columns
- Bonds: not synced yet (seed + user-added custom securities). NSE/BSE debt lists are a later addition
- Prices: NSE bhavcopy (UDiFF zip), then BSE bhavcopy for what NSE lacks, AMFI NAV for MFs only (ETFs use market price). No yfinance needed

Rules: dedupe on ISIN (a stock on both exchanges is one row, both symbols in `metadata`). Mark securities that vanish from the lists `is_active = false`, never delete them. If a sync inserts an ISIN that exists as a custom security, promote it (`is_custom = false`, `created_by = null`).

Implementation: `backend/app/services/securities_sync/` (sources -> merge -> store, orchestrated by
jobs). Safety: missing columns raise `SourceFormatError`; core sources below a row floor abort
before writing; a run that would deactivate > 5% of synced rows rolls back. Deactivation only
applies to NSE/AMFI rows of types STOCK/ETF/MF (BSE-only rows come from a daily trade file, and
bonds aren't synced). Metadata is merged (`||`), never replaced; sector is only overwritten with a
non-null value; existing ETF names are kept. Hand-entered (`MANUAL`) prices are never overwritten.
Price history is pruned to 7 days for securities with no transactions (about 20k prices/day
would fill the free tier's 500 MB in about a year), but a security's latest price is never pruned.

## Phase 1 scope and build order
1. ✅ Scaffold repo and config, create the Supabase project, apply the migration
2. ✅ Auth: JWT verification dependency, `GET/PATCH /me`
   - The project signs user tokens with **asymmetric ES256 keys**, not the legacy shared secret.
     `core/auth.py` routes on the token header: ES256/RS256 are verified against the public key
     from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` (matched by `kid`, cached 10 min, sent
     with `SUPABASE_PUBLISHABLE_KEY` as `apikey`); HS256 is verified only against
     `SUPABASE_JWT_SECRET` (old tokens). Anything else, including `alg: none`, is 401. HS256 is
     never checked against a public key, which blocks algorithm-confusion forgery. A JWKS fetch
     failure returns 503, not 401. Needs `cryptography`. Tests cover each case.
   - `SUPABASE_URL` must be the bare project URL, with no `/rest/v1/` suffix.
3. ✅ Securities sync (NSE, BSE, AMFI) and the securities search endpoints
   - Sync job: see "Securities sync" above. Run: `python -m app.services.securities_sync sync`
     or `POST /api/v1/internal/sync-securities` with `X-Internal-Secret`. First live run
     (2026-09-29): 22,743 securities (2,587 NSE stocks, 1,985 BSE-only stocks, 351 ETFs,
     17,820 MF ISINs), merged over the 49 seed rows in 10 s; re-run inserts 0, deactivates 0.
     The seed (`supabase/seed.sql`) stays as a fallback for a fresh project.
   - **Scheduled (temporary): Windows Task Scheduler** on the dev laptop, task
     "Securities Saathi - daily sync", Mon-Fri 8:00 pm IST (after exchange/AMFI files publish),
     runs `backend/scripts/run_daily_sync.ps1` (sync + prices, logs to `backend/logs/`, keeps 30,
     exits non-zero on failure -> Task Scheduler "Last Run Result"). Catches up at next logon if
     the laptop was off; allowed on battery; needs network. Check it with
     `Get-ScheduledTaskInfo -TaskName "Securities Saathi - daily sync"`.
   - **Planned: move to a GitHub Actions cron** (`30 14 * * 1-5` = 8 pm IST) running
     `python -m app.services.securities_sync all` with `DATABASE_URL` as a repo secret. Needs the
     repo pushed to GitHub first. Unregister the Windows task once that's running, so the two
     don't both run.
   - ✅ Search endpoints (`app/api/v1/securities.py`): `GET /securities` (search by name/
     symbol/ISIN prefix, type filter, cursor pagination), `GET /securities/{isin}`,
     `GET /securities/{isin}/price`, `GET /securities/{isin}/history`. 14 tests against a
     fake connection, plus the actual search/filter/pagination SQL verified against the
     live seeded data (ticker match, name substring, ISIN prefix, type filter, and
     pagination landing exactly on all 49 rows).
4. ✅ Price refresh job, price and history endpoints
   - `python -m app.services.securities_sync prices [--date YYYY-MM-DD]` or
     `POST /api/v1/internal/refresh-prices`. Walks back over weekends/holidays to the latest
     bhavcopy. First run: 22,736 prices (NSE 2,939, BSE 2,003, AMFI 17,794); every active MF has
     a price; 1,126 bhavcopy rows unmatched (bonds/SGBs, not in the master). A first version
     pruned stale-but-latest NAVs; fixed, and the prune now always keeps each latest price.
   - 14 unit tests (`tests/test_securities_sync.py`) on fixtures cut from the real files:
     column checks, AMFI sections/options/N.A. NAVs, ISIN type filter, merge precedence, price
     precedence (NSE EQ series > NSE > BSE > AMFI), row floors, endpoint secret.
5. ✅ Transaction CRUD with ledger validation (rule 5)
   - `app/api/v1/transactions.py`: `POST/GET/PATCH/DELETE /transactions`, `GET /transactions/{id}`.
     Every write goes through `services/ledger.py::run_guarded` (advisory lock scoped to
     `(user_id, security_id)`, apply the change, check `ledger_balance_ok`, 422 on violation --
     the lock+check wrap the whole DB transaction so a failed check rolls back the write too).
     PATCH only allows correcting quantity/price/charges/executed_at/notes, never `txn_type`
     or `isin` -- changing what was bought/sold is a new transaction. DELETE is a soft delete
     (rule 2). 19 tests against a fake connection, plus the real SQL text (insert...returning,
     the joined list/get select, the dynamic-SET update, the soft-delete update) verified
     against the live database: a real oversell and a real delete-of-a-depended-on-BUY both
     correctly flipped `ledger_balance_ok` to false, and a legitimate delete correctly set
     `deleted_at` and left the ledger valid. All test data cleaned up afterward.
6. ✅ Portfolio: FIFO service, summary, holdings, allocation
   - `services/fifo.py` (pure): matches SELLs against the oldest BUY lots, same ordering as
     `ledger_balance_ok`. Buy charges go into lot cost; sell charges reduce proceeds; partial
     splits are pro rata with the last slice taking the remainder, quantized to 10 dp
     internally so later sums stay exact (at 28-digit default precision, 31/3 x 3 came back
     as 30.999...). Holding days use IST dates (rule 11). 17 tests.
   - `services/valuation.py` (pure, valuation date passed in): holdings with no price yet are
     carried at cost (market_value null, counted in `unpriced_holdings`) instead of dropping
     out of totals. Bonds without a price are face value + accrued interest, 30/360,
     coupon dates stepped back from maturity (semi-annual default), flagged `estimated`
     (rule 10). Face value defaults to 100; `face_value`/`coupon_frequency` in metadata
     override. 21 tests.
   - `app/api/v1/portfolio.py`: `GET /portfolio/summary`, `/holdings?type=` (sorted by value,
     weights always relative to the whole portfolio), `/holdings/{isin}` (open lots +
     realised matches, 404 if never traded), `/allocation?by=asset_class|sector` (missing
     sector -> "Unclassified"). One ledger query per request; nothing is stored. Outputs are
     formatted to 6 dp (the DB's scale); display rounding stays in the frontend. 16 tests,
     plus the real query run on the live DB (throwaway user, 4 securities, a soft-deleted
     row that must be ignored, two prices for one security) and its rows fed through the
     pipeline: every total matched the hand-computed figures. Test data cleaned up.
   - Fixed alongside: the asyncpg pool now registers json/jsonb codecs (`core/db.py`).
     Without them jsonb came back as str, which would have broken `metadata` on
     `GET /securities/{isin}` and `preferences` on `/me` against the real DB.
7. Frontend: login, dashboard with totals by asset class, security search, add-transaction form, holdings table
   - Built in `frontend/` (Vite 8, React 19, TS, TanStack Query 5, Recharts 3, react-router 7 for the
     four pages). supabase-js is used for auth only; every data call goes to the FastAPI backend
     with the session's JWT (`src/lib/api.ts`). Env in `frontend/.env.local` (`.env.example`
     documents it; everything `VITE_*` is public, so no secrets there).
   - Pages: sign in / sign up / forgot + reset password; Dashboard (summary stats, allocation
     donut by asset class or sector, totals-by-asset-class table); Holdings (type filter, click
     through to open lots and FIFO matches); Transactions (add form with security search,
     last-price prefill and "you hold N" on sells; ledger with soft delete and load-more).
   - Decimal strings are sent as typed and only turned into numbers for display
     (`src/lib/format.ts`). The trade time input is read as IST and sent as UTC. The backend
     remains the only validator of the ledger; its 422 message is shown as-is.
   - Verified: tsc clean, 23 vitest tests (IST conversion, decimal input, error flattening),
     production build, sign-in page renders with no console errors, CORS from :5173 to the API
     works, no horizontal scroll at 375 px. Signed-in pages not yet exercised with a real account.

Out of scope for Phase 1: Excel import, statements, XIRR, tax, risk score.

## Phase 2 progress
User's choices (2026-09-29): import rules for Zerodha, Groww, Upstox/Angel One; LLM column-mapping
fallback built now; statements as Excel + PDF. Build order: XIRR -> imports -> statements.

1. ✅ XIRR (rule 9) -- `services/xirr.py`: Decimal-only bisection on NPV(r) = 0 with Excel's
   actual/365 convention (bisection can't diverge; widens the bracket to 1,000,000x, returns None
   when undefined: one-sided flows, everything on one day, or off the scale). BUY = -(qty x price
   + charges), SELL = qty x price - charges, dated by IST day; open holdings add one final inflow
   at today's value (cost when unpriced, same as the dashboard, flagged `includes_unpriced`).
   `GET /analysis/xirr` gives portfolio + per security (closed positions included), with
   `short_period` (< 365 days) so the UI can warn that annualising a few weeks exaggerates.
   Tests: Microsoft's documented XIRR example (agrees to Excel's 1e-8 precision, and our root
   has the smaller NPV), closed-form cases, leap year, undefined cases, and the endpoint against
   an independent float solver. UI: XIRR card on the dashboard, XIRR column on holdings.
2. ✅ Excel import (rule 7) -- run end to end by the user on 2026-10-06 (upload -> review ->
   commit). Fixed on the way: `window.confirm()` is silently cancelled in some embedded browsers
   (the Claude app's pane returns false in 1 ms), so all confirmations now use
   `components/ConfirmButton.tsx` (two-step in-page confirm). LLM fallback verified live (2026-10-06): an unrecognisable file
   ("Scrip / Ticker", "Txn Dt", "Dir.", 2-line preamble) mapped correctly in ~6 s; that run
   also showed "Sale" was missing from the sell words (added, with tests).
   - Storage: bucket `imports` (private, 5 MB, csv/xlsx) via migration `20261006100000`, RLS on
     storage.objects: own `<user_id>/` folder only. The backend reads/writes with the *caller's*
     JWT, no service-role key. Migration `20261006110000` adds `imports.profile`.
   - `services/imports/`: `table.py` (read csv/xlsx to a string grid, header row = the row in the
     first 40 matching most aliases, one alias table covers all brokers; profiles only label the
     broker), `rows.py` (day-first dates, Excel serials, Indian number formats, value/qty when no
     price, missing time -> 15:30 IST, cancelled orders and F&O auto-excluded, plain-English
     issues), `llm.py` (Claude Opus 5.5 at low effort, structured output via `messages.parse`;
     only the first 15 rows are sent; it returns column *names*, which are checked against the
     real header before use; rows are then parsed by the same code; the UI discloses this),
     `service.py` (security resolution ISIN > symbol/BSE code > exact name, preferring active and
     NSE; duplicate-of-ledger rows get a non-blocking "Warning: " issue and need an explicit
     confirm; commit locks every affected (user, security) in a fixed order, inserts with
     `on conflict (import_row_id) do nothing`, runs `ledger_balance_ok` for each, rolls the whole
     import back on any oversell).
   - API `api/v1/imports.py`: POST (multipart), GET list/detail, PATCH row, POST commit
     (idempotent), DELETE (soft discard). Same file twice -> 409 (server-side sha256).
   - Verified: 20 parsing tests (Zerodha with preamble, Groww value-not-price + cancelled,
     Upstox split date/time + total row, real .xlsx, invented LLM column names dropped); commit
     SQL run on the live DB inside a rolled-back transaction (resolve, idempotent re-commit,
     duplicate warning, oversell blocked; ledger unchanged afterwards).
   - UI: Import page (drop zone, history) and review page (needs attention / ready / excluded,
     inline row editor with security picker, include/exclude, sticky commit bar).
3. ✅ Statements (Excel + PDF) -- `services/statements.py` lays out a `Statement` (summary +
   tables + notes) as .xlsx (openpyxl, real numbers, Indian digit grouping) or PDF (fpdf2, A4
   landscape, Helvetica so amounts read "Rs"). `api/v1/reports.py`: `GET /reports/holdings` and
   `GET /reports/realised?fy=` with `format=xlsx|pdf`, streamed with Content-Disposition. Built from
   the same FIFO/valuation/XIRR/tax functions as the screens. Download buttons on Holdings and Tax
   (fetch with the bearer token -> blob, since a plain link can't send it).

## Phase 3 progress
1. ✅ Tax classifier (rule 8) -- `services/tax/`: `rules.py` (dated RuleSets; pick by *sale* date:
   before / from 23 Jul 2024; s.50AA cut-off 1 Apr 2023; grandfathering cut-off 1 Feb 2018; 4% cess),
   `engine.py` (classify security: stock/equity ETF/equity-oriented MF by AMFI category -> EQUITY;
   gold/silver/international ETF -> OTHER listed; debt ETF/debt MF -> DEBT_FUND; bonds by listing;
   uncertain categories flagged for the user. Classify lot: "held more than N months" = sold after
   purchase + N months; equity 111A/112A rates, other LTCG s.112, other STCG at slab. Year summary:
   net per bucket, short-term losses set off against slab gains first, then higher flat rates,
   then other LTCG, equity LTCG last; long-term losses only against LTCG; then the s.112A
   exemption; estimated tax + cess; slab gains reported, not taxed; losses carried shown.)
   Not modelled, and said so to the user: slab tax, surcharge, pre-2024 indexation, the 31-Jan-2018
   grandfathered cost, STT non-deductibility. Rules verified on incometaxindia.gov.in on 2026-10-07
   (pages state "as amended by the Finance Act, 2026"; the Income-tax Act 2025 renumbers sections
   but keeps these rates for 2026-27). `GET /tax/capital-gains?fy=`. 26 engine tests (incl. the
   department's own SBI-shares illustrations) + endpoint/statement tests. UI: Tax page.
2. LLM plain-language summary of the tax output -- not started.
3. Dashboard polish, optional risk score -- not started.

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
cd frontend && npm install && cp .env.example .env.local   # then fill in the publishable key
cd frontend && npm run dev        # http://localhost:5173, needs the backend on :8000
cd frontend && npm test && npm run build
```
New migrations are applied with the Supabase MCP `apply_migration` tool (project: Securities Saathi,
id `vmonemnomwyjwacagkuc`, region ap-south-1, org "purvansh playground") rather than the Supabase CLI,
since this was built without local Docker. Keep the SQL file in `supabase/migrations/` in sync with
whatever gets applied -- that file is the record of the schema, the live project is just where it runs.
