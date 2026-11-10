import hmac
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from promiseops.contracts import PredictionRequest

REQUESTS = Counter(
    "promiseops_http_requests_total",
    "Total HTTP requests",
    ["route", "status"],
)


def create_app():
    app = FastAPI(title="PromiseOps")

    @app.middleware("http")
    async def check_api_key(request: Request, call_next):
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
        return response

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
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