import json
from pathlib import Path

import psycopg
import pytest

from promiseops import migrate

ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, params=None):
        self.connection.executed.append((query, params))
        if self.connection.fail_on and self.connection.fail_on in query:
            raise psycopg.errors.SyntaxError('syntax error at or near "BROKEN"\nLINE 1: BROKEN')

    def fetchall(self):
        return [("batch_receipts",), ("orders",)]


class FakeConnection:
    def __init__(self, fail_on=None):
        self.executed = []
        self.fail_on = fail_on
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.closed = True
        return False


def _write(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


def test_repository_migrations_are_applied_in_numeric_order():
    names = [path.name for path in migrate.migration_files(ROOT / "sql")]

    assert names == sorted(names)
    assert names[:2] == ["001_source_and_ingest.sql", "002_predictions.sql"]


def test_migration_files_ignores_other_files_and_sorts_by_name(tmp_path):
    _write(tmp_path, "010_later.sql", "SELECT 10;")
    _write(tmp_path, "002_second.sql", "SELECT 2;")
    _write(tmp_path, "notes.md", "not a migration")

    assert [path.name for path in migrate.migration_files(tmp_path)] == [
        "002_second.sql",
        "010_later.sql",
    ]


def test_migration_files_rejects_missing_and_empty_directories(tmp_path):
    with pytest.raises(migrate.MigrationError, match="not found"):
        migrate.migration_files(tmp_path / "absent")
    with pytest.raises(migrate.MigrationError, match="no .sql files"):
        migrate.migration_files(tmp_path)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u@db/app", "postgresql://u@db/app"),
        ("postgresql+psycopg://u@db/app", "postgresql://u@db/app"),
        ("postgres://u@db/app", "postgresql://u@db/app"),
    ],
)
def test_connection_url_accepts_both_spellings(given, expected):
    assert migrate.connection_url(given) == expected


def test_apply_migrations_takes_the_lock_then_runs_each_file_once(tmp_path):
    first = _write(tmp_path, "001_a.sql", "CREATE TABLE IF NOT EXISTS a (id int);")
    second = _write(tmp_path, "002_b.sql", "CREATE TABLE IF NOT EXISTS b (id int);")
    connection = FakeConnection()

    applied = migrate.apply_migrations(connection, [first, second])

    assert applied == ["001_a.sql", "002_b.sql"]
    queries = [query for query, _ in connection.executed]
    assert "pg_advisory_lock" in queries[0]
    assert queries[1:] == [first.read_text(), second.read_text()]


def test_apply_migrations_names_the_failing_file(tmp_path):
    good = _write(tmp_path, "001_a.sql", "SELECT 1;")
    bad = _write(tmp_path, "002_b.sql", "BROKEN")
    never = _write(tmp_path, "003_c.sql", "SELECT 3;")
    connection = FakeConnection(fail_on="BROKEN")

    with pytest.raises(migrate.MigrationError, match=r"^002_b\.sql failed: syntax error"):
        migrate.apply_migrations(connection, [good, bad, never])

    assert never.read_text() not in [query for query, _ in connection.executed]


def test_main_requires_the_database_url(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PROMISEOPS_DATABASE_URL", raising=False)

    assert migrate.main(["--dir", str(tmp_path)]) == 1
    assert "PROMISEOPS_DATABASE_URL" in capsys.readouterr().err


def test_main_reports_applied_files_and_tables(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "001_a.sql", "SELECT 1;")
    connection = FakeConnection()
    captured = {}

    def fake_connect(url, **kwargs):
        captured.update(url=url, kwargs=kwargs)
        return connection

    monkeypatch.setattr(migrate.psycopg, "connect", fake_connect)
    monkeypatch.setenv("PROMISEOPS_DATABASE_URL", "postgresql+psycopg://user:hidden@db/app")

    assert migrate.main(["--dir", str(tmp_path)]) == 0

    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "applied": ["001_a.sql"],
        "tables": ["batch_receipts", "orders"],
    }
    assert captured["url"] == "postgresql://user:hidden@db/app"
    assert captured["kwargs"]["autocommit"] is True
    assert connection.closed is True
    assert "hidden" not in output.out + output.err


def test_main_fails_without_leaking_the_url(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "001_a.sql", "BROKEN")
    connection = FakeConnection(fail_on="BROKEN")
    monkeypatch.setattr(migrate.psycopg, "connect", lambda url, **kwargs: connection)
    monkeypatch.setenv("PROMISEOPS_DATABASE_URL", "postgresql://user:hidden@db/app")

    assert migrate.main(["--dir", str(tmp_path)]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert "001_a.sql failed" in output.err
    assert "hidden" not in output.err
    assert connection.closed is True
