"""Supabase Storage access for uploaded broker files.

Calls are made with the *caller's own* JWT, never a service-role key, so Storage's row level
security (migration 20261006100000_imports_storage.sql) applies: a user can only write or read
objects under their own "<user_id>/" folder. That keeps the backend free of any credential that
could read everyone's files.
"""

import ssl
from urllib.parse import quote

import httpx

from app.core.config import get_settings

BUCKET = "imports"


class StorageError(Exception):
    pass


def _client(token: str) -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        base_url=f"{settings.supabase_url.rstrip('/')}/storage/v1",
        headers={"Authorization": f"Bearer {token}", "apikey": settings.supabase_publishable_key},
        timeout=httpx.Timeout(30.0),
        verify=ssl.create_default_context(),  # OS trust store (see securities_sync/fetch.py)
    )


async def upload(token: str, path: str, content: bytes, content_type: str) -> None:
    async with _client(token) as client:
        resp = await client.post(
            f"/object/{BUCKET}/{quote(path)}",
            content=content,
            headers={"Content-Type": content_type, "x-upsert": "false"},
        )
    if resp.status_code >= 400:
        raise StorageError(f"Storage upload failed ({resp.status_code}): {resp.text[:200]}")


async def download(token: str, path: str) -> bytes:
    async with _client(token) as client:
        resp = await client.get(f"/object/authenticated/{BUCKET}/{quote(path)}")
    if resp.status_code >= 400:
        raise StorageError(f"Storage download failed ({resp.status_code})")
    return resp.content
