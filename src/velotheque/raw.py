import hashlib
import json

from sqlalchemy import Connection, Table, and_, literal, select


def payload_hash(payload) -> bytes:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).digest()


def store_raw(conn: Connection, table: Table, payload, key: dict, extra: dict | None = None):
    """Append a raw payload unless it is identical to the latest one for the same key."""
    digest = payload_hash(payload)
    latest = (
        select(table.c.payload_sha256)
        .where(and_(*(table.c[name] == value for name, value in key.items())))
        .order_by(table.c.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    row = {**key, **(extra or {}), "payload": payload, "payload_sha256": digest}
    source = select(*(literal(v, table.c[k].type) for k, v in row.items())).where(
        latest.is_distinct_from(digest)
    )
    inserted = conn.execute(table.insert().from_select(list(row), source).returning(table.c.id))
    return inserted.first() is not None
