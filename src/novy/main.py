import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from novy.composition import create_builder_session_service
from novy.infrastructure.mongodb import PLATFORM_DATABASE_NAME, create_mongo_client
from novy.infrastructure.settings import Settings
from novy.presentation.http.health import router as health_router

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    is_production = settings.environment == "production"

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        connection_string = settings.mongodb_connection_string
        client = create_mongo_client(connection_string) if connection_string is not None else None
        try:
            application.state.mongo_client = client
            application.state.platform_database = (
                client[PLATFORM_DATABASE_NAME] if client is not None else None
            )
            application.state.builder_session_service = (
                create_builder_session_service(application.state.platform_database, settings)
                if application.state.platform_database is not None
                and settings.session_encryption_key is not None
                and settings.session_lookup_key is not None
                else None
            )
            yield
        finally:
            application.state.builder_session_service = None
            application.state.mongo_client = None
            application.state.platform_database = None
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    logger.warning("MongoDB client cleanup failed")

    app = FastAPI(
        title="Novy Backend",
        version="0.1.0",
        debug=False,
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
        lifespan=lifespan,
    )
    app.include_router(health_router, prefix="/api/v1")
    return app


app = create_app()
