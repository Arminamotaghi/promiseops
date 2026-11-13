"""Batch validation that needs no database (steps 1-3 and 5-8 of docs/design/data.md).

Every rejection raises :class:`IngestRejected` with a stable ``reason_code``; anything else
(a bug, an unreachable database) is NOT a rejection and must propagate so the caller retries
instead of quarantining a healthy batch.

Intended use inside the transactional ``ingest_batch``::

    envelope = read_envelope(raw, expected_file_sha256=..., expected_payload_sha256=...)
    # step 4: look up a prior receipt by envelope.batch_id / envelope.payload_sha256
    checked = validate_content(envelope.parsed, as_of=as_of)
    # steps 8-10 against stored rows: checked.external, row_sha256(row), then commit
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from promiseops.contracts import SAFE_ID, Batch, Row, digest, instant

# Shared vocabulary for quarantine.reason_code (matches ^[a-z0-9_]{3,64}$).
REASON_CODES: frozenset[str] = frozenset(
    {
        "checksum_mismatch",
        "json_invalid",
        "schema_invalid",
        "future_publication",
        "duplicate_key",
        "incomplete_order",
        # raised by the database-side checks:
        "batch_id_conflict",
        "missing_fk",
        "committed_order_extended",
        "immutable_row_changed",
    }
)

_SAFE_ID = re.compile(SAFE_ID)


class IngestRejected(ValueError):
    """The batch itself is invalid. ``reason_code`` goes to quarantine; ``detail`` has no secrets."""

    def __init__(self, reason_code: str, detail: str):
        if reason_code not in REASON_CODES:
            raise ValueError(f"unknown reason_code: {reason_code}")
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class Envelope:
    """Result of steps 1-3. ``batch_id`` is None when the payload has no safe identifier."""

    parsed: dict
    file_sha256: str
    payload_sha256: str
    batch_id: str | None


@dataclass(frozen=True)
class ExternalRefs:
    """Keys the batch points at but does not carry; the database-side checks resolve them.

    * customer/product/seller IDs must already be stored, else ``missing_fk``
    * ``line_order_ids`` (orders referenced by items/payments but absent from this batch):
      stored order -> ``committed_order_extended``; unknown order -> ``missing_fk``
    * ``outcome_order_ids`` must already be stored, else ``missing_fk``
    """

    customer_ids: tuple[str, ...]
    product_ids: tuple[str, ...]
    seller_ids: tuple[str, ...]
    line_order_ids: tuple[str, ...]
    outcome_order_ids: tuple[str, ...]


@dataclass(frozen=True)
class CheckedBatch:
    batch: Batch
    external: ExternalRefs


def file_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def row_sha256(row: Row) -> str:
    """Stored in ``row_sha256``; always computed from the validated row, never from raw JSON."""
    return digest(row.model_dump(mode="json"))


def _reject_constant(name: str):
    raise ValueError(f"non-finite number {name}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate object key")
    return result


def read_envelope(
    raw: bytes, *, expected_file_sha256: str, expected_payload_sha256: str | None = None
) -> Envelope:
    """Steps 1-3: file checksum, strict JSON parse, payload checksum.

    ``expected_file_sha256`` hashes the file bytes; ``expected_payload_sha256`` (from the feed
    manifest) hashes the canonical JSON via ``contracts.digest``. They are different values.
    """
    actual_file = file_sha256(raw)
    if actual_file != expected_file_sha256:
        raise IngestRejected("checksum_mismatch", "file sha256 differs from the feed manifest")
    try:
        parsed = json.loads(
            raw.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_object
        )
        payload_sha = digest(parsed)  # allow_nan=False also rejects overflowed numbers such as 1e999
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise IngestRejected("json_invalid", str(exc)[:200]) from None
    if not isinstance(parsed, dict):
        raise IngestRejected("json_invalid", "top level is not an object")
    if expected_payload_sha256 is not None and payload_sha != expected_payload_sha256:
        raise IngestRejected("checksum_mismatch", "payload sha256 differs from the feed manifest")
    batch_id = parsed.get("batch_id")
    safe = isinstance(batch_id, str) and _SAFE_ID.fullmatch(batch_id) is not None
    return Envelope(parsed, actual_file, payload_sha, batch_id if safe else None)


def validate_schema(parsed: dict) -> Batch:
    """Step 5: the public contract (unknown columns, types, kind, timestamps, amounts)."""
    try:
        return Batch.model_validate(parsed)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False, include_context=False)
        shown = "; ".join(f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in errors[:5])
        raise IngestRejected("schema_invalid", f"{len(errors)} error(s): {shown}"[:500]) from None


def check_publication(batch: Batch, as_of: datetime | str) -> None:
    """Step 6: a batch published after the run's ``as_of`` is not visible yet."""
    cutoff = instant(as_of)
    if batch.available_at > cutoff:
        raise IngestRejected(
            "future_publication",
            f"batch available_at {batch.available_at.isoformat()} is after as_of {cutoff.isoformat()}",
        )


def _first_duplicate(keys: Iterable) -> object | None:
    seen = set()
    for key in keys:
        if key in seen:
            return key
        seen.add(key)
    return None


def check_duplicate_keys(batch: Batch) -> None:
    """Step 7: no primary key may repeat inside one batch, in any table."""
    tables = {
        "customers": (row.customer_id for row in batch.customers),
        "sellers": (row.seller_id for row in batch.sellers),
        "products": (row.product_id for row in batch.products),
        "orders": (row.order_id for row in batch.orders),
        "items": ((row.order_id, row.item_id) for row in batch.items),
        "payments": ((row.order_id, row.sequence) for row in batch.payments),
        "outcomes": (row.order_id for row in batch.outcomes),
    }
    for table, keys in tables.items():
        duplicate = _first_duplicate(keys)
        if duplicate is not None:
            raise IngestRejected("duplicate_key", f"{table}: key {duplicate!r} appears more than once")


def check_complete_orders(batch: Batch) -> None:
    """Step 8 (in-batch part): every order arrives with at least one item and one payment."""
    with_items = {row.order_id for row in batch.items}
    with_payments = {row.order_id for row in batch.payments}
    for order in batch.orders:
        if order.order_id not in with_items:
            raise IngestRejected("incomplete_order", f"order {order.order_id} has no item in this batch")
        if order.order_id not in with_payments:
            raise IngestRejected("incomplete_order", f"order {order.order_id} has no payment in this batch")


def external_references(batch: Batch) -> ExternalRefs:
    """Keys referenced by the batch but not carried by it (resolved against stored rows)."""
    customers = {row.customer_id for row in batch.customers}
    products = {row.product_id for row in batch.products}
    sellers = {row.seller_id for row in batch.sellers}
    orders = {row.order_id for row in batch.orders}
    line_orders = {row.order_id for row in batch.items} | {row.order_id for row in batch.payments}
    return ExternalRefs(
        customer_ids=tuple(sorted({row.customer_id for row in batch.orders} - customers)),
        product_ids=tuple(sorted({row.product_id for row in batch.items} - products)),
        seller_ids=tuple(sorted({row.seller_id for row in batch.items} - sellers)),
        line_order_ids=tuple(sorted(line_orders - orders)),
        outcome_order_ids=tuple(sorted({row.order_id for row in batch.outcomes})),
    )


def validate_content(parsed: dict, *, as_of: datetime | str) -> CheckedBatch:
    """Steps 5-8 that need no database, in contract order."""
    batch = validate_schema(parsed)
    check_publication(batch, as_of)
    check_duplicate_keys(batch)
    check_complete_orders(batch)
    return CheckedBatch(batch, external_references(batch))


__all__ = [
    "REASON_CODES",
    "CheckedBatch",
    "Envelope",
    "ExternalRefs",
    "IngestRejected",
    "check_complete_orders",
    "check_duplicate_keys",
    "check_publication",
    "external_references",
    "file_sha256",
    "read_envelope",
    "row_sha256",
    "validate_content",
    "validate_schema",
]
