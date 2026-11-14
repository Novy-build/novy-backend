from fastapi import FastAPI

from novy.infrastructure.settings import Settings
from novy.presentation.http.health import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    is_production = settings.environment == "production"
    app = FastAPI(
        title="Novy Backend",
        version="0.1.0",
        debug=False,
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
    )
    app.include_router(health_router, prefix="/api/v1")
    return app


app = create_app()
