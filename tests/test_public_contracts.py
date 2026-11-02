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
