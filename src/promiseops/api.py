from fastapi import FastAPI


def create_app():
    """Implement /predict, /healthz, /readyz, /metrics and the public error/auth contract."""
    app = FastAPI(title="PromiseOps — implement your team's service")
    # Configure a trusted pinned model bundle at startup, one shared feature path,
    # an authenticated prediction route, idempotent request IDs and bounded metrics.
    return app
