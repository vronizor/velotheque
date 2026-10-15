from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, select

from velotheque.tables import backup_status

STALE_AFTER = timedelta(hours=48)


def record(conn: Connection, ok: bool, error: str | None = None, details: dict | None = None):
    conn.execute(
        backup_status.insert().values(
            ok=ok, error=None if ok else (error or "unknown error")[:2000], details=details or {}
        )
    )


@dataclass
class BackupState:
    last_success: datetime | None
    last_run: object | None

    @property
    def age(self) -> timedelta | None:
        return datetime.now(UTC) - self.last_success if self.last_success else None

    @property
    def stale(self) -> bool:
        return self.age is None or self.age > STALE_AFTER

    @property
    def last_failed(self) -> bool:
        return self.last_run is not None and not self.last_run.ok


def state(conn: Connection) -> BackupState:
    last_success = conn.execute(
        select(backup_status.c.finished_at)
        .where(backup_status.c.ok)
        .order_by(backup_status.c.finished_at.desc())
        .limit(1)
    ).scalar()
    last_run = conn.execute(
        select(backup_status).order_by(backup_status.c.finished_at.desc()).limit(1)
    ).first()
    return BackupState(last_success, last_run)


def history(conn: Connection, limit: int = 20):
    return conn.execute(
        select(backup_status).order_by(backup_status.c.finished_at.desc()).limit(limit)
    ).all()
