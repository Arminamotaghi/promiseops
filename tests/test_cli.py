from datetime import UTC

import pytest

from promiseops.cli import main


def test_score_priority_accepts_timezone_aware_as_of(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["promiseops", "score-priority", "--as-of", "2018-01-01T00:00:00Z"],
    )

    assert main() == 1

    output = capsys.readouterr()
    assert "database and model integration are not implemented" in output.err
    assert "2018-01-01T00:00:00+00:00" in output.err


def test_score_priority_rejects_as_of_without_timezone(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["promiseops", "score-priority", "--as-of", "2018-01-01"],
    )

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2


def test_score_priority_accepts_missing_as_of(monkeypatch, capsys):
    from datetime import datetime

    expected = datetime(2018, 9, 15, 10, 0, tzinfo=UTC)

    monkeypatch.setattr(
        "promiseops.cli.latest_released_at",
        lambda: expected,
    )
    monkeypatch.setattr(
        "sys.argv",
        ["promiseops", "score-priority"],
    )

    assert main() == 1

    output = capsys.readouterr()
    assert "database and model integration are not implemented" in output.err
    assert "as_of=2018-09-15T10:00:00+00:00" in output.err


def test_latest_released_at_reads_max_order_release(monkeypatch):
    from datetime import datetime
    from unittest.mock import MagicMock

    from promiseops.cli import latest_released_at

    expected = datetime(2018, 9, 15, 10, 0, tzinfo=UTC)

    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = expected

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection

    create_engine = MagicMock(return_value=engine)

    monkeypatch.setenv(
        "PROMISEOPS_DATABASE_URL",
        "postgresql+psycopg://test:test@localhost/test",
    )
    monkeypatch.setattr("promiseops.cli.create_engine", create_engine)

    result = latest_released_at()

    assert result == expected
    create_engine.assert_called_once()
    connection.execute.assert_called_once()
    assert str(connection.execute.call_args.args[0]) == (
        "SELECT MAX(released_at) FROM orders"
    )
    engine.dispose.assert_called_once()


def test_latest_released_at_rejects_empty_orders(monkeypatch):
    from unittest.mock import MagicMock

    from promiseops.cli import latest_released_at

    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = None

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection

    monkeypatch.setenv(
        "PROMISEOPS_DATABASE_URL",
        "postgresql+psycopg://test:test@localhost/test",
    )
    monkeypatch.setattr(
        "promiseops.cli.create_engine",
        lambda url: engine,
    )

    with pytest.raises(ValueError, match="No orders found in database"):
        latest_released_at()

    engine.dispose.assert_called_once()


def test_latest_released_at_handles_database_error(monkeypatch):
    from unittest.mock import MagicMock

    from sqlalchemy.exc import OperationalError

    from promiseops.cli import latest_released_at

    engine = MagicMock()
    engine.connect.side_effect = OperationalError(
        "SELECT MAX(released_at) FROM orders",
        {},
        Exception("Connection refused"),
    )

    monkeypatch.setenv(
        "PROMISEOPS_DATABASE_URL",
        "postgresql+psycopg://test:test@localhost/test",
    )
    monkeypatch.setattr(
        "promiseops.cli.create_engine",
        lambda url: engine,
    )

    with pytest.raises(
        ValueError,
        match="Cannot read latest released_at from orders database",
    ):
        latest_released_at()

    engine.dispose.assert_called_once()


def test_latest_released_at_requires_database_url(monkeypatch):
    from promiseops.cli import latest_released_at

    monkeypatch.delenv("PROMISEOPS_DATABASE_URL", raising=False)

    with pytest.raises(
        ValueError,
        match="PROMISEOPS_DATABASE_URL is required",
    ):
        latest_released_at()


def test_score_priority_requires_database_url_when_as_of_missing(
    monkeypatch, capsys
):
    monkeypatch.delenv("PROMISEOPS_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        ["promiseops", "score-priority"],
    )

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "PROMISEOPS_DATABASE_URL is required" in output.err
