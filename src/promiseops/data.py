"""Own-team transactional ingestion; features live in one shared module."""

import json
from datetime import datetime
from promiseops.contracts import digest
from promiseops.features import build_features
from promiseops.ingest_checks import (
    IngestRejected,
    read_envelope,
    row_sha256,
    validate_content,
)


def ingest_batch(
    connection,
    payload: bytes,
    *,
    as_of: datetime,
    expected_sha256: str,
    expected_payload_sha256: str = None,
    file_name: str = "batch_payload.json",
) -> dict:
    """Execute idempotent transactional ingestion following data.md and CONTRACTS.md.

    Performs 11 steps:
    1. File SHA-256 checksum check against expected_sha256.
    2. Secure JSON parsing rejecting NaN/Infinity and duplicate keys.
    3. Payload SHA-256 digest validation.
    4. Prior receipt check (idempotent replay or conflict rejection).
    5. Pydantic schema validation.
    6. Future publication check (available_at <= as_of).
    7. Intra-batch duplicate key check.
    8. FK integrity and business rules (missing_fk, committed_order_extended).
    9. Immutability checks against existing rows (immutable_row_changed).
    10. Atomic transaction commit with advisory lock, dimension upserts, facts, and receipt.
    11. Rollback and separate quarantine logging on any failure.
    """

    def write_quarantine(conn, reason_code: str, detail: str, b_id=None, p_sha=None, f_as_of=None):
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO quarantine 
                    (file_name, batch_id, payload_sha256, file_sha256, reason_code, reason_detail, raw_payload, feed_as_of)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        file_name,
                        b_id,
                        p_sha,
                        expected_sha256,
                        reason_code,
                        detail,
                        payload,
                        f_as_of,
                    ),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

    batch_id = None
    payload_sha = None
    feed_as_of = as_of

    try:
        # Steps 1-3: File checksum, secure JSON parsing, and payload checksum
        envelope = read_envelope(
            payload,
            expected_file_sha256=expected_sha256,
            expected_payload_sha256=expected_payload_sha256,
        )
        payload_sha = envelope.payload_sha256
        batch_id = envelope.batch_id

        # Steps 5-8 (in-memory): Schema validation, publication window, duplicate keys, complete orders
        checked = validate_content(envelope.parsed, as_of=as_of)
        batch = checked.batch
        kind = batch.kind
        schema_version = batch.schema_version

        # Step 4, 8-10: Database Connection, Advisory Lock, Receipt Check & Insertion
        with connection.cursor() as cur:
            # 1. Advisory lock per batch_id
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (batch_id,))

            # 2. Receipt Lookup inside transaction
            cur.execute(
                "SELECT batch_id, kind, schema_version, available_at, payload_sha256, file_sha256, feed_as_of, row_counts "
                "FROM batch_receipts WHERE batch_id = %s",
                (batch_id,),
            )
            existing_receipt = cur.fetchone()

            if existing_receipt:
                if existing_receipt[4] == payload_sha:
                    connection.commit()
                    return {
                        "batch_id": existing_receipt[0],
                        "kind": existing_receipt[1],
                        "status": "accepted",
                        "replayed": True,
                        "payload_sha256": existing_receipt[4],
                        "row_counts": existing_receipt[7],
                    }
                else:
                    raise IngestRejected("batch_id_conflict", "batch_id exists with different payload hash")

            # Step 10: Upsert dimensions with ON CONFLICT DO NOTHING
            for c in batch.customers:
                c_sha = row_sha256(c)
                cur.execute("SELECT row_sha256 FROM customers WHERE customer_id = %s", (c.customer_id,))
                row = cur.fetchone()
                if row:
                    if row[0] != c_sha:
                        raise IngestRejected("immutable_row_changed", f"customer {c.customer_id} changed")
                else:
                    cur.execute(
                        """
                        INSERT INTO customers (customer_id, customer_state, row_sha256, first_batch_id)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (customer_id) DO NOTHING
                        """,
                        (c.customer_id, c.customer_state, c_sha, batch_id),
                    )

            for s in batch.sellers:
                s_sha = row_sha256(s)
                cur.execute("SELECT row_sha256 FROM sellers WHERE seller_id = %s", (s.seller_id,))
                row = cur.fetchone()
                if row:
                    if row[0] != s_sha:
                        raise IngestRejected("immutable_row_changed", f"seller {s.seller_id} changed")
                else:
                    cur.execute(
                        """
                        INSERT INTO sellers (seller_id, seller_state, row_sha256, first_batch_id)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (seller_id) DO NOTHING
                        """,
                        (s.seller_id, s.seller_state, s_sha, batch_id),
                    )

            for p in batch.products:
                p_sha = row_sha256(p)
                cur.execute("SELECT row_sha256 FROM products WHERE product_id = %s", (p.product_id,))
                row = cur.fetchone()
                if row:
                    if row[0] != p_sha:
                        raise IngestRejected("immutable_row_changed", f"product {p.product_id} changed")
                else:
                    cur.execute(
                        """
                        INSERT INTO products (product_id, category, weight_g, row_sha256, first_batch_id)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (product_id) DO NOTHING
                        """,
                        (p.product_id, p.category, p.weight_g, p_sha, batch_id),
                    )

            # Check preexisting orders to detect committed_order_extended
            child_order_ids = (
                {item.order_id for item in batch.items}
                | {pay.order_id for pay in batch.payments}
            )
            preexisting_child_order_ids = set()
            if child_order_ids:
                cur.execute(
                    "SELECT order_id FROM orders WHERE order_id = ANY(%s)",
                    (list(child_order_ids),),
                )
                preexisting_child_order_ids = {row[0] for row in cur.fetchall()}

            # Process Orders & Facts
            order_counts = len(batch.orders)
            item_counts = len(batch.items)
            payment_counts = len(batch.payments)
            outcome_counts = len(batch.outcomes)

            for o in batch.orders:
                o_sha = row_sha256(o)
                cur.execute("SELECT row_sha256, batch_id FROM orders WHERE order_id = %s", (o.order_id,))
                row = cur.fetchone()
                if row:
                    if row[0] != o_sha:
                        raise IngestRejected("immutable_row_changed", f"order {o.order_id} changed")
                else:
                    cur.execute("SELECT 1 FROM customers WHERE customer_id = %s", (o.customer_id,))
                    if not cur.fetchone():
                        raise IngestRejected("missing_fk", f"missing customer {o.customer_id}")

                    cur.execute(
                        """
                        INSERT INTO orders (order_id, customer_id, approved_at, promised_date, row_sha256, batch_id, released_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (o.order_id, o.customer_id, o.approved_at, o.promised_date, o_sha, batch_id, batch.available_at),
                    )

            # Items
            for item in batch.items:
                item_sha = row_sha256(item)
                cur.execute("SELECT row_sha256 FROM items WHERE order_id = %s AND item_id = %s", (item.order_id, item.item_id))
                row = cur.fetchone()
                if row:
                    if row[0] != item_sha:
                        raise IngestRejected("immutable_row_changed", f"item {item.order_id}#{item.item_id} changed")
                else:
                    if item.order_id in preexisting_child_order_ids:
                        raise IngestRejected("committed_order_extended", f"order {item.order_id} is already committed")

                    cur.execute("SELECT 1 FROM orders WHERE order_id = %s", (item.order_id,))
                    if not cur.fetchone():
                        raise IngestRejected("missing_fk", f"missing order {item.order_id}")

                    cur.execute(
                        """
                        INSERT INTO items (order_id, item_id, product_id, seller_id, price, freight, row_sha256)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (item.order_id, item.item_id, item.product_id, item.seller_id, item.price, item.freight, item_sha),
                    )

            # Payments
            for pay in batch.payments:
                pay_sha = row_sha256(pay)
                cur.execute("SELECT row_sha256 FROM payments WHERE order_id = %s AND sequence = %s", (pay.order_id, pay.sequence))
                row = cur.fetchone()
                if row:
                    if row[0] != pay_sha:
                        raise IngestRejected("immutable_row_changed", f"payment {pay.order_id}#{pay.sequence} changed")
                else:
                    if pay.order_id in preexisting_child_order_ids:
                        raise IngestRejected("committed_order_extended", f"order {pay.order_id} is already committed")

                    cur.execute("SELECT 1 FROM orders WHERE order_id = %s", (pay.order_id,))
                    if not cur.fetchone():
                        raise IngestRejected("missing_fk", f"missing order {pay.order_id}")

                    cur.execute(
                        """
                        INSERT INTO payments (order_id, sequence, payment_type, installments, value, row_sha256)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (pay.order_id, pay.sequence, pay.payment_type, pay.installments, pay.value, pay_sha),
                    )

            # Outcomes
            for out in batch.outcomes:
                out_sha = row_sha256(out)
                cur.execute("SELECT row_sha256 FROM outcomes WHERE order_id = %s", (out.order_id,))
                row = cur.fetchone()
                if row:
                    if row[0] != out_sha:
                        raise IngestRejected("immutable_row_changed", f"outcome {out.order_id} changed")
                else:
                    cur.execute("SELECT 1 FROM orders WHERE order_id = %s", (out.order_id,))
                    if not cur.fetchone():
                        raise IngestRejected("missing_fk", f"missing order {out.order_id}")

                    cur.execute(
                        """
                        INSERT INTO outcomes (order_id, delivered_at, available_at, row_sha256, batch_id, released_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (out.order_id, out.delivered_at, out.available_at, out_sha, batch_id, batch.available_at),
                    )

            row_counts = {
                "customers": len(batch.customers),
                "sellers": len(batch.sellers),
                "products": len(batch.products),
                "orders": order_counts,
                "items": item_counts,
                "payments": payment_counts,
                "outcomes": outcome_counts,
            }

            # Insert Receipt in the same transaction
            cur.execute(
                """
                INSERT INTO batch_receipts 
                (batch_id, kind, schema_version, available_at, payload_sha256, file_sha256, feed_as_of, row_counts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    batch_id,
                    kind,
                    schema_version,
                    batch.available_at,
                    payload_sha,
                    expected_sha256,
                    feed_as_of,
                    json.dumps(row_counts),
                ),
            )
        connection.commit()
        return {
            "batch_id": batch_id,
            "kind": kind,
            "status": "accepted",
            "replayed": False,
            "payload_sha256": payload_sha,
            "row_counts": row_counts,
        }

    except IngestRejected as e:
        try:
            connection.rollback()
        except Exception:
            pass

        try:
            write_quarantine(connection, e.reason_code, str(e), batch_id, payload_sha, feed_as_of)
        except Exception as q_err:
            print(f"CRITICAL: Failed to write quarantine record: {q_err}")

        raise ValueError(e.reason_code)

    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise



__all__ = ["build_features", "ingest_batch"]
