"""Test env setup.

Settings (app/core/config.py) has no defaults for these -- they're meant to
come from a real .env in dev/prod. Setting them here, before any test module
imports app.main, lets the whole suite run without a real Supabase project
or database. Values are deliberately fake; nothing here is a real secret.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
os.environ.setdefault("INTERNAL_SYNC_SECRET", "test-internal-secret")
