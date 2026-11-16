"""Apply the SQL migrations shipped with the image to the team database.

The database is reachable only from inside the cluster, so this runs as a Job (the Helm
chart starts it before every install and upgrade):

    python -m promiseops.migrate

Every ``*.sql`` file in the migrations directory is executed in name order. The files are
idempotent (``CREATE ... IF NOT EXISTS``) and carry their own ``BEGIN``/``COMMIT``, so
applying them on every deploy is safe. A session advisory lock keeps two deploys from
running the same files at the same moment; it is released explicitly when the run ends.

Schema changes are never rolled back: if file N fails, files before it stay committed while
Helm keeps the previous pods. Every migration must therefore be additive and work with the
code of the previous release (add tables, columns or indexes; never drop or rename).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

import psycopg

DEFAULT_SQL_DIR = "sql"
# Arbitrary constant shared by every run of this module against one database.
ADVISORY_LOCK_KEY = 7007_2026


class MigrationError(RuntimeError):
    """The migrations could not be found or applied."""


def migration_files(sql_dir: Path | str) -> list[Path]:
    """Return the migration files in the order they must be applied."""
    sql_dir = Path(sql_dir)
    if not sql_dir.is_dir():
        raise MigrationError(f"migrations directory not found: {sql_dir}")
    files = sorted(path for path in sql_dir.iterdir() if path.is_file() and path.suffix == ".sql")
    if not files:
        raise MigrationError(f"no .sql files in {sql_dir}")
    return files


def connection_url(url: str) -> str:
    """psycopg wants a plain libpq URL; accept the SQLAlchemy spelling as well."""
    for prefix in ("postgresql+psycopg://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


def apply_migrations(connection, files: list[Path]) -> list[str]:
    """Execute each file as one script on an autocommit connection."""
    applied = []
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        try:
            for path in files:
                try:
                    cursor.execute(path.read_text(encoding="utf-8"))
                except psycopg.Error as exc:
                    detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
                    raise MigrationError(f"{path.name} failed: {detail}") from exc
                applied.append(path.name)
        except BaseException:
            # A failed file leaves its transaction open and aborted; end it so the unlock can
            # run. If the session itself is gone, the server has already dropped the lock.
            with contextlib.suppress(psycopg.Error):
                cursor.execute("ROLLBACK")
                cursor.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
            raise
        cursor.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
    return applied


def public_tables(connection) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        )
        return [row[0] for row in cursor.fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="promiseops.migrate", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(os.environ.get("PROMISEOPS_SQL_DIR", DEFAULT_SQL_DIR)),
        help="directory with the *.sql migration files",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("PROMISEOPS_DATABASE_URL")
    if not database_url:
        print("error: PROMISEOPS_DATABASE_URL is not configured", file=sys.stderr)
        return 1

    try:
        files = migration_files(args.dir)
        # Closing the connection ends the session as well, so the lock cannot outlive the Job.
        with psycopg.connect(connection_url(database_url), autocommit=True, connect_timeout=15) as connection:
            applied = apply_migrations(connection, files)
            tables = public_tables(connection)
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        # The message of a connection failure names host and user, never the password.
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        print(f"error: database is not reachable: {detail}", file=sys.stderr)
        return 1

    print(json.dumps({"applied": applied, "tables": tables}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
