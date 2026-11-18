"""Daily securities-master sync and price refresh (CLAUDE.md "Securities sync").

Split so the rules can be tested without a network or database:
  sources.py -- parse each publisher's file (pure; fails loudly on format changes)
  merge.py   -- one row per ISIN, one price per (ISIN, date) (pure)
  fetch.py   -- downloads
  store.py   -- bulk upserts, deactivation, pruning
  jobs.py    -- the two jobs that wire these together

Run from backend/:  python -m app.services.securities_sync [sync|prices|all] [--date YYYY-MM-DD]
or through POST /api/v1/internal/sync-securities and /internal/refresh-prices.
"""
