from fastapi import FastAPI


def create_app():
    app = FastAPI(title="PromiseOps")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        # TODO: Check model and database availability.
        return {"status": "ok"}

    return app

