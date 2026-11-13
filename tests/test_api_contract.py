from promiseops.contracts import PredictionRequest


def test_request_id_accepts_valid_characters():
    request = PredictionRequest(order_id="order-123", request_id="req-123")
    assert request.request_id == "req-123"


def test_request_id_accepts_colon():
    request = PredictionRequest(order_id="order-123", request_id="req:123")
    assert request.request_id == "req:123"
