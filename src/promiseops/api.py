import hmac
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from promiseops.contracts import PredictionRequest
from promiseops.release import BundleError, load_bundle

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



MODEL_INFO = Gauge(
    "promiseops_model_info",
    "Information about the loaded prediction model",
    ["model_version"],
)


def check_database_ready():
    url = os.getenv("PROMISEOPS_DATABASE_URL")
    if not url:
        return False

    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]

    try:
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        finally:
            engine.dispose()
    except (SQLAlchemyError, OSError, ImportError):
        return False


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def create_app(*, model=None, database_ready=None):
    app = FastAPI(title="PromiseOps")

    model_version = None
    if model is None:
        release_dir = os.getenv("PROMISEOPS_RELEASE_DIR", "release")
        try:
            bundle = load_bundle(release_dir)
            model = bundle.model
            model_version = bundle.metadata["model_version"]
        except (BundleError, OSError, ValueError):
            model = None

    app.state.model = model
    app.state.model_version = model_version
    MODEL_READY.set(1 if model is not None else 0)
    MODEL_INFO.clear()
    if model is not None and model_version is not None:
        MODEL_INFO.labels(model_version=model_version).set(1)

    app.state.database_ready = (
        database_ready if database_ready is not None else check_database_ready
    )

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
        REQUEST_DURATION.labels(route_name).observe(time.perf_counter() - started)
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

        if model_ready and db_ready:
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
