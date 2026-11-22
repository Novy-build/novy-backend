"""Builder authentication orchestration using verified Supabase identities.

This module does not verify credentials itself and does not expose provider
tokens. Supabase owns credentials; MongoDB owns Novy profiles and sessions.
"""

from dataclasses import dataclass, field

from novy.application.builder_profiles import (
    BuilderProfileRepository,
    require_active_builder_profile,
)
from novy.application.builder_sessions import BuilderSessionService, RevocationResult
from novy.domain.builder import (
    BuilderAccessDenied,
    BuilderIdentity,
    BuilderProfile,
    BuilderProfileAlreadyExists,
)
from novy.domain.builder_sessions import ProviderTokens
from novy.infrastructure.supabase_auth import SupabaseAuthClient, SupabaseUser
from novy.infrastructure.supabase_jwt import SupabaseJwtValidator, VerifiedSupabaseIdentity


class BuilderAuthError(Exception):
    """Safe application error for the HTTP boundary."""

    def __init__(self, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__("Builder authentication failed")


@dataclass(frozen=True, slots=True)
class AuthUserView:
    id: str
    display_name: str
    email: str


@dataclass(frozen=True, slots=True)
class AuthResult:
    user: AuthUserView
    credential: str = field(repr=False)
    record_id: str


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    user: AuthUserView
    requires_confirmation: bool
    credential: str | None = field(default=None, repr=False)
    record_id: str | None = None


class BuilderAuthService:
    def __init__(
        self,
        *,
        profiles: BuilderProfileRepository,
        sessions: BuilderSessionService,
        provider: SupabaseAuthClient,
        jwt_validator: SupabaseJwtValidator,
    ) -> None:
        self._profiles = profiles
        self._sessions = sessions
        self._provider = provider
        self._jwt = jwt_validator

    async def register(self, *, email: str, password: str, display_name: str) -> RegistrationResult:
        result = await self._provider.sign_up(email, password, display_name)
        if result.tokens is None:
            return RegistrationResult(
                user=self._pending_user(result.user), requires_confirmation=True
            )
        identity = await self._jwt.validate(result.tokens.access_token)
        issued = await self._issue(identity, result.tokens)
        return RegistrationResult(
            user=issued.user,
            requires_confirmation=False,
            credential=issued.credential,
            record_id=issued.record_id,
        )

    async def login(self, *, email: str, password: str) -> AuthResult:
        result = await self._provider.sign_in(email, password)
        if result.tokens is None:
            raise BuilderAuthError("email_not_confirmed", 400)
        identity = await self._jwt.validate(result.tokens.access_token)
        return await self._issue(identity, result.tokens)

    async def session(self, credential: str) -> AuthUserView:
        resolved = await self._sessions.resolve(credential)
        return self._view(resolved.profile)

    async def refresh(self, credential: str) -> AuthResult:
        current = await self._sessions.provider_tokens(credential)
        refreshed = await self._provider.refresh(current.refresh_token)
        if refreshed.tokens is None:
            raise BuilderAuthError("invalid_session", 401)
        identity = await self._jwt.validate(refreshed.tokens.access_token)
        resolved = await self._sessions.replace_provider_tokens(credential, refreshed.tokens)
        if (
            resolved.profile.identity.issuer != identity.issuer
            or resolved.profile.identity.subject != identity.subject
        ):
            raise BuilderAccessDenied()
        return AuthResult(
            user=self._view(resolved.profile),
            credential=credential,
            record_id=resolved.record_id,
        )

    async def logout(self, credential: str) -> bool:
        result = await self._sessions.revoke(credential)
        return result in (RevocationResult.REVOKED, RevocationResult.ALREADY_REVOKED)

    async def _issue(
        self, identity: VerifiedSupabaseIdentity, tokens: ProviderTokens
    ) -> AuthResult:
        builder_identity = BuilderIdentity(issuer=identity.issuer, subject=identity.subject)
        profile = await self._profile_for_identity(
            builder_identity, email=identity.email, display_name=identity.display_name
        )
        issued = await self._sessions.issue(
            builder_identity,
            ProviderTokens(tokens.access_token, tokens.refresh_token),
        )
        return AuthResult(
            user=self._view(profile), credential=issued.credential, record_id=issued.record_id
        )

    async def _profile_for_identity(
        self, identity: BuilderIdentity, *, email: str, display_name: str
    ) -> BuilderProfile:
        if not isinstance(email, str) or not email.strip():
            raise BuilderAuthError("invalid_identity", 401)
        name = (
            display_name.strip()
            if isinstance(display_name, str) and display_name.strip()
            else email
        )
        existing = await self._profiles.find_by_identity(identity)
        if existing is None:
            try:
                await self._profiles.create(identity, display_name=name, email=email)
            except BuilderProfileAlreadyExists:
                pass
        return await require_active_builder_profile(self._profiles, identity)

    @staticmethod
    def _view(profile: BuilderProfile) -> AuthUserView:
        return AuthUserView(id=profile.id, display_name=profile.display_name, email=profile.email)

    @staticmethod
    def _pending_user(user: SupabaseUser) -> AuthUserView:
        return AuthUserView(id=user.id, display_name=user.display_name, email=user.email)
