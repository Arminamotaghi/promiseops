import pytest
import json
import hashlib
from datetime import datetime, timezone
from promiseops.data import ingest_batch
from promiseops.contracts import digest

# ۱. شبیه‌ساز دیتابیس (Fake)
class FakeCursor:
    def __init__(self, result=None):
        self.result = result
    def execute(self, query, params=None): pass
    def fetchone(self): return self.result
    def __enter__(self): return self
    def __exit__(self, *args): pass

class FakeConnection:
    def __init__(self, fetch_result=None):
        self.fetch_result = fetch_result
    def cursor(self): return FakeCursor(self.fetch_result)
    def rollback(self): pass
    def commit(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass

# داده‌های معتبر مطابق با قرارداد اسکیما
PAYLOAD_DICT = {
    "batch_id": "b1",
    "kind": "snapshot",
    "schema_version": 1,
    "available_at": "2023-01-01T00:00:00Z",
    "customers": [],
    "sellers": [],
    "products": [],
    "orders": [],
    "items": [],
    "payments": [],
    "outcomes": []
}
PAYLOAD_BYTES = json.dumps(PAYLOAD_DICT).encode('utf-8')
CORRECT_SHA = hashlib.sha256(PAYLOAD_BYTES).hexdigest()
NOW = datetime.now(timezone.utc)

# --- تست‌های واحد ---

def test_checksum_mismatch():
    """تست شماره ۱: اگر چک‌سوم غلط باشد، باید خطا بدهد"""
    conn = FakeConnection()
    with pytest.raises(ValueError, match="checksum_mismatch"):
        ingest_batch(conn, PAYLOAD_BYTES, as_of=NOW, expected_sha256="wrong_sha")

def test_json_with_nan_rejected():
    """تست شماره ۲: اگر دیتای خراب (NaN) بیاید، باید رد شود"""
    bad_payload = b'{"batch_id": "b1", "value": NaN}'
    bad_sha = hashlib.sha256(bad_payload).hexdigest()
    conn = FakeConnection()
    with pytest.raises(ValueError, match="json_invalid"):
        ingest_batch(conn, bad_payload, as_of=NOW, expected_sha256=bad_sha)

def test_replay_idempotency():
    """تست شماره ۳: اگر دیتابیس بگوید قبلاً این فایل را دیده، باید replayed: true برگرداند"""
    payload_sha = digest(PAYLOAD_DICT)
    
    # ساختن رکورد کاملِ بازگشتی از جدول batch_receipts (۸ ستون مطابق خط ۸۷ کد اصلی)
    # (batch_id, kind, schema_version, available_at, payload_sha256, file_sha256, feed_as_of, row_counts)
    row_counts_json = json.dumps({"customers": 0, "sellers": 0, "products": 0, "orders": 0, "items": 0, "payments": 0, "outcomes": 0})
    fake_db_record = ("b1", "snapshot", 1, "2023-01-01", payload_sha, CORRECT_SHA, "2023-01-01", row_counts_json)
    
    conn = FakeConnection(fetch_result=fake_db_record)
    
    result = ingest_batch(conn, PAYLOAD_BYTES, as_of=NOW, expected_sha256=CORRECT_SHA)
    
    assert result["replayed"] is True
    assert result["status"] == "accepted"
