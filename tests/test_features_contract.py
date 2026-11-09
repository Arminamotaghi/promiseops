import json
from datetime import date
from pathlib import Path

import pytest

from promiseops.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    FORBIDDEN_INPUTS,
    NUMERIC_FEATURES,
    cohort_hash,
    feature_contract_sha256,
    is_late,
    local_date,
)

ROOT = Path(__file__).resolve().parents[1]


def test_feature_list_matches_public_contract():
    assert list(FEATURES) == json.loads((ROOT / "contracts/features.json").read_text())
    assert not set(NUMERIC_FEATURES) & set(CATEGORICAL_FEATURES)
    names = json.loads((ROOT / "contracts/features.json").read_text())
    assert feature_contract_sha256() == feature_contract_sha256(names)


def test_no_forbidden_input_is_a_feature():
    assert not FORBIDDEN_INPUTS & set(FEATURES)


def test_local_date_uses_sao_paulo_not_utc():
    # 01:30 UTC on Jan 10 is 23:30 on Jan 9 in Sao Paulo (UTC-2, summer time in Jan 2018)
    assert local_date("2018-01-10T01:30:00+00:00") == date(2018, 1, 9)


@pytest.mark.parametrize(
    ("delivered_at", "promised", "expected"),
    [
        ("2018-01-09T23:00:00-02:00", "2018-01-09", False),  # same local day: on time
        ("2018-01-10T01:00:00+00:00", "2018-01-09", False),  # next UTC day, same local day
        ("2018-01-10T12:00:00-02:00", "2018-01-09", True),   # next local day: late
        ("2018-01-08T12:00:00+00:00", "2018-01-09", False),  # early
    ],
)
def test_late_label_compares_local_dates(delivered_at, promised, expected):
    assert is_late(delivered_at, promised) is expected


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError):
        is_late("2018-01-10T12:00:00", "2018-01-09")


def test_cohort_hash_ignores_order_and_duplicates():
    assert cohort_hash(["b", "a", "a"]) == cohort_hash(["a", "b"])
    assert cohort_hash(["a"]) != cohort_hash(["a", "b"])
