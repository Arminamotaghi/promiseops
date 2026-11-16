"""Tests for database-backed batch priority scoring."""

from datetime import datetime

import pytest

from promiseops.priority_job import score_priority


def test_score_priority_rejects_naive_as_of():
    with pytest.raises(ValueError):
        score_priority(as_of=datetime(2026, 11, 10))


def test_cohort_query_filters_release_window_and_batch_predictions():
    from promiseops.priority_job import COHORT_SQL

    query = " ".join(COHORT_SQL.split()).lower()

    assert "o.released_at > cast(:previous_as_of as timestamptz)" in query
    assert "released_at <= :as_of" in query
    assert "not exists" in query
    assert "source = 'batch'" in query
    assert "order by" in query


def test_fetch_cohort_returns_order_ids_and_passes_cutoffs():
    from datetime import UTC
    from unittest.mock import MagicMock

    from promiseops.priority_job import fetch_cohort

    connection = MagicMock()
    connection.execute.return_value.scalars.return_value.all.return_value = [
        "order-001",
        "order-002",
    ]

    previous = datetime(2026, 11, 1, tzinfo=UTC)
    current = datetime(2026, 11, 10, tzinfo=UTC)

    result = fetch_cohort(
        connection,
        previous_as_of=previous,
        as_of=current,
    )

    assert result == ["order-001", "order-002"]
    connection.execute.assert_called_once()

    statement, parameters = connection.execute.call_args.args

    assert "FROM orders" in str(statement)
    assert parameters["previous_as_of"] == previous
    assert parameters["as_of"] == current


@pytest.mark.parametrize("previous_day", [10, 11])
def test_fetch_cohort_rejects_invalid_time_window(previous_day):
    from datetime import UTC
    from unittest.mock import MagicMock

    from promiseops.priority_job import fetch_cohort

    connection = MagicMock()

    with pytest.raises(ValueError, match="previous_as_of"):
        fetch_cohort(
            connection,
            previous_as_of=datetime(2026, 11, previous_day, tzinfo=UTC),
            as_of=datetime(2026, 11, 10, tzinfo=UTC),
        )

    connection.execute.assert_not_called()


def test_get_previous_as_of_returns_latest_cutoff():
    from datetime import UTC
    from unittest.mock import MagicMock

    from promiseops.priority_job import get_previous_as_of

    connection = MagicMock()
    previous = datetime(2026, 11, 9, tzinfo=UTC)
    current = datetime(2026, 11, 10, tzinfo=UTC)

    connection.execute.return_value.scalar_one.return_value = previous

    result = get_previous_as_of(connection, as_of=current)

    assert result == previous
    connection.execute.assert_called_once()

    statement, parameters = connection.execute.call_args.args
    assert "MAX(as_of)" in str(statement)
    assert "as_of < :as_of" in str(statement)
    assert parameters["as_of"] == current


def test_get_previous_as_of_returns_none_for_first_run():
    from datetime import UTC
    from unittest.mock import MagicMock

    from promiseops.priority_job import get_previous_as_of

    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = None

    result = get_previous_as_of(
        connection,
        as_of=datetime(2026, 11, 10, tzinfo=UTC),
    )

    assert result is None


def test_get_previous_as_of_rejects_naive_datetime():
    from unittest.mock import MagicMock

    from promiseops.priority_job import get_previous_as_of

    connection = MagicMock()

    with pytest.raises(ValueError):
        get_previous_as_of(
            connection,
            as_of=datetime(2026, 11, 10),
        )

    connection.execute.assert_not_called()


def test_fetch_cohort_accepts_first_run_without_previous_cutoff():
    from datetime import UTC
    from unittest.mock import MagicMock

    from promiseops.priority_job import fetch_cohort

    connection = MagicMock()
    connection.execute.return_value.scalars.return_value.all.return_value = [
        "order-001",
        "order-002",
    ]

    current = datetime(2026, 11, 10, tzinfo=UTC)

    result = fetch_cohort(
        connection,
        previous_as_of=None,
        as_of=current,
    )

    assert result == ["order-001", "order-002"]

    statement, parameters = connection.execute.call_args.args
    assert "CAST(:previous_as_of AS timestamptz) IS NULL" in str(statement)
    assert parameters["previous_as_of"] is None
    assert parameters["as_of"] == current


def test_score_cohort_scores_every_order(monkeypatch):
    from datetime import UTC
    from unittest.mock import MagicMock

    import pandas as pd

    from promiseops.priority_job import score_cohort

    as_of = datetime(2026, 11, 10, tzinfo=UTC)
    connection = MagicMock()
    model = MagicMock()

    features = pd.DataFrame(
        {"feature_a": [10, 20]},
        index=pd.Index(["order-001", "order-002"], name="order_id"),
    )

    build = MagicMock(return_value=features)
    predict = MagicMock(side_effect=[0.8, 0.3])

    monkeypatch.setattr("promiseops.features.build_features", build)
    monkeypatch.setattr("promiseops.release._positive_probability", predict)

    result = score_cohort(
        connection,
        model,
        ["order-001", "order-002"],
        as_of=as_of,
    )

    assert result == [
        {"order_id": "order-001", "risk_probability": 0.8},
        {"order_id": "order-002", "risk_probability": 0.3},
    ]

    build.assert_called_once_with(
        connection,
        ["order-001", "order-002"],
        as_of=as_of,
        labeled=False,
    )

    assert predict.call_count == 2
    assert predict.call_args_list[0].args[1].index.tolist() == ["order-001"]
    assert predict.call_args_list[1].args[1].index.tolist() == ["order-002"]


def test_score_cohort_empty_does_not_call_model(monkeypatch):
    from datetime import UTC
    from unittest.mock import MagicMock

    from promiseops.priority_job import score_cohort

    build = MagicMock()
    predict = MagicMock()

    monkeypatch.setattr("promiseops.features.build_features", build)
    monkeypatch.setattr("promiseops.release._positive_probability", predict)

    result = score_cohort(
        MagicMock(),
        MagicMock(),
        [],
        as_of=datetime(2026, 11, 10, tzinfo=UTC),
    )

    assert result == []
    build.assert_not_called()
    predict.assert_not_called()


def test_score_cohort_rejects_missing_feature_rows(monkeypatch):
    from datetime import UTC
    from unittest.mock import MagicMock

    import pandas as pd

    from promiseops.priority_job import score_cohort

    features = pd.DataFrame(
        {"feature_a": [10]},
        index=pd.Index(["order-001"], name="order_id"),
    )

    build = MagicMock(return_value=features)
    predict = MagicMock()

    monkeypatch.setattr("promiseops.features.build_features", build)
    monkeypatch.setattr("promiseops.release._positive_probability", predict)

    with pytest.raises(ValueError, match="Feature rows do not match"):
        score_cohort(
            MagicMock(),
            MagicMock(),
            ["order-001", "order-002"],
            as_of=datetime(2026, 11, 10, tzinfo=UTC),
        )

    predict.assert_not_called()


def test_get_existing_priority_run_returns_saved_output():
    from datetime import UTC
    from unittest.mock import MagicMock

    from promiseops.priority_job import get_existing_priority_run

    connection = MagicMock()
    saved_output = {
        "schema_version": "1.0",
        "scored_count": 10,
        "k": 1,
        "orders": [{"rank": 1, "order_id": "order-001", "risk_probability": 0.9}],
    }

    connection.execute.return_value.scalars.return_value.all.return_value = [
        saved_output
    ]

    as_of = datetime(2026, 11, 10, tzinfo=UTC)

    result = get_existing_priority_run(
        connection,
        as_of=as_of,
        model_version="model-v1",
    )

    assert result == saved_output

    statement, parameters = connection.execute.call_args.args
    assert "FROM priority_runs" in str(statement)
    assert "LIMIT 2" in str(statement)
    assert parameters["as_of"] == as_of
    assert parameters["model_version"] == "model-v1"


def test_get_existing_priority_run_returns_none_when_missing():
    from datetime import UTC
    from unittest.mock import MagicMock

    from promiseops.priority_job import get_existing_priority_run

    connection = MagicMock()
    connection.execute.return_value.scalars.return_value.all.return_value = []

    result = get_existing_priority_run(
        connection,
        as_of=datetime(2026, 11, 10, tzinfo=UTC),
        model_version="model-v1",
    )

    assert result is None


def test_get_existing_priority_run_rejects_duplicate_runs():
    from datetime import UTC
    from unittest.mock import MagicMock

    from promiseops.priority_job import get_existing_priority_run

    connection = MagicMock()
    connection.execute.return_value.scalars.return_value.all.return_value = [
        {"orders": []},
        {"orders": []},
    ]

    with pytest.raises(ValueError, match="Multiple priority runs"):
        get_existing_priority_run(
            connection,
            as_of=datetime(2026, 11, 10, tzinfo=UTC),
            model_version="model-v1",
        )


def test_batch_request_id_is_deterministic_and_valid():
    import re

    from promiseops.priority_job import build_batch_request_id

    first = build_batch_request_id("order-001")
    second = build_batch_request_id("order-001")
    different = build_batch_request_id("order-002")

    assert first == second
    assert first != different
    assert len(first) <= 128
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", first)


def test_batch_request_id_rejects_empty_order_id():
    from promiseops.priority_job import build_batch_request_id

    with pytest.raises(ValueError):
        build_batch_request_id("")


@pytest.mark.parametrize("order_id", [None, 123, "", []])
def test_batch_request_id_rejects_invalid_order_ids(order_id):
    from promiseops.priority_job import build_batch_request_id

    with pytest.raises(ValueError):
        build_batch_request_id(order_id)


def test_insert_batch_predictions_inserts_all_orders():
    from unittest.mock import MagicMock

    from promiseops.priority_job import (
        build_batch_request_id,
        insert_batch_predictions,
    )

    connection = MagicMock()

    scored_orders = [
        {"order_id": "order-001", "risk_probability": 0.91},
        {"order_id": "order-002", "risk_probability": 0.27},
    ]

    insert_batch_predictions(
        connection,
        scored_orders=scored_orders,
        model_version="model-v1",
    )

    assert connection.execute.call_count == 2

    first_call = connection.execute.call_args_list[0]
    second_call = connection.execute.call_args_list[1]

    statement = str(first_call.args[0])
    assert "INSERT INTO predictions" in statement
    assert "'batch'" in statement

    assert first_call.args[1] == {
        "request_id": build_batch_request_id("order-001"),
        "order_id": "order-001",
        "risk_probability": 0.91,
        "model_version": "model-v1",
    }

    assert second_call.args[1] == {
        "request_id": build_batch_request_id("order-002"),
        "order_id": "order-002",
        "risk_probability": 0.27,
        "model_version": "model-v1",
    }

    connection.commit.assert_not_called()


def test_insert_batch_predictions_empty_cohort():
    from unittest.mock import MagicMock

    from promiseops.priority_job import insert_batch_predictions

    connection = MagicMock()

    insert_batch_predictions(
        connection,
        scored_orders=[],
        model_version="model-v1",
    )

    connection.execute.assert_not_called()
    connection.commit.assert_not_called()


def test_insert_priority_run_persists_contract_output():
    import json
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from promiseops.features import cohort_hash
    from promiseops.priority import build_priority_output, build_run_id
    from promiseops.priority_job import insert_priority_run

    connection = MagicMock()
    as_of = datetime(2026, 11, 10, tzinfo=UTC)
    order_ids = ["order-001", "order-002"]
    digest = cohort_hash(order_ids)

    output = build_priority_output(
        as_of=as_of,
        cohort_id="released:(start,2026-11-10T00:00:00Z]",
        cohort_rule=(
            "orders with released_at in (previous as_of, as_of] "
            "that have no batch prediction yet"
        ),
        cohort_hash=digest,
        model_version="model-v1",
        scored_orders=[
            {"order_id": "order-001", "risk_probability": 0.91},
            {"order_id": "order-002", "risk_probability": 0.27},
        ],
    )

    run_id = insert_priority_run(connection, output=output)

    assert run_id == build_run_id(digest, "model-v1")
    connection.execute.assert_called_once()

    statement, params = connection.execute.call_args.args

    assert "INSERT INTO priority_runs" in str(statement)
    assert params["run_id"] == run_id
    assert params["as_of"] == as_of
    assert params["cohort_hash"] == digest
    assert params["model_version"] == "model-v1"
    assert params["scored_count"] == 2
    assert params["k"] == 1
    assert json.loads(params["output"]) == output

    connection.commit.assert_not_called()


def test_acquire_priority_lock_succeeds():
    from unittest.mock import MagicMock

    from promiseops.priority_job import acquire_priority_lock

    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = True

    acquire_priority_lock(connection)

    connection.execute.assert_called_once()
    statement = connection.execute.call_args.args[0]

    assert "pg_try_advisory_xact_lock" in str(statement)
    connection.commit.assert_not_called()


def test_acquire_priority_lock_rejects_concurrent_job():
    from unittest.mock import MagicMock

    from promiseops.priority_job import acquire_priority_lock

    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = False

    with pytest.raises(
        RuntimeError,
        match="Another priority job is already running",
    ):
        acquire_priority_lock(connection)


def test_get_priority_run_by_id_returns_existing_run():
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from promiseops.priority_job import get_priority_run_by_id

    connection = MagicMock()
    saved_run = {
        "as_of": datetime(2026, 11, 10, tzinfo=UTC),
        "model_version": "model-v1",
        "output": {"schema_version": "1.0", "orders": []},
    }

    connection.execute.return_value.mappings.return_value.one_or_none.return_value = saved_run

    result = get_priority_run_by_id(
        connection,
        run_id="example-run-id",
    )

    assert result == saved_run

    statement, parameters = connection.execute.call_args.args
    assert "FROM priority_runs" in str(statement)
    assert "WHERE run_id = :run_id" in str(statement)
    assert parameters == {"run_id": "example-run-id"}


def test_get_priority_run_by_id_returns_none_when_missing():
    from unittest.mock import MagicMock

    from promiseops.priority_job import get_priority_run_by_id

    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.one_or_none.return_value = None

    result = get_priority_run_by_id(
        connection,
        run_id="unknown-run-id",
    )

    assert result is None


def test_ensure_run_id_available_when_missing(monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from promiseops.priority_job import ensure_run_id_available

    connection = MagicMock()
    lookup = MagicMock(return_value=None)

    monkeypatch.setattr(
        "promiseops.priority_job.get_priority_run_by_id",
        lookup,
    )

    as_of = datetime(2026, 11, 10, tzinfo=UTC)

    ensure_run_id_available(
        connection,
        run_id="run-001",
        as_of=as_of,
    )

    lookup.assert_called_once_with(connection, run_id="run-001")


def test_ensure_run_id_rejects_different_cutoff(monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from promiseops.priority_job import ensure_run_id_available

    monkeypatch.setattr(
        "promiseops.priority_job.get_priority_run_by_id",
        lambda connection, *, run_id: {
            "as_of": datetime(2026, 11, 9, tzinfo=UTC)
        },
    )

    with pytest.raises(ValueError, match="Priority run_id collision"):
        ensure_run_id_available(
            MagicMock(),
            run_id="run-001",
            as_of=datetime(2026, 11, 10, tzinfo=UTC),
        )


def test_ensure_run_id_rejects_existing_same_cutoff(monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from promiseops.priority_job import ensure_run_id_available

    as_of = datetime(2026, 11, 10, tzinfo=UTC)

    monkeypatch.setattr(
        "promiseops.priority_job.get_priority_run_by_id",
        lambda connection, *, run_id: {"as_of": as_of},
    )

    with pytest.raises(ValueError, match="already exists for this as_of"):
        ensure_run_id_available(
            MagicMock(),
            run_id="run-001",
            as_of=as_of,
        )


def test_score_priority_reuses_existing_run(monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from promiseops.priority_job import score_priority

    as_of = datetime(2026, 11, 10, tzinfo=UTC)
    saved_output = {
        "schema_version": "1.0",
        "scored_count": 1,
        "k": 1,
        "orders": [
            {
                "rank": 1,
                "order_id": "order-001",
                "risk_probability": 0.9,
            }
        ],
    }

    connection = MagicMock()
    engine = MagicMock()
    engine.begin.return_value.__enter__.return_value = connection

    bundle = MagicMock()
    bundle.metadata = {"model_version": "model-v1"}

    monkeypatch.setattr(
        "promiseops.release.load_bundle",
        lambda path: bundle,
    )
    monkeypatch.setattr(
        "promiseops.api.create_database_engine",
        lambda: engine,
    )

    lock = MagicMock()
    lookup = MagicMock(return_value=saved_output)
    fetch = MagicMock()
    score = MagicMock()
    insert = MagicMock()

    monkeypatch.setattr("promiseops.priority_job.acquire_priority_lock", lock)
    monkeypatch.setattr(
        "promiseops.priority_job.get_existing_priority_run", lookup
    )
    monkeypatch.setattr("promiseops.priority_job.fetch_cohort", fetch)
    monkeypatch.setattr("promiseops.priority_job.score_cohort", score)
    monkeypatch.setattr(
        "promiseops.priority_job.insert_priority_run", insert
    )

    result = score_priority(as_of=as_of)

    assert result == saved_output
    lock.assert_called_once_with(connection)
    lookup.assert_called_once_with(
        connection,
        as_of=as_of,
        model_version="model-v1",
    )
    fetch.assert_not_called()
    score.assert_not_called()
    insert.assert_not_called()
    engine.dispose.assert_called_once()


def test_score_priority_new_run_scores_and_persists(monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from promiseops.priority_job import score_priority

    as_of = datetime(2026, 11, 10, tzinfo=UTC)
    connection = MagicMock()
    engine = MagicMock()
    engine.begin.return_value.__enter__.return_value = connection

    bundle = MagicMock()
    bundle.metadata = {"model_version": "model-v1"}

    monkeypatch.setattr(
        "promiseops.release.load_bundle",
        lambda path: bundle,
    )
    monkeypatch.setattr(
        "promiseops.api.create_database_engine",
        lambda: engine,
    )

    events = []

    monkeypatch.setattr(
        "promiseops.priority_job.acquire_priority_lock",
        lambda conn: events.append(("lock", conn)),
    )
    monkeypatch.setattr(
        "promiseops.priority_job.get_existing_priority_run",
        lambda conn, **kwargs: None,
    )
    monkeypatch.setattr(
        "promiseops.priority_job.get_previous_as_of",
        lambda conn, **kwargs: None,
    )

    def fetch(conn, **kwargs):
        events.append(("fetch", conn))
        return ["order-001", "order-002"]

    def score(conn, model, order_ids, **kwargs):
        events.append(("score", conn))
        assert model is bundle.model
        assert order_ids == ["order-001", "order-002"]
        return [
            {"order_id": "order-001", "risk_probability": 0.9},
            {"order_id": "order-002", "risk_probability": 0.3},
        ]

    monkeypatch.setattr("promiseops.priority_job.fetch_cohort", fetch)
    monkeypatch.setattr("promiseops.priority_job.score_cohort", score)
    monkeypatch.setattr(
        "promiseops.priority_job.ensure_run_id_available",
        lambda conn, **kwargs: events.append(("check_id", conn)),
    )
    monkeypatch.setattr(
        "promiseops.priority_job.insert_batch_predictions",
        lambda conn, **kwargs: events.append(("predictions", conn)),
    )
    monkeypatch.setattr(
        "promiseops.priority_job.insert_priority_run",
        lambda conn, **kwargs: events.append(("priority_run", conn)),
    )

    result = score_priority(as_of=as_of)

    assert result["scored_count"] == 2
    assert result["k"] == 1
    assert result["orders"][0]["order_id"] == "order-001"

    assert [name for name, _ in events] == [
        "lock",
        "fetch",
        "score",
        "check_id",
        "predictions",
        "priority_run",
    ]
    assert all(conn is connection for _, conn in events)
    engine.begin.assert_called_once()
    engine.dispose.assert_called_once()


def test_score_priority_rolls_back_when_persist_fails(monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from promiseops.priority_job import score_priority

    as_of = datetime(2026, 11, 10, tzinfo=UTC)
    connection = MagicMock()
    engine = MagicMock()
    transaction = engine.begin.return_value
    transaction.__enter__.return_value = connection

    bundle = MagicMock()
    bundle.metadata = {"model_version": "model-v1"}

    monkeypatch.setattr(
        "promiseops.release.load_bundle",
        lambda path: bundle,
    )
    monkeypatch.setattr(
        "promiseops.api.create_database_engine",
        lambda: engine,
    )
    monkeypatch.setattr(
        "promiseops.priority_job.acquire_priority_lock",
        lambda conn: None,
    )
    monkeypatch.setattr(
        "promiseops.priority_job.get_existing_priority_run",
        lambda conn, **kwargs: None,
    )
    monkeypatch.setattr(
        "promiseops.priority_job.get_previous_as_of",
        lambda conn, **kwargs: None,
    )
    monkeypatch.setattr(
        "promiseops.priority_job.fetch_cohort",
        lambda conn, **kwargs: ["order-001"],
    )
    monkeypatch.setattr(
        "promiseops.priority_job.score_cohort",
        lambda conn, model, ids, **kwargs: [
            {"order_id": "order-001", "risk_probability": 0.9}
        ],
    )
    monkeypatch.setattr(
        "promiseops.priority_job.ensure_run_id_available",
        lambda conn, **kwargs: None,
    )

    predictions = MagicMock()
    monkeypatch.setattr(
        "promiseops.priority_job.insert_batch_predictions",
        predictions,
    )

    def fail_to_save(conn, *, output):
        raise RuntimeError("Simulated database insert failure")

    monkeypatch.setattr(
        "promiseops.priority_job.insert_priority_run",
        fail_to_save,
    )

    with pytest.raises(
        RuntimeError,
        match="Simulated database insert failure",
    ):
        score_priority(as_of=as_of)

    predictions.assert_called_once()
    transaction.__exit__.assert_called_once()

    exc_type, exc_value, traceback = transaction.__exit__.call_args.args

    assert exc_type is RuntimeError
    assert str(exc_value) == "Simulated database insert failure"
    assert traceback is not None

    engine.dispose.assert_called_once()
