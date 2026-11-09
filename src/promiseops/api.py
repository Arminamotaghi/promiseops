import hmac
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from promiseops.contracts import PredictionRequest


def create_app():
    app = FastAPI(title="PromiseOps")

    @app.middleware("http")
    async def check_api_key(request, call_next):
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

        return await call_next(request)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready"},
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
