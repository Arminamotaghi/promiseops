import inspect
import json
from datetime import date
from pathlib import Path

import pytest

from promiseops.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    FEATURE_CONTRACT_SHA256,
    FORBIDDEN_INPUTS,
    LABEL_COLUMNS,
    NUMERIC_FEATURES,
    build_features,
    cohort_hash,
    feature_contract_sha256,
    is_late,
    local_date,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FEATURES = json.loads((ROOT / "contracts/features.json").read_text())


def test_feature_list_matches_public_contract():
    assert list(FEATURES) == PUBLIC_FEATURES
    assert len(FEATURES) == 15
    assert len(set(FEATURES)) == len(FEATURES)
    assert not set(NUMERIC_FEATURES) & set(CATEGORICAL_FEATURES)
    assert set(NUMERIC_FEATURES) | set(CATEGORICAL_FEATURES) == set(FEATURES)
    assert feature_contract_sha256() == feature_contract_sha256(PUBLIC_FEATURES)


def test_feature_contract_hash_is_stable():
    assert FEATURE_CONTRACT_SHA256 == "2dbf79e7bc30c5d5744e113575120c14972fb17905a7784dfe44441185763fc5"
    assert feature_contract_sha256() == FEATURE_CONTRACT_SHA256
    assert feature_contract_sha256(tuple(reversed(FEATURES))) != FEATURE_CONTRACT_SHA256


def test_feature_contract_hash_matches_release_contract():
    release = ROOT / "contracts/release.example.json"
    if not release.exists():
        pytest.skip("release contract is not merged into this branch yet")
    assert json.loads(release.read_text())["feature_contract_sha256"] == FEATURE_CONTRACT_SHA256


def test_no_forbidden_input_is_a_feature():
    assert not FORBIDDEN_INPUTS & set(FEATURES)
    assert not FORBIDDEN_INPUTS & set(LABEL_COLUMNS)


def test_forbidden_inputs_cover_raw_leakage_columns():
    raw_columns = {
        "order_id",
        "customer_id",
        "approved_at",
        "promised_date",
        "delivered_at",
        "available_at",
        "late",
        "label_available_at",
        "released_at",
        "batch_id",
        "status",
        "review_score",
    }
    assert raw_columns <= FORBIDDEN_INPUTS


def test_build_features_signature_is_stable():
    signature = inspect.signature(build_features)
    assert list(signature.parameters) == ["connection", "order_ids", "as_of", "labeled"]
    assert signature.parameters["order_ids"].default is inspect.Parameter.empty
    assert signature.parameters["as_of"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["labeled"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["labeled"].default is False


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
