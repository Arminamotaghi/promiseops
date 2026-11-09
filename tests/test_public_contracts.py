import json
from pathlib import Path

import pytest

from promiseops.contracts import Batch, PredictionRequest

ROOT = Path(__file__).resolve().parents[1]


def test_supplied_fixtures_follow_contract():
    for path in (ROOT / "fixtures").glob("synthetic-*.json"):
        Batch.model_validate(json.loads(path.read_text()))


def test_prediction_ids_cannot_be_paths():
    with pytest.raises(ValueError):
        PredictionRequest(order_id="../../secret")


def test_group_adds_behavior_tests():
    # This test documents the starting point; passing fixture validation does not
    # demonstrate an implemented pipeline. Replace this skip with behavioral tests.
    pytest.skip("Add tests for your own ingestion/model/API/monitoring implementation")


def test_request_id_accepts_valid_characters():
    request = PredictionRequest(order_id="order-123", request_id="req-123")
    assert request.request_id == "req-123"


def test_request_id_rejects_colon():
    with pytest.raises(ValueError):
        PredictionRequest(order_id="order-123", request_id="req:123")

def test_prediction_response_rejects_empty_model_version():
    from promiseops.contracts import PredictionResponse

    with pytest.raises(ValueError):
        PredictionResponse(
            order_id="order-123",
            request_id="req-123",
            risk_probability=0.5,
            model_version="",
        )