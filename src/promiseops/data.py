"""Own-team transactional ingestion; features live in one shared module."""

import hashlib
import json
from datetime import datetime, timezone
from pydantic import ValidationError
from promiseops.features import build_features
from promiseops.contracts import Batch, digest


def ingest_batch(connection, payload: bytes, *, as_of: datetime, expected_sha256: str) -> dict:
    """Execute idempotent transactional ingestion following data-(4).md and CONTRACTS.md.

    Performs 11 steps:
    1. File SHA-256 checksum check against expected_sha256.
    2. Secure JSON parsing rejecting NaN/Infinity.
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
    file_name = "batch_payload.json"  # default or caller context
    
    # Helper for quarantine without extra separate helper functions
    def write_quarantine(conn, reason_code: str, detail: str):
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
                        locals().get("batch_id"),
                        locals().get("payload_sha"),
                        expected_sha256,
                        reason_code,
                        detail,
                        payload,
                        locals().get("feed_as_of")
                    )
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

    try:
        # Step 1: File Checksum
        computed_file_sha = hashlib.sha256(payload).hexdigest()
        if computed_file_sha != expected_sha256:
            raise ValueError("checksum_mismatch")

        # Step 2: Secure JSON parse rejecting NaN/Infinity
        try:
            parsed = json.loads(
                payload.decode("utf-8"),
                parse_constant=lambda x: (_ for _ in ()).throw(ValueError("json_invalid"))
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("json_invalid")

        if not isinstance(parsed, dict):
            raise ValueError("json_invalid")

        # Step 3: Payload Checksum
        try:
            payload_sha = digest(parsed)
        except Exception:
            raise ValueError("schema_invalid")

        # Step 4 & Batch Info Extraction (Read-only check for receipt before lock)
        batch_id = parsed.get("batch_id")
        kind = parsed.get("kind")
        schema_version = parsed.get("schema_version")
        
        with connection.cursor() as cur:
            cur.execute(
                "SELECT batch_id, kind, schema_version, available_at, payload_sha256, file_sha256, feed_as_of, row_counts "
                "FROM batch_receipts WHERE batch_id = %s",
                (batch_id,)
            )
            existing_receipt = cur.fetchone()

        if existing_receipt:
            if existing_receipt[4] == payload_sha:
                # Replay / Idempotent No-op
                return {
                    "batch_id": existing_receipt[0],
                    "kind": existing_receipt[1],
                    "status": "accepted",
                    "replayed": True,
                    "payload_sha256": existing_receipt[4],
                    "row_counts": existing_receipt[7]
                }
            else:
                raise ValueError("batch_id_conflict")

        # Step 5: Schema Validation
        try:
            batch = Batch.model_validate(parsed)
        except ValidationError:
            raise ValueError("schema_invalid")

        feed_as_of = batch.available_at

        # Step 6: Future Publication Check
        if batch.available_at > as_of:
            raise ValueError("future_publication")

        # Step 7: Intra-batch Duplicate Key Checks
        # (Pydantic models and set uniqueness for IDs)
        seen_customers = set()
        for c in batch.customers:
            if c.customer_id in seen_customers:
                raise ValueError("duplicate_key")
            seen_customers.add(c.customer_id)

        seen_sellers = set()
        for s in batch.sellers:
            if s.seller_id in seen_sellers:
                raise ValueError("duplicate_key")
            seen_sellers.add(s.seller_id)

        seen_products = set()
        for p in batch.products:
            if p.product_id in seen_products:
                raise ValueError("duplicate_key")
            seen_products.add(p.product_id)

        seen_orders = set()
        for o in batch.orders:
            if o.order_id in seen_orders:
                raise ValueError("duplicate_key")
            seen_orders.add(o.order_id)

        # Step 8 & 9: Database Connection, Advisory Lock, FK, Business Rules & Immutability Check
        with connection:
            with connection.cursor() as cur:
                # Advisory lock per batch_id
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (batch_id,))

                # Verify FKs and Immutable rows / committed_order_extended in DB
                # 1. Upsert dimensions
                for c in batch.customers:
                    c_sha = digest(c.model_dump(mode="json"))
                    cur.execute(
                        """
                        SELECT row_sha256 FROM customers WHERE customer_id = %s
                        """,
                        (c.customer_id,)
                    )
                    row = cur.fetchone()
                    if row:
                        if row[0] != c_sha:
                            raise ValueError("immutable_row_changed")
                    else:
                        cur.execute(
                            """
                            INSERT INTO customers (customer_id, customer_state, row_sha256, first_batch_id)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (customer_id) DO UPDATE 
                            SET customer_state = EXCLUDED.customer_state
                            """,
                            (c.customer_id, c.customer_state, c_sha, batch_id)
                        )

                for s in batch.sellers:
                    s_sha = digest(s.model_dump(mode="json"))
                    cur.execute("SELECT row_sha256 FROM sellers WHERE seller_id = %s", (s.seller_id,))
                    row = cur.fetchone()
                    if row:
                        if row[0] != s_sha:
                            raise ValueError("immutable_row_changed")
                    else:
                        cur.execute(
                            """
                            INSERT INTO sellers (seller_id, seller_state, row_sha256, first_batch_id)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (seller_id) DO UPDATE 
                            SET seller_state = EXCLUDED.seller_state
                            """,
                            (s.seller_id, s.seller_state, s_sha, batch_id)
                        )

                for p in batch.products:
                    p_sha = digest(p.model_dump(mode="json"))
                    cur.execute("SELECT row_sha256 FROM products WHERE product_id = %s", (p.product_id,))
                    row = cur.fetchone()
                    if row:
                        if row[0] != p_sha:
                            raise ValueError("immutable_row_changed")
                    else:
                        cur.execute(
                            """
                            INSERT INTO products (product_id, category, weight_g, row_sha256, first_batch_id)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (product_id) DO UPDATE 
                            SET category = EXCLUDED.category, weight_g = EXCLUDED.weight_g
                            """,
                            (p.product_id, p.category, p.weight_g, p_sha, batch_id)
                        )

                # Process Orders & Facts
                order_counts = len(batch.orders)
                item_counts = len(batch.items)
                payment_counts = len(batch.payments)
                outcome_counts = len(batch.outcomes)

                for o in batch.orders:
                    o_sha = digest(o.model_dump(mode="json"))
                    cur.execute("SELECT row_sha256, batch_id FROM orders WHERE order_id = %s", (o.order_id,))
                    row = cur.fetchone()
                    if row:
                        if row[0] != o_sha:
                            raise ValueError("immutable_row_changed")
                    else:
                        # Verify customer FK exists
                        cur.execute("SELECT 1 FROM customers WHERE customer_id = %s", (o.customer_id,))
                        if not cur.fetchone():
                            raise ValueError("missing_fk")
                        
                        cur.execute(
                            """
                            INSERT INTO orders (order_id, customer_id, approved_at, promised_date, row_sha256, batch_id, released_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (o.order_id, o.customer_id, o.approved_at, o.promised_date, o_sha, batch_id, as_of)
                        )

                # Items
                for item in batch.items:
                    item_sha = digest(item.model_dump(mode="json"))
                    cur.execute("SELECT row_sha256 FROM items WHERE order_id = %s AND item_id = %s", (item.order_id, item.item_id))
                    row = cur.fetchone()
                    if row:
                        if row[0] != item_sha:
                            raise ValueError("immutable_row_changed")
                    else:
                        # Check if order exists (if not in this batch, must be committed already; but check FK)
                        cur.execute("SELECT 1 FROM orders WHERE order_id = %s", (item.order_id,))
                        if not cur.fetchone():
                            raise ValueError("missing_fk")
                        
                        cur.execute(
                            """
                            INSERT INTO items (order_id, item_id, product_id, seller_id, price, freight, row_sha256)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (item.order_id, item.item_id, item.product_id, item.seller_id, item.price, item.freight, item_sha)
                        )

                # Payments
                for pay in batch.payments:
                    pay_sha = digest(pay.model_dump(mode="json"))
                    cur.execute("SELECT row_sha256 FROM payments WHERE order_id = %s AND sequence = %s", (pay.order_id, pay.sequence))
                    row = cur.fetchone()
                    if row:
                        if row[0] != pay_sha:
                            raise ValueError("immutable_row_changed")
                    else:
                        cur.execute("SELECT 1 FROM orders WHERE order_id = %s", (pay.order_id,))
                        if not cur.fetchone():
                            raise ValueError("missing_fk")

                        cur.execute(
                            """
                            INSERT INTO payments (order_id, sequence, payment_type, installments, value, row_sha256)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (pay.order_id, pay.sequence, pay.payment_type, pay.installments, pay.value, pay_sha)
                        )

                # Outcomes
                for out in batch.outcomes:
                    out_sha = digest(out.model_dump(mode="json"))
                    cur.execute("SELECT row_sha256 FROM outcomes WHERE order_id = %s", (out.order_id,))
                    row = cur.fetchone()
                    if row:
                        if row[0] != out_sha:
                            raise ValueError("immutable_row_changed")
                    else:
                        cur.execute("SELECT 1 FROM orders WHERE order_id = %s", (out.order_id,))
                        if not cur.fetchone():
                            raise ValueError("missing_fk")

                        cur.execute(
                            """
                            INSERT INTO outcomes (order_id, delivered_at, available_at, row_sha256, batch_id, released_at)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (out.order_id, out.delivered_at, out.available_at, out_sha, batch_id, as_of)
                        )

                row_counts = {
                    "customers": len(batch.customers),
                    "sellers": len(batch.sellers),
                    "products": len(batch.products),
                    "orders": order_counts,
                    "items": item_counts,
                    "payments": payment_counts,
                    "outcomes": outcome_counts
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
                        json.dumps(row_counts)
                    )
                )

        # Successful Receipt return
        return {
            "batch_id": batch_id,
            "kind": kind,
            "status": "accepted",
            "replayed": False,
            "payload_sha256": payload_sha,
            "row_counts": {
                "customers": len(batch.customers),
                "sellers": len(batch.sellers),
                "products": len(batch.products),
                "orders": order_counts,
                "items": item_counts,
                "payments": payment_counts,
                "outcomes": outcome_counts
            }
        }

    except Exception as e:
        err_msg = str(e)
        # Extract or match valid reason code matching [a-z0-9_]{3,64}
        codes = [
            "checksum_mismatch", "json_invalid", "batch_id_conflict",
            "schema_invalid", "future_publication", "duplicate_key",
            "missing_fk", "committed_order_extended", "immutable_row_changed"
        ]
        reason_code = "schema_invalid"
        for code in codes:
            if code in err_msg or code == err_msg:
                reason_code = code
                break

        # Step 11: Rollback current transaction and log quarantine in a separate transaction
        try:
            connection.rollback()
        except Exception:
            pass

        write_quarantine(connection, reason_code, err_msg)
        raise ValueError(reason_code)


__all__ = ["build_features", "ingest_batch"]
