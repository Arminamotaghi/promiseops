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

from promiseops.contracts import PredictionRequest

REQUESTS = Counter(
    "promiseops_http_requests_total",
    "Total HTTP requests",
    ["route", "status"],
)
LATENCY = Histogram(
    "promiseops_http_request_duration_seconds",
    "HTTP request duration",
    ["route"],
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


def create_app():
    app = FastAPI(title="PromiseOps")

    @app.middleware("http")
    async def check_api_key(request: Request, call_next):
        started = time.perf_counter()
        if request.url.path in {"/predict", "/metrics"}:
            expected = os.getenv("PROMISEOPS_API_KEY", "")
            supplied = request.headers.get("X-API-Key", "")

            if not expected or not hmac.compare_digest(supplied, expected):
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "code": "unauthorized",
                            "message": "Invalid or missing API key",
                        }
                    },
                )

        response = await call_next(request)
        route = request.scope.get("route")
        route_name = getattr(route, "path", "unmatched")
        REQUESTS.labels(route_name, str(response.status_code)).inc()
        LATENCY.labels(route_name).observe(time.perf_counter() - started)
        return response

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        READINESS.set(0)
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
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "model_unavailable",
                    "message": "Prediction model is not available",
                }
            },
        )

    return app