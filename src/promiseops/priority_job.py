"""Database-backed batch priority scoring job."""

from datetime import datetime, timedelta

from promiseops.contracts import instant


def score_priority(*, as_of: datetime) -> dict:
    """Score newly released orders and persist the priority result."""
    import os

    from promiseops.api import create_database_engine
    from promiseops.features import cohort_hash
    from promiseops.priority import build_priority_output, build_run_id
    from promiseops.release import load_bundle

    as_of = instant(as_of)

    bundle = load_bundle(os.getenv("PROMISEOPS_RELEASE_DIR", "release"))
    model_version = bundle.metadata["model_version"]

    engine = create_database_engine()
    try:
        with engine.begin() as connection:
            acquire_priority_lock(connection)

            existing = get_existing_priority_run(
                connection,
                as_of=as_of,
                model_version=model_version,
            )
            if existing is not None:
                return existing

            previous_as_of = get_previous_as_of(connection, as_of=as_of)

            if previous_as_of is not None:
                previous_as_of = instant(previous_as_of)

            order_ids = fetch_cohort(
                connection,
                previous_as_of=previous_as_of,
                as_of=as_of,
            )

            scored_orders = score_cohort(
                connection,
                bundle.model,
                order_ids,
                as_of=as_of,
            )

            digest = cohort_hash(order_ids)
            current_label = as_of.isoformat().replace("+00:00", "Z")

            if previous_as_of is None:
                first_run_start = as_of - timedelta(days=1)
                start_label = first_run_start.isoformat().replace("+00:00", "Z")
                cohort_id = f"approved:({start_label},{current_label}]"
                cohort_rule = (
                    "first run: orders with approved_at in "
                    "(as_of - 1 day, as_of] that have no batch prediction yet"
                )
            else:
                previous_label = previous_as_of.isoformat().replace("+00:00", "Z")
                cohort_id = f"released:({previous_label},{current_label}]"
                cohort_rule = (
                    "orders with released_at in (previous as_of, as_of] "
                    "that have no batch prediction yet"
                )

            output = build_priority_output(
                as_of=as_of,
                cohort_id=cohort_id,
                cohort_rule=cohort_rule,
                cohort_hash=digest,
                model_version=model_version,
                scored_orders=scored_orders,
            )

            if output["scored_count"] == 0:
                return output

            run_id = build_run_id(digest, model_version)
            ensure_run_id_available(
                connection,
                run_id=run_id,
                as_of=as_of,
            )

            insert_batch_predictions(
                connection,
                scored_orders=scored_orders,
                model_version=model_version,
            )
            insert_priority_run(connection, output=output)

            return output
    finally:
        engine.dispose()


COHORT_SQL = """
SELECT o.order_id
FROM orders AS o
WHERE (
    (
        CAST(:previous_as_of AS timestamptz) IS NULL
        AND o.approved_at > :first_run_start
        AND o.approved_at <= :as_of
    )
    OR
    (
        CAST(:previous_as_of AS timestamptz) IS NOT NULL
        AND o.released_at > CAST(:previous_as_of AS timestamptz)
        AND o.released_at <= :as_of
    )
)
AND NOT EXISTS (
    SELECT 1
    FROM predictions AS p
    WHERE p.order_id = o.order_id
      AND p.source = 'batch'
)
ORDER BY o.order_id
"""


def fetch_cohort(connection, *, previous_as_of: datetime | None, as_of: datetime) -> list[str]:
    """Fetch unscored batch orders released within the requested window."""
    from sqlalchemy import text

    as_of = instant(as_of)

    if previous_as_of is not None:
        previous_as_of = instant(previous_as_of)
        if previous_as_of >= as_of:
            raise ValueError("previous_as_of must be earlier than as_of")

    rows = connection.execute(
        text(COHORT_SQL),
        {
            "previous_as_of": previous_as_of,
            "first_run_start": as_of - timedelta(days=1),
            "as_of": as_of,
        },
    ).scalars().all()

    return list(rows)


def get_previous_as_of(connection, *, as_of: datetime):
    """Return the latest earlier priority run cutoff, if any."""
    from sqlalchemy import text

    as_of = instant(as_of)

    return connection.execute(
        text(
            """
            SELECT MAX(as_of)
            FROM priority_runs
            WHERE as_of < :as_of
            """
        ),
        {"as_of": as_of},
    ).scalar_one()


def score_cohort(connection, model, order_ids: list[str], *, as_of: datetime) -> list[dict]:
    """Score every eligible order using the shared feature contract."""
    from promiseops.features import build_features
    from promiseops.release import _positive_probability

    as_of = instant(as_of)

    if not order_ids:
        return []

    features = build_features(
        connection,
        order_ids,
        as_of=as_of,
        labeled=False,
    )

    if len(features) != len(order_ids) or set(features.index) != set(order_ids):
        raise ValueError("Feature rows do not match the selected cohort")

    return [
        {
            "order_id": order_id,
            "risk_probability": _positive_probability(
                model,
                features.loc[[order_id]],
            ),
        }
        for order_id in order_ids
    ]


def get_existing_priority_run(connection, *, as_of: datetime, model_version: str):
    """Return a previously stored priority output for this cutoff and model."""
    from sqlalchemy import text

    as_of = instant(as_of)

    if not isinstance(model_version, str) or not model_version:
        raise ValueError("model_version must be a non-empty string")

    rows = connection.execute(
        text(
            """
            SELECT output
            FROM priority_runs
            WHERE as_of = :as_of
              AND model_version = :model_version
            ORDER BY created_at DESC, run_id
            LIMIT 2
            """
        ),
        {
            "as_of": as_of,
            "model_version": model_version,
        },
    ).scalars().all()

    if len(rows) > 1:
        raise ValueError(
            "Multiple priority runs exist for the same as_of and model_version"
        )

    return rows[0] if rows else None


def build_batch_request_id(order_id: str) -> str:
    """Build a stable request ID for one batch-scored order."""
    import hashlib

    if not isinstance(order_id, str) or not order_id:
        raise ValueError("order_id must be a non-empty string")

    digest = hashlib.sha256(order_id.encode("utf-8")).hexdigest()
    return f"batch:{digest}"


def insert_batch_predictions(
    connection,
    *,
    scored_orders: list[dict],
    model_version: str,
) -> None:
    """Insert all batch predictions using the caller's transaction."""
    from sqlalchemy import text

    if not isinstance(model_version, str) or not model_version:
        raise ValueError("model_version must be a non-empty string")

    statement = text(
        """
        INSERT INTO predictions (
            request_id,
            order_id,
            risk_probability,
            model_version,
            source
        )
        VALUES (
            :request_id,
            :order_id,
            :risk_probability,
            :model_version,
            'batch'
        )
        """
    )

    for order in scored_orders:
        connection.execute(
            statement,
            {
                "request_id": build_batch_request_id(order["order_id"]),
                "order_id": order["order_id"],
                "risk_probability": order["risk_probability"],
                "model_version": model_version,
            },
        )


def insert_priority_run(connection, *, output: dict) -> str:
    """Persist a priority result using the caller's transaction."""
    import json

    from sqlalchemy import text

    from promiseops.priority import build_run_id

    cohort_hash = output["cohort"]["hash"]
    model_version = output["model_version"]
    run_id = build_run_id(cohort_hash, model_version)

    connection.execute(
        text(
            """
            INSERT INTO priority_runs (
                run_id,
                as_of,
                cohort_id,
                cohort_hash,
                model_version,
                scored_count,
                k,
                output
            )
            VALUES (
                :run_id,
                :as_of,
                :cohort_id,
                :cohort_hash,
                :model_version,
                :scored_count,
                :k,
                CAST(:output AS jsonb)
            )
            """
        ),
        {
            "run_id": run_id,
            "as_of": instant(output["as_of"]),
            "cohort_id": output["cohort"]["id"],
            "cohort_hash": cohort_hash,
            "model_version": model_version,
            "scored_count": output["scored_count"],
            "k": output["k"],
            "output": json.dumps(output),
        },
    )

    return run_id


def acquire_priority_lock(connection) -> None:
    """Serialize priority jobs using a PostgreSQL transaction lock."""
    from sqlalchemy import text

    acquired = connection.execute(
        text("SELECT pg_try_advisory_xact_lock(741203, 1)")
    ).scalar_one()

    if acquired is not True:
        raise RuntimeError("Another priority job is already running")


def get_priority_run_by_id(connection, *, run_id: str):
    """Find an existing priority run by its contract-defined ID."""
    from sqlalchemy import text

    return connection.execute(
        text(
            """
            SELECT as_of, model_version, output
            FROM priority_runs
            WHERE run_id = :run_id
            """
        ),
        {"run_id": run_id},
    ).mappings().one_or_none()


def ensure_run_id_available(connection, *, run_id: str, as_of: datetime) -> None:
    """Reject a run ID already associated with another cutoff."""
    as_of = instant(as_of)

    existing = get_priority_run_by_id(connection, run_id=run_id)

    if existing is None:
        return

    existing_as_of = instant(existing["as_of"])

    if existing_as_of != as_of:
        raise ValueError(
            "Priority run_id collision: the same cohort and model "
            "were already stored for a different as_of"
        )

    raise ValueError(
        "Priority run_id already exists for this as_of; "
        "the existing run must be reused"
    )
