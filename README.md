# Securities Saathi

**One portfolio view for every Indian investment you own.** Investors hold stocks, ETFs, mutual
funds and bonds across many brokers and apps, and none of them shows the whole picture. Securities
Saathi lets you record buys and sells by hand or import your broker's trade file, then shows your
total holdings, profit and loss, XIRR, capital-gains tax, and downloadable statements, all in one
place.

Built as a college minor project, topic T-2: *"Development of universal utility for analysis of
investment and trading in listed securities on NSE/BSE"*.

---

## Features

| | |
|---|---|
| **Portfolio dashboard** | Current value, invested amount, unrealised and realised P/L, XIRR, and allocation by asset class or sector |
| **Holdings** | Every position with average cost, latest price, P/L, XIRR and weight; click through to the open lots and FIFO sale matches |
| **Transactions** | Add a buy or sell with security search; selling more than you hold is rejected; deletes are soft and audited |
| **Excel / CSV import** | Upload a tradebook from Zerodha, Groww, Upstox or Angel One, review every row, fix problems, then commit. Unrecognised layouts are mapped by Claude (AI) |
| **XIRR** | Annualised return that accounts for when each rupee went in and came out, for the whole portfolio and each holding |
| **Capital-gains tax** | Each sale classified as short or long term, taxed under the right rule, with loss set-off, the ₹1.25 lakh exemption and an estimated tax for each financial year |
| **Statements** | Holdings and capital-gains statements as formatted Excel or printable PDF |
| **Daily market data** | About 22,700 NSE/BSE stocks, ETFs and mutual funds, with closing prices and NAVs refreshed every trading day |

---

## Tech stack

- **Backend:** Python 3.12, FastAPI, Pydantic v2, asyncpg with plain parameterised SQL (no ORM)
- **Database, auth, files:** Supabase (Postgres, Supabase Auth, Supabase Storage)
- **Frontend:** React 19, TypeScript, Vite, TanStack Query, Recharts
- **AI:** Claude API, used only to map the columns of unrecognised import files
- **Files:** pandas + openpyxl to read Excel, openpyxl and fpdf2 to write the statements
- **Market data:** NSE and BSE bhavcopy files and the NSE equity/ETF lists, plus AMFI `NAVAll.txt`

```
 React (Vite)  ──JWT──▶  FastAPI  ──asyncpg──▶  Supabase Postgres
      │                     │  └────────────▶  Supabase Storage (uploaded files)
      └── Supabase Auth     ├──────────────▶  Claude API (import fallback only)
          (login only)      └── daily job ──▶  NSE / BSE / AMFI public files
```

---

## Design decisions

These are the rules the whole project is built around (the full list is in [`CLAUDE.md`](CLAUDE.md)).

1. **The ledger is the source of truth.** Only buys and sells are stored. Holdings, average cost,
   P/L, XIRR and tax are all *derived* from them on every request, so they can never drift out
   of sync. There is no way to edit a holding directly.
2. **Nothing is ever hard-deleted.** Deleting a transaction sets `deleted_at`, and a database
   trigger copies every edit and delete into `transaction_revisions`, a full audit trail.
3. **Money is never a float.** All amounts and quantities are `Decimal` in Python and `numeric`
   in Postgres, and travel as strings in JSON. Rounding happens only when displaying.
4. **You can't sell what you don't own.** Every write takes a per-holding advisory lock and
   re-checks the running balance over time. A sale that would ever leave a holding negative,
   including one exposed by a back-dated edit, is rejected.
5. **FIFO lot matching.** Sells are matched to the oldest buys first. That gives realised P/L,
   holding periods and the short- or long-term split for tax.
6. **XIRR, not CAGR,** because real investors add and withdraw money at irregular dates.
7. **Imports never write straight to the ledger.** Upload, then staging rows, then your review,
   then commit. Committing twice can't create duplicates.
8. **Tax is a fixed-rule engine, not ML or an LLM.** Rates and holding periods live in a dated
   config ([`backend/app/services/tax/rules.py`](backend/app/services/tax/rules.py)) checked against
   the Income Tax Department's published guidance. The AI never calculates a number.
9. **Times are stored in UTC and shown in IST;** financial years run from 1 April to 31 March.

---

## Repository layout

```
backend/
  app/
    api/v1/            routers: me, securities, transactions, portfolio, analysis (XIRR),
                       imports, tax, reports (statements), internal (daily jobs)
    core/              config, JWT verification (ES256 via JWKS + legacy HS256), DB pool
    services/          ledger, fifo, valuation, xirr, statements,
                       imports/ (parsing, LLM fallback, staging, commit),
                       tax/ (rules, engine), securities_sync/ (daily NSE/BSE/AMFI job)
    schemas/           Pydantic models
  scripts/             run_daily_sync.ps1 (Windows Task Scheduler wrapper)
  tests/               pytest suite
frontend/              React + TypeScript app
supabase/migrations/   SQL migrations, the single source of truth for the schema
supabase/seed.sql      a small verified seed list of securities
samples/               sample broker tradebooks for trying the import
CLAUDE.md              project spec, rules and build log
```

---

## Running it locally

### Prerequisites
- Python 3.11 or newer, and Node.js 20 or newer
- A [Supabase](https://supabase.com) project
- *(Optional)* an [Anthropic API key](https://console.anthropic.com) for the AI import fallback

### 1. Database
Apply the SQL files in `supabase/migrations/` to your Supabase project **in filename order**
(SQL editor or the Supabase CLI). Optionally run `supabase/seed.sql`.

In the Supabase dashboard, under **Authentication → URL Configuration**, set the Site URL and a
redirect URL to `http://localhost:5173`.

### 2. Backend
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
```
Fill in `backend/.env` (each variable is explained in `.env.example`):

| Variable | Where to find it |
|---|---|
| `SUPABASE_URL` | Project Settings → Data API (the bare URL, no `/rest/v1/`) |
| `SUPABASE_JWT_SECRET` | Project Settings → Data API → JWT Settings (legacy secret) |
| `SUPABASE_PUBLISHABLE_KEY` | Project Settings → API Keys (`sb_publishable_…`) |
| `DATABASE_URL` | **Connect** button → Session pooler URI (port 5432) |
| `INTERNAL_SYNC_SECRET` | Make one up: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ANTHROPIC_API_KEY` | Optional; only needed for the AI import fallback |

Start the API:
```bash
uvicorn app.main:app --reload --port 8000
```
Interactive API docs are then at http://localhost:8000/docs.

### 3. Load market data (first run)
```bash
python -m app.services.securities_sync all
```
This downloads the NSE/BSE/AMFI lists and the latest prices (about 15 seconds). Run it daily after
market close to keep prices fresh; on Windows, `backend/scripts/run_daily_sync.ps1` is ready for
Task Scheduler.

### 4. Frontend
```bash
cd frontend
npm install
copy .env.example .env.local    # macOS/Linux: cp .env.example .env.local
npm run dev
```
Set `VITE_SUPABASE_PUBLISHABLE_KEY` in `frontend/.env.local`, and set `VITE_API_URL` if the API
isn't on port 8000. Then open http://localhost:5173, create an account, and try importing one of
the files in `samples/`.

### Tests
```bash
cd backend && python -m pytest -q
cd frontend && npm test
```

---

## API overview

All endpoints are under `/api/v1` and need a Supabase JWT (`Authorization: Bearer …`), except
`/health`. Amounts are decimal strings.

| Area | Endpoints |
|---|---|
| Profile | `GET/PATCH /me` |
| Securities | `GET /securities?q=`, `GET /securities/{isin}`, `/{isin}/price`, `/{isin}/history` |
| Transactions | `POST/GET /transactions`, `GET/PATCH/DELETE /transactions/{id}` |
| Portfolio | `GET /portfolio/summary`, `/holdings`, `/holdings/{isin}`, `/allocation?by=` |
| Returns | `GET /analysis/xirr` |
| Import | `POST/GET /imports`, `GET /imports/{id}`, `PATCH /imports/{id}/rows/{row}`, `POST /imports/{id}/commit`, `DELETE /imports/{id}` |
| Tax | `GET /tax/capital-gains?fy=2026-27` |
| Statements | `GET /reports/holdings?format=xlsx\|pdf`, `GET /reports/realised?fy=&format=xlsx\|pdf` |
| Daily jobs | `POST /internal/sync-securities`, `POST /internal/refresh-prices` (needs `X-Internal-Secret`) |

---

## Tax disclaimer

The capital-gains figures are an **estimate to help you plan, not tax advice.** They follow the
Income Tax Department's published rules (last checked October 2026), and the app states on screen
what it does not model: tax on slab-rate gains, surcharge, indexation for older non-equity sales,
the 31 January 2018 grandfathered cost, and non-deductible STT. Check your figures with a tax
professional before filing.

---

## License

[MIT](LICENSE) © 2026 Purvansh09
