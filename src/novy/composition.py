from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from novy.application.builder_sessions import BuilderSessionService
from novy.infrastructure.builder_profiles import (
    BUILDER_PROFILES_COLLECTION,
    MongoBuilderProfileRepository,
)
from novy.infrastructure.builder_sessions import (
    BUILDER_SESSIONS_COLLECTION,
    MongoBuilderSessionRepository,
)
from novy.infrastructure.session_security import SessionSecurity
from novy.infrastructure.settings import Settings, decode_session_key


def create_builder_session_service(
    database: AsyncDatabase[dict[str, Any]], settings: Settings
) -> BuilderSessionService:
    if settings.session_encryption_key is None or settings.session_lookup_key is None:
        raise RuntimeError("Builder session security is not configured")
    return BuilderSessionService(
        sessions=MongoBuilderSessionRepository(database[BUILDER_SESSIONS_COLLECTION]),
        profiles=MongoBuilderProfileRepository(database[BUILDER_PROFILES_COLLECTION]),
        protector=SessionSecurity(
            encryption_key=decode_session_key(settings.session_encryption_key),
            lookup_key=decode_session_key(settings.session_lookup_key),
            environment=settings.environment,
        ),
    )
