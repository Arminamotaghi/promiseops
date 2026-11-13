import copy
import hashlib
import json
from pathlib import Path

import pytest

from promiseops.contracts import Batch, digest
from promiseops.ingest_checks import (
    REASON_CODES,
    IngestRejected,
    external_references,
    read_envelope,
    row_sha256,
    validate_content,
)

ROOT = Path(__file__).resolve().parents[1]
ORDERS_RAW = (ROOT / "fixtures" / "synthetic-orders.json").read_bytes()
OUTCOMES_RAW = (ROOT / "fixtures" / "synthetic-outcomes.json").read_bytes()
AS_OF = "2018-01-01T00:00:00Z"


def orders() -> dict:
    return json.loads(ORDERS_RAW)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def rejected(code: str):
    return pytest.raises(IngestRejected, match=f"^{code}: ")


# ---------- steps 1-3: envelope ----------
def test_valid_fixture_passes_all_database_free_steps():
    envelope = read_envelope(
        ORDERS_RAW, expected_file_sha256=sha(ORDERS_RAW), expected_payload_sha256=digest(orders())
    )
    assert envelope.batch_id == "synthetic-orders-13-300"
    assert envelope.file_sha256 == sha(ORDERS_RAW)
    assert envelope.payload_sha256 == digest(orders())
    checked = validate_content(envelope.parsed, as_of=AS_OF)
    assert len(checked.batch.orders) == 300
    assert checked.external.customer_ids == ()
    assert checked.external.line_order_ids == ()


def test_file_checksum_mismatch_is_rejected_before_parsing():
    with rejected("checksum_mismatch"):
        read_envelope(b"not json at all", expected_file_sha256="0" * 64)


def test_payload_checksum_is_a_different_value_and_is_enforced():
    assert sha(ORDERS_RAW) != digest(orders())
    with rejected("checksum_mismatch"):
        # passing the FILE hash where the PAYLOAD hash belongs must fail
        read_envelope(ORDERS_RAW, expected_file_sha256=sha(ORDERS_RAW), expected_payload_sha256=sha(ORDERS_RAW))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"batch_id": "b1", "value": NaN}',
        b'{"batch_id": "b1", "value": -Infinity}',
        b'{"batch_id": "b1", "value": 1e999}',
        b'{"batch_id": "b1", "batch_id": "b2"}',
        b'["not", "an", "object"]',
        b'{"batch_id": "b1"',
        b"\xff\xfe",
    ],
)
def test_malformed_json_is_rejected(raw):
    with rejected("json_invalid"):
        read_envelope(raw, expected_file_sha256=sha(raw))


def test_unsafe_batch_id_is_not_exposed():
    raw = b'{"batch_id": "../../etc/passwd"}'
    assert read_envelope(raw, expected_file_sha256=sha(raw)).batch_id is None


# ---------- step 5: schema ----------
def mutate(change) -> dict:
    payload = orders()
    change(payload)
    return payload


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(kind="snapshot"),
        lambda p: p.update(schema_version="2.0"),
        lambda p: p.update(surprise=1),
        lambda p: p["orders"][0].update(status="delivered"),
        lambda p: p["items"][0].update(price=-1),
        lambda p: p["payments"][0].update(value=10_000_001),
        lambda p: p["orders"][0].update(approved_at="2017-01-01T16:00:00"),
        lambda p: p["orders"][0].update(promised_date="2016-12-01"),
        lambda p: p["orders"][0].update(order_id="../../secret"),
        lambda p: p.update(outcomes=json.loads(OUTCOMES_RAW)["outcomes"][:1]),
    ],
)
def test_contract_violations_are_schema_invalid(change):
    with rejected("schema_invalid"):
        validate_content(mutate(change), as_of=AS_OF)


def test_schema_error_detail_does_not_echo_input_values():
    payload = mutate(lambda p: p["orders"][0].update(order_id="../../secret-value"))
    with pytest.raises(IngestRejected) as caught:
        validate_content(payload, as_of=AS_OF)
    assert "secret-value" not in str(caught.value)
    assert "orders.0.order_id" in caught.value.detail


# ---------- step 6: publication ----------
def test_batch_published_after_as_of_is_rejected():
    with rejected("future_publication"):
        validate_content(orders(), as_of="2017-04-14T23:59:59Z")
    validate_content(orders(), as_of="2017-04-15T00:00:00Z")  # equal is visible


def test_naive_as_of_is_a_caller_bug_not_a_rejection():
    with pytest.raises(ValueError) as caught:
        validate_content(orders(), as_of="2018-01-01T00:00:00")
    assert not isinstance(caught.value, IngestRejected)


# ---------- step 7: duplicate keys ----------
@pytest.mark.parametrize("table", ["customers", "sellers", "products", "orders", "items", "payments"])
def test_duplicate_key_in_any_order_table_is_rejected(table):
    payload = mutate(lambda p: p[table].append(copy.deepcopy(p[table][0])))
    with rejected("duplicate_key") as caught:
        validate_content(payload, as_of=AS_OF)
    assert table in caught.value.detail


def test_duplicate_outcome_is_rejected():
    payload = json.loads(OUTCOMES_RAW)
    payload["outcomes"].append(copy.deepcopy(payload["outcomes"][0]))
    with rejected("duplicate_key"):
        validate_content(payload, as_of=AS_OF)


# ---------- step 8: in-batch rules ----------
@pytest.mark.parametrize("table", ["items", "payments"])
def test_order_without_item_or_payment_is_rejected(table):
    payload = mutate(lambda p: p.update({table: [row for row in p[table] if row["order_id"] != "o000000"]}))
    with rejected("incomplete_order"):
        validate_content(payload, as_of=AS_OF)


def test_external_references_list_only_keys_missing_from_the_batch():
    payload = orders()
    payload["customers"] = payload["customers"][1:]  # c000000 must come from stored rows
    payload["items"].append({**payload["items"][0], "order_id": "o-earlier", "item_id": 2})
    payload["payments"].append({**payload["payments"][0], "order_id": "o-earlier", "sequence": 9})
    refs = validate_content(payload, as_of=AS_OF).external
    assert refs.customer_ids == ("c000000",)
    assert refs.line_order_ids == ("o-earlier",)  # stored -> committed_order_extended, unknown -> missing_fk
    assert refs.product_ids == () and refs.seller_ids == ()


def test_outcome_batch_references_every_order_externally():
    refs = external_references(Batch.model_validate(json.loads(OUTCOMES_RAW)))
    assert len(refs.outcome_order_ids) == 300
    assert refs.customer_ids == () and refs.line_order_ids == ()


# ---------- row hashes ----------
def test_row_hash_is_stable_and_detects_changes():
    batch = Batch.model_validate(orders())
    again = Batch.model_validate(orders())
    assert row_sha256(batch.orders[0]) == row_sha256(again.orders[0])
    changed = again.products[0].model_copy(update={"weight_g": 701.0})
    assert row_sha256(batch.products[0]) != row_sha256(changed)
    # an equivalent timestamp spelling is the same row
    shifted = mutate(lambda p: p["orders"][0].update(approved_at="2017-01-01T13:00:00-03:00"))
    assert row_sha256(Batch.model_validate(shifted).orders[0]) == row_sha256(batch.orders[0])


def test_reason_codes_fit_the_quarantine_constraint():
    assert all(3 <= len(code) <= 64 and code.replace("_", "").isalnum() and code.islower() for code in REASON_CODES)
    with pytest.raises(ValueError):
        IngestRejected("made_up_code", "x")
