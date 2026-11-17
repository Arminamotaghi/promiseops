import hmac
import logging
import os
import time

from contextlib import asynccontextmanager
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from promiseops.contracts import PredictionRequest
from promiseops.features import build_features
from promiseops.release import BundleError, _positive_probability, load_bundle


logger = logging.getLogger(__name__)


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

RETRAINING_REQUIRED = Gauge(
    "promiseops_retraining_required",
    "Whether the latest monitoring report requires model retraining",
)
RETRAINING_REQUIRED.set(0)



def create_database_engine():
    """Create the SQLAlchemy engine using the configured psycopg driver."""
    url = os.getenv("PROMISEOPS_DATABASE_URL")
    if not url:
        raise ValueError("PROMISEOPS_DATABASE_URL is required")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return create_engine(url, pool_pre_ping=True)



def calculate_order_risk(connection, model, order_id: str):
    """Build order features and calculate late-delivery probability."""
    from datetime import UTC, datetime

    features = build_features(
        connection,
        [order_id],
        as_of=datetime.now(UTC),
    )

    if features.empty:
        return None

    return _positive_probability(model, features)

def get_existing_prediction(connection, request_id: str):
    """Read an existing prediction for idempotent API requests."""
    statement = text(
        """
        SELECT order_id, request_id, risk_probability, model_version
        FROM predictions
        WHERE request_id = :request_id
        """
    )
    return connection.execute(
        statement,
        {"request_id": request_id},
    ).mappings().first()

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
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            with app.state.database_engine_lock:
                engine = app.state.database_engine
                app.state.database_engine = None

            if engine is not None:
                engine.dispose()

    app = FastAPI(title="PromiseOps", lifespan=lifespan)

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
    app.state.database_engine = None
    app.state.database_engine_lock = Lock()

    def get_database_engine():
        with app.state.database_engine_lock:
            if app.state.database_engine is None:
                app.state.database_engine = create_database_engine()
            return app.state.database_engine
    app.state.database_engine = None
    MODEL_READY.set(1 if model is not None else 0)
    MODEL_INFO.clear()
    if model is not None and model_version is not None:
        MODEL_INFO.labels(model_version=model_version).set(1)

    app.state.database_ready = (
        database_ready if database_ready is not None else check_database_ready
    )


    def refresh_retraining_required_metric():
        """Refresh the gauge from the latest monitoring report."""
        try:
            engine = get_database_engine()
            with engine.connect() as connection:
                value = connection.execute(
                    text(
                        """
                        SELECT retraining_required
                        FROM monitoring_reports
                        ORDER BY as_of DESC, report_id DESC
                        LIMIT 1
                        """
                    )
                ).scalar_one_or_none()
        except (SQLAlchemyError, OSError, ValueError):
            # Preserve the last known value on database failure.
            return

        RETRAINING_REQUIRED.set(1 if value is True else 0)


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
        refresh_retraining_required_metric()
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

        request_id = payload.request_id or uuid4().hex

        try:
            engine = get_database_engine()

            with engine.begin() as connection:
                existing = get_existing_prediction(connection, request_id)

                if existing is not None:
                    if existing["order_id"] != payload.order_id:
                        return error_response(
                            409,
                            "request_id_conflict",
                            "Request ID belongs to another order",
                        )
                    return {
                        "order_id": existing["order_id"],
                        "request_id": existing["request_id"],
                        "risk_probability": existing["risk_probability"],
                        "model_version": existing["model_version"],
                    }

                probability = calculate_order_risk(
                    connection,
                    app.state.model,
                    payload.order_id,
                )

                if probability is None:
                    return error_response(
                        404,
                        "order_not_found",
                        "Order was not found",
                    )

                model_version = app.state.model_version
                if not model_version:
                    return error_response(
                        503,
                        "model_unavailable",
                        "Model version is unavailable",
                    )

                inserted = connection.execute(
                    text(
                        """
                        INSERT INTO predictions (
                            request_id,
                            order_id,
                            risk_probability,
                            model_version,
                            source
                        )
                        VALUES (
                            :request_id,
                            :order_id,
                            :risk_probability,
                            :model_version,
                            'api'
                        )
                        ON CONFLICT (request_id) DO NOTHING
                        RETURNING order_id, request_id,
                                  risk_probability, model_version
                        """
                    ),
                    {
                        "request_id": request_id,
                        "order_id": payload.order_id,
                        "risk_probability": probability,
                        "model_version": model_version,
                    },
                ).mappings().first()

                if inserted is None:
                    inserted = get_existing_prediction(connection, request_id)

                if inserted is None:
                    return error_response(
                        503,
                        "internal_error",
                        "Prediction could not be saved",
                    )

                if inserted["order_id"] != payload.order_id:
                    return error_response(
                        409,
                        "request_id_conflict",
                        "Request ID belongs to another order",
                    )

                return {
                    "order_id": inserted["order_id"],
                    "request_id": inserted["request_id"],
                    "risk_probability": inserted["risk_probability"],
                    "model_version": inserted["model_version"],
                }

        except (SQLAlchemyError, OSError, ValueError, BundleError) as exc:
            logger.error(
                "Predict failed: exception_type=%s",
                type(exc).__name__,
            )
            return error_response(
                500,
                "internal_error",
                "Prediction could not be completed",
            )

    return app
