"""PostgreSQL persistence for the standalone Brain4All cron service."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from threading import Lock
from typing import Any, Callable, Mapping

from .files import StoreError


TABLE = "brain4all_cron_jobs"


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


class PostgresCronRepository:
    """Store cron definitions in a dedicated, namespaced PostgreSQL table."""

    def __init__(self, config: Mapping[str, Any], connect: Callable[..., Any] | None = None):
        self.config = dict(config)
        self._connect_override = connect
        self._schema_ready = False
        self._schema_lock = Lock()

    @classmethod
    def from_environment(cls) -> "PostgresCronRepository | None":
        host = os.getenv("BRAIN4ALL_CRON_POSTGRES_HOST", "").strip()
        if not host:
            return None
        try:
            port = int(os.getenv("BRAIN4ALL_CRON_POSTGRES_PORT", "5432"))
        except ValueError as error:
            raise StoreError("cron PostgreSQL port is invalid", code="invalid_cron_store") from error
        return cls({
            "host": host,
            "port": port,
            "user": os.getenv("BRAIN4ALL_CRON_POSTGRES_USER", "postgres"),
            "password": os.getenv("BRAIN4ALL_CRON_POSTGRES_PASSWORD", ""),
            "dbname": os.getenv("BRAIN4ALL_CRON_POSTGRES_DB", "postgres"),
            "connect_timeout": 5,
        })

    def list_crons(self) -> list[dict[str, Any]]:
        self._ensure_schema()
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT * FROM {TABLE} ORDER BY created_at")
                return [self._job(row) for row in cursor.fetchall()]
        except Exception as error:
            raise self._unavailable(error) from error

    def put_cron(self, item: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_schema()
        job = dict(item)
        values = (
            job["id"], job["agent_id"], job["name"], job["prompt"], job["schedule"],
            job.get("timezone") or "Etc/UTC", bool(job.get("enabled")),
            job.get("next_run_at"), job.get("last_run_at"), job.get("last_run_id"),
            job.get("last_run_status"), job.get("last_run_completed_at"),
            job.get("last_output"), job.get("last_error"), job["created_at"],
            job["updated_at"], int(job.get("version") or 1),
        )
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {TABLE} (
                        id, agent_id, name, prompt, schedule, timezone, enabled,
                        next_run_at, last_run_at, last_run_id, last_run_status,
                        last_run_completed_at, last_output, last_error,
                        created_at, updated_at, version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        agent_id = EXCLUDED.agent_id,
                        name = EXCLUDED.name,
                        prompt = EXCLUDED.prompt,
                        schedule = EXCLUDED.schedule,
                        timezone = EXCLUDED.timezone,
                        enabled = EXCLUDED.enabled,
                        next_run_at = EXCLUDED.next_run_at,
                        last_run_at = EXCLUDED.last_run_at,
                        last_run_id = EXCLUDED.last_run_id,
                        last_run_status = EXCLUDED.last_run_status,
                        last_run_completed_at = EXCLUDED.last_run_completed_at,
                        last_output = EXCLUDED.last_output,
                        last_error = EXCLUDED.last_error,
                        updated_at = EXCLUDED.updated_at,
                        version = EXCLUDED.version
                    """,
                    values,
                )
            return job
        except Exception as error:
            raise self._unavailable(error) from error

    def delete_cron(self, cron_id: Any) -> bool:
        self._ensure_schema()
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {TABLE} WHERE id = %s", (str(cron_id),))
                return cursor.rowcount > 0
        except Exception as error:
            raise self._unavailable(error) from error

    def _connect(self):
        if self._connect_override is not None:
            return self._connect_override(**self.config)
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise StoreError(
                "cron PostgreSQL driver is unavailable",
                status=503,
                code="cron_store_unavailable",
            ) from error
        return psycopg.connect(**self.config, row_factory=dict_row)

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            try:
                with self._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(f"""
                        CREATE TABLE IF NOT EXISTS {TABLE} (
                            id TEXT PRIMARY KEY,
                            agent_id TEXT NOT NULL,
                            name TEXT NOT NULL,
                            prompt TEXT NOT NULL,
                            schedule TEXT NOT NULL,
                            timezone TEXT NOT NULL DEFAULT 'Etc/UTC',
                            enabled BOOLEAN NOT NULL DEFAULT TRUE,
                            next_run_at TIMESTAMPTZ,
                            last_run_at TIMESTAMPTZ,
                            last_run_id TEXT,
                            last_run_status TEXT,
                            last_run_completed_at TIMESTAMPTZ,
                            last_output TEXT,
                            last_error TEXT,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL,
                            version INTEGER NOT NULL DEFAULT 1
                        )
                    """)
                    cursor.execute(
                        f"CREATE INDEX IF NOT EXISTS idx_brain4all_cron_jobs_due "
                        f"ON {TABLE} (enabled, next_run_at)"
                    )
                    for column, definition in (
                        ("last_run_id", "TEXT"),
                        ("last_run_status", "TEXT"),
                        ("last_run_completed_at", "TIMESTAMPTZ"),
                        ("last_output", "TEXT"),
                        ("last_error", "TEXT"),
                    ):
                        cursor.execute(
                            f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {column} {definition}"
                        )
                self._schema_ready = True
            except Exception as error:
                raise self._unavailable(error) from error

    @staticmethod
    def _job(row: Mapping[str, Any]) -> dict[str, Any]:
        return {key: _iso(value) for key, value in dict(row).items()}

    @staticmethod
    def _unavailable(error: Exception) -> StoreError:
        if isinstance(error, StoreError):
            return error
        return StoreError(
            "cron PostgreSQL store is unavailable",
            status=503,
            code="cron_store_unavailable",
        )


__all__ = ["PostgresCronRepository", "TABLE"]
