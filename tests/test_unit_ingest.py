"""Database integration unit tests for ingest_batch."""

import hashlib
import json
import pytest
from datetime import datetime, timezone

from promiseops.contracts import digest
from zoneinfo import ZoneInfo
from promiseops.data import ingest_batch


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=None):
        self.conn.executed_queries.append((query.strip(), params))

    def fetchone(self):
        if self.conn.fetchone_queue:
            return self.conn.fetchone_queue.pop(0)
        return self.conn.default_fetchone

    def fetchall(self):
        if self.conn.fetchall_queue:
            return self.conn.fetchall_queue.pop(0)
        return self.conn.default_fetchall

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeConnection:
    def __init__(self, default_fetchone=None, default_fetchall=None):
        self.default_fetchone = default_fetchone
        self.default_fetchall = default_fetchall if default_fetchall is not None else []
        self.fetchone_queue = []
        self.fetchall_queue = []
        self.executed_queries = []
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rollback_count += 1

    def commit(self):
        self.commit_count += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


FIXED_NOW = datetime(2023, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

VALID_PAYLOAD_DICT = {
    "batch_id": "b1",
    "kind": "orders",
    "schema_version": "1.0",
    "available_at": "2023-01-01T00:00:00Z",
    "customers": [{"customer_id": "c1", "customer_state": "SP"}],
    "sellers": [{"seller_id": "s1", "seller_state": "SP"}],
    "products": [{"product_id": "p1", "category": "auto", "weight_g": 100}],
    "orders": [
        {
            "order_id": "o1",
            "customer_id": "c1",
            "approved_at": "2023-01-01T00:00:00Z",
            "promised_date": "2023-01-10T00:00:00Z",
        }
    ],
    "items": [
        {
            "order_id": "o1",
            "item_id": 1,
            "product_id": "p1",
            "seller_id": "s1",
            "price": 50.0,
            "freight": 10.0,
        }
    ],
    "payments": [
        {
            "order_id": "o1",
            "sequence": 1,
            "payment_type": "credit_card",
            "installments": 1,
            "value": 60.0,
        }
    ],
    "outcomes": [],
}

PAYLOAD_BYTES = json.dumps(VALID_PAYLOAD_DICT).encode("utf-8")
CORRECT_FILE_SHA = hashlib.sha256(PAYLOAD_BYTES).hexdigest()
CORRECT_PAYLOAD_SHA = digest(VALID_PAYLOAD_DICT)


def test_checksum_mismatch():
    """Rejects a batch when the file checksum does not match the expected checksum."""
    conn = FakeConnection()
    with pytest.raises(ValueError, match="^checksum_mismatch$"):
        ingest_batch(conn, PAYLOAD_BYTES, as_of=FIXED_NOW, expected_sha256="wrong_sha")


def test_payload_sha_mismatch():
    """Rejects a batch when the payload checksum does not match the expected payload checksum."""
    conn = FakeConnection()
    with pytest.raises(ValueError, match="^checksum_mismatch$"):
        ingest_batch(
            conn,
            PAYLOAD_BYTES,
            as_of=FIXED_NOW,
            expected_sha256=CORRECT_FILE_SHA,
            expected_payload_sha256="wrong_payload_sha",
        )


def test_json_with_nan_rejected():
    """Rejects JSON payloads containing non-standard values such as NaN."""

    bad_payload = b'{"batch_id": "b1", "value": NaN}'
    bad_sha = hashlib.sha256(bad_payload).hexdigest()
    conn = FakeConnection()
    with pytest.raises(ValueError, match="^json_invalid$"):
        ingest_batch(conn, bad_payload, as_of=FIXED_NOW, expected_sha256=bad_sha)


def test_replay_idempotency():
    """Returns the existing accepted receipt without reprocessing an already ingested batch."""
    row_counts_json = json.dumps({"orders": 1, "items": 1, "payments": 1})
    fake_db_record = (
        "b1",
        "orders",
        "1.0",
        "2023-01-01",
        CORRECT_PAYLOAD_SHA,
        CORRECT_FILE_SHA,
        "2023-06-01",
        row_counts_json,
    )

    conn = FakeConnection()
    conn.fetchone_queue = [fake_db_record]

    result = ingest_batch(
        conn,
        PAYLOAD_BYTES,
        as_of=FIXED_NOW,
        expected_sha256=CORRECT_FILE_SHA,
        expected_payload_sha256=CORRECT_PAYLOAD_SHA,
    )

    assert result["replayed"] is True
    assert result["status"] == "accepted"


def test_batch_id_conflict_raises_and_quarantines():
    """Raises batch_id_conflict and quarantines the batch when an existing batch ID has a different payload checksum."""
    fake_db_record = (
        "b1",
        "orders",
        "1.0",
        "2023-01-01",
        "different_payload_sha",
        CORRECT_FILE_SHA,
        "2023-06-01",
        "{}",
    )

    conn = FakeConnection()
    conn.fetchone_queue = [fake_db_record]

    with pytest.raises(ValueError, match="^batch_id_conflict$"):
        ingest_batch(
            conn,
            PAYLOAD_BYTES,
            as_of=FIXED_NOW,
            expected_sha256=CORRECT_FILE_SHA,
        )

    assert conn.rollback_count >= 1
    assert any("INSERT INTO quarantine" in query for query, _ in conn.executed_queries)


def test_committed_order_extended_detected_and_rejected():
    """Rejects and quarantines attempts to add items or payments to an already committed order."""
    conn = FakeConnection()
    conn.fetchone_queue = [
        None,  # receipt lookup (None -> new batch)
        None,  # select customer (None -> insert customer)
        None,  # select seller
        None,  # select product
    ]
    conn.fetchall_queue = [
        [("o1",)]  # preexisting_child_order_ids: order o1 is already committed in DB!
    ]

    with pytest.raises(ValueError, match="^committed_order_extended$"):
        ingest_batch(
            conn,
            PAYLOAD_BYTES,
            as_of=FIXED_NOW,
            expected_sha256=CORRECT_FILE_SHA,
            expected_payload_sha256=CORRECT_PAYLOAD_SHA,
        )

    assert conn.rollback_count >= 1
    assert any("INSERT INTO quarantine" in query for query, _ in conn.executed_queries)


def test_dimension_on_conflict_do_nothing():
    """Verifies that duplicate dimension records are ignored using ON CONFLICT DO NOTHING."""
    conn = FakeConnection()
    conn.fetchone_queue = [
        None,  # receipt check -> new batch
        None,  # select customer -> None
        None,  # select seller -> None
        None,  # select product -> None
        None,  # select order -> None
        (1,),  # customer FK check -> found
        None,  # select item -> None
        (1,),  # order FK for item -> found
        None,  # select payment -> None
        (1,),  # order FK for payment -> found
    ]
    conn.fetchall_queue = [
        []  # child order ids -> empty
    ]

    result = ingest_batch(
        conn,
        PAYLOAD_BYTES,
        as_of=FIXED_NOW,
        expected_sha256=CORRECT_FILE_SHA,
        expected_payload_sha256=CORRECT_PAYLOAD_SHA,
    )

    assert result["replayed"] is False
    assert result["status"] == "accepted"
    customer_inserts = [
        q for q, _ in conn.executed_queries if "INSERT INTO customers" in q
    ]
    assert len(customer_inserts) == 1
    assert "ON CONFLICT (customer_id) DO NOTHING" in customer_inserts[0]
from zoneinfo import ZoneInfo


def test_future_publication_rejected_and_quarantined():
    """Rejects and quarantines a batch published after the specified as_of cutoff."""
    conn = FakeConnection()
    past_as_of = datetime(2022, 12, 1, 0, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="^future_publication$"):
        ingest_batch(
            conn,
            PAYLOAD_BYTES,
            as_of=past_as_of,
            expected_sha256=CORRECT_FILE_SHA,
        )

    assert conn.rollback_count >= 1
    quarantine_queries = [
        (query, params)
        for query, params in conn.executed_queries
        if "INSERT INTO quarantine" in query
    ]
    assert len(quarantine_queries) == 1

    query, params = quarantine_queries[0]
    assert "future_publication" in params


def test_cutoff_comparison_with_sao_paulo_timezone():
    """Verifies that publication cutoff comparisons correctly handle the America/Sao_Paulo timezone."""
    conn = FakeConnection()
    sao_paulo_tz = ZoneInfo("America/Sao_Paulo")
    valid_local_as_of = datetime(2023, 1, 1, 10, 0, 0, tzinfo=sao_paulo_tz)

    res = ingest_batch(
        conn,
        PAYLOAD_BYTES,
        as_of=valid_local_as_of,
        expected_sha256=CORRECT_FILE_SHA,
    )
    assert res["status"] == "accepted"
    assert res["replayed"] is False