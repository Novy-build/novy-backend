import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from fastapi import FastAPI

from novy.composition import (
    create_builder_auth_service,
    create_builder_session_service,
    create_csrf_service,
    create_project_service,
)
from novy.infrastructure.mongodb import PLATFORM_DATABASE_NAME, create_mongo_client
from novy.infrastructure.settings import Settings
from novy.presentation.http.auth import router as auth_router
from novy.presentation.http.errors import install_error_handlers
from novy.presentation.http.health import router as health_router
from novy.presentation.http.projects import router as projects_router

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    is_production = settings.environment == "production"

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        connection_string = settings.mongodb_connection_string
        client = create_mongo_client(connection_string) if connection_string is not None else None
        http_client: httpx2.AsyncClient | None = None
        try:
            application.state.mongo_client = client
            application.state.platform_database = (
                client[PLATFORM_DATABASE_NAME] if client is not None else None
            )
            sessions = (
                create_builder_session_service(application.state.platform_database, settings)
                if application.state.platform_database is not None
                and settings.session_encryption_key is not None
                and settings.session_lookup_key is not None
                else None
            )
            application.state.builder_session_service = sessions
            application.state.csrf_service = create_csrf_service(settings)
            application.state.project_service = (
                create_project_service(application.state.platform_database)
                if application.state.platform_database is not None
                else None
            )
            if (
                sessions is not None
                and settings.supabase_url is not None
                and settings.supabase_publishable_key is not None
            ):
                http_client = httpx2.AsyncClient(timeout=5.0)
                application.state.builder_auth_service = create_builder_auth_service(
                    settings=settings,
                    database=application.state.platform_database,
                    sessions=sessions,
                    http_client=http_client,
                )
            else:
                application.state.builder_auth_service = None
            yield
        finally:
            application.state.builder_auth_service = None
            application.state.builder_session_service = None
            application.state.csrf_service = None
            application.state.project_service = None
            application.state.mongo_client = None
            application.state.platform_database = None
            if http_client is not None:
                try:
                    await http_client.aclose()
                except Exception:
                    logger.warning("Supabase HTTP client cleanup failed")
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
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(projects_router, prefix="/api/v1")
    return app


app = create_app()
