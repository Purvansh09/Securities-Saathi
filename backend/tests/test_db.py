"""The pool's per-connection init must register json/jsonb codecs; without them asyncpg
returns jsonb as str and rejects dict parameters (see core/db.py)."""

import json

from app.core.db import init_connection


class _RecordingConn:
    def __init__(self):
        self.codecs = {}

    async def set_type_codec(self, type_name, *, encoder, decoder, schema):
        self.codecs[type_name] = (encoder, decoder, schema)


async def test_init_connection_registers_json_and_jsonb_codecs():
    conn = _RecordingConn()

    await init_connection(conn)

    assert set(conn.codecs) == {"json", "jsonb"}
    encoder, decoder, schema = conn.codecs["jsonb"]
    assert schema == "pg_catalog"
    assert decoder('{"coupon_pct": 7.18}') == {"coupon_pct": 7.18}
    assert json.loads(encoder({"theme": "dark"})) == {"theme": "dark"}
