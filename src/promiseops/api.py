import hmac
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)
from sqlalchemy.exc import SQLAlchemyError
from promiseops.contracts import PredictionRequest

REQUESTS = Counter(
    "promiseops_http_requests_total",
    "Total HTTP requests",
    ["route", "method", "status"],
)
REQUEST_DURATION = Histogram(
    "promiseops_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["route"],
)
MODEL_READY = Gauge(
    "promiseops_model_ready",
    "Whether the prediction model is loaded",
)

READINESS = Gauge(
    "promiseops_readiness",
    "Whether the service is ready",
)

MODEL = Info(
    "promiseops_model",
    "Loaded model information",
)

READINESS.set(0)
MODEL.info({"model_version": "unknown"})


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def create_app(*, model=None, database_ready=None):
    app = FastAPI(title="PromiseOps")
    app.state.model = model
    MODEL_READY.set(1 if model is not None else 0)
    app.state.database_ready = database_ready or (lambda: False)

    @app.middleware("http")
    async def check_api_key(request: Request, call_next):
        started = time.perf_counter()
        if request.url.path in {"/predict", "/metrics"}:
            expected = os.getenv("PROMISEOPS_API_KEY", "")
            supplied = request.headers.get("X-API-Key", "")
            if not expected or not hmac.compare_digest(supplied, expected):
                response = error_response(
                    401, "unauthorized", "Invalid or missing API key"
                )
                REQUESTS.labels(
                    request.url.path, request.method, "401"
                ).inc()
                route = request.scope.get("route")
                route_name = getattr(route, "path", "unmatched")
                REQUEST_DURATION.labels(route_name).observe(
                    time.perf_counter() - started
                )
                return response

        response = await call_next(request)
        route = request.scope.get("route")
        route_name = getattr(route, "path", "unmatched")
        REQUESTS.labels(
            route_name, request.method, str(response.status_code)
        ).inc()
        REQUEST_DURATION.labels(route_name).observe(
            time.perf_counter() - started
        )
        return response

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        model_ready = app.state.model is not None
        try:
            db_ready = bool(app.state.database_ready())
        except (SQLAlchemyError, OSError):
            db_ready = False

        is_ready = model_ready and db_ready
        READINESS.set(1 if is_ready else 0)

        if is_ready:
            return {"status": "ready"}
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready"},
        )

    @app.get("/metrics")
    def metrics():
        return Response(
            generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    @app.post("/predict")
    def predict(payload: PredictionRequest):
        if app.state.model is None:
            return error_response(
                503,
                "model_unavailable",
                "Prediction model is not available",
            )

        return error_response(
            503,
            "model_unavailable",
            "Prediction model integration is not configured",
        )

    return app
