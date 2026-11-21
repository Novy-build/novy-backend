import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from novy.application.builder_auth import (
    AuthUserView,
    BuilderAuthError,
    BuilderAuthService,
)
from novy.application.builder_sessions import ResolvedBuilderSession, RevocationResult
from novy.domain.builder import (
    BuilderAccessDenied,
    BuilderIdentity,
    BuilderProfile,
    BuilderProfileAlreadyExists,
)
from novy.domain.builder_sessions import ProviderTokens
from novy.infrastructure.supabase_auth import SupabaseAuthResult, SupabaseUser
from novy.infrastructure.supabase_jwt import VerifiedSupabaseIdentity

NOW = datetime(2026, 1, 1, tzinfo=UTC)
IDENTITY = BuilderIdentity("https://synthetic.example.test/auth/v1", "subject-1")
VERIFIED = VerifiedSupabaseIdentity(
    issuer=IDENTITY.issuer,
    subject=IDENTITY.subject,
    email="builder@example.test",
    display_name="Builder",
    session_id="session-1",
    role="authenticated",
)
TOKENS = ProviderTokens("synthetic-access", "synthetic-refresh")
PROFILE = BuilderProfile(
    id="profile-1",
    identity=IDENTITY,
    display_name="Builder",
    email="builder@example.test",
    is_active=True,
    created_at=NOW,
    updated_at=NOW,
)


class Profiles:
    def __init__(self, profile: BuilderProfile | None = None) -> None:
        self.value = profile
        self.created: list[tuple[BuilderIdentity, str, str]] = []

    async def find_by_identity(self, identity: BuilderIdentity) -> BuilderProfile | None:
        return self.value if identity == IDENTITY else None

    async def create(
        self, identity: BuilderIdentity, *, display_name: str, email: str
    ) -> BuilderProfile:
        self.created.append((identity, display_name, email))
        if self.value is not None:
            raise BuilderProfileAlreadyExists()
        self.value = replace(PROFILE, display_name=display_name, email=email)
        return self.value


class Sessions:
    def __init__(self, profile: BuilderProfile = PROFILE) -> None:
        self.profile = profile
        self.issued: list[tuple[BuilderIdentity, ProviderTokens]] = []
        self.replacements: list[ProviderTokens] = []
        self.revoked = False

    async def issue(self, identity: BuilderIdentity, tokens: ProviderTokens) -> Any:
        self.issued.append((identity, tokens))
        return type("Issued", (), {"credential": "credential-1", "record_id": "record-1"})()

    async def resolve(self, credential: str) -> ResolvedBuilderSession:
        return ResolvedBuilderSession(record_id="record-1", profile=self.profile)

    async def provider_tokens(self, credential: str) -> ProviderTokens:
        return TOKENS

    async def replace_provider_tokens(
        self, credential: str, tokens: ProviderTokens
    ) -> ResolvedBuilderSession:
        self.replacements.append(tokens)
        return await self.resolve(credential)

    async def revoke(self, credential: str) -> RevocationResult:
        self.revoked = True
        return RevocationResult.REVOKED


class Provider:
    def __init__(self, result: SupabaseAuthResult) -> None:
        self.result = result
        self.calls: list[str] = []

    async def sign_up(self, email: str, password: str, display_name: str) -> SupabaseAuthResult:
        self.calls.append("signup")
        return self.result

    async def sign_in(self, email: str, password: str) -> SupabaseAuthResult:
        self.calls.append("login")
        return self.result

    async def refresh(self, refresh_token: str) -> SupabaseAuthResult:
        self.calls.append("refresh")
        return self.result


class Validator:
    def __init__(self, identity: VerifiedSupabaseIdentity = VERIFIED) -> None:
        self.identity = identity

    async def validate(self, token: str) -> VerifiedSupabaseIdentity:
        return self.identity


def service(
    *,
    profiles: Profiles | None = None,
    sessions: Sessions | None = None,
    provider: Provider | None = None,
    validator: Validator | None = None,
) -> BuilderAuthService:
    pending = SupabaseAuthResult(
        user=SupabaseUser("supabase-1", "builder@example.test", "Builder"), tokens=None
    )
    return BuilderAuthService(
        profiles=profiles or Profiles(),
        sessions=sessions or Sessions(),  # type: ignore[arg-type]
        provider=provider or Provider(pending),  # type: ignore[arg-type]
        jwt_validator=validator or Validator(),  # type: ignore[arg-type]
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_register_pending_does_not_create_profile_or_session() -> None:
    profiles, sessions = Profiles(), Sessions()
    auth = service(profiles=profiles, sessions=sessions)
    result = run(
        auth.register(email="builder@example.test", password="password1", display_name="Builder")
    )
    assert result.requires_confirmation is True
    assert result.credential is None
    assert profiles.created == []
    assert sessions.issued == []


def test_register_with_session_creates_profile_and_issues_session() -> None:
    profiles, sessions = Profiles(), Sessions()
    provider = Provider(
        SupabaseAuthResult(
            user=SupabaseUser("supabase-1", "builder@example.test", "Builder"), tokens=TOKENS
        )
    )
    auth = service(profiles=profiles, sessions=sessions, provider=provider)
    result = run(
        auth.register(email="builder@example.test", password="password1", display_name="Builder")
    )
    assert result.requires_confirmation is False
    assert result.credential == "credential-1"
    assert profiles.created and profiles.created[0][1:] == ("Builder", "builder@example.test")
    assert sessions.issued[0][0] == IDENTITY
    assert sessions.issued[0][1] == TOKENS


def test_login_uses_existing_profile_and_denies_disabled_profile() -> None:
    active = Profiles(replace(PROFILE))
    auth = service(
        profiles=active,
        provider=Provider(
            SupabaseAuthResult(
                user=SupabaseUser("supabase-1", "builder@example.test", "Builder"), tokens=TOKENS
            )
        ),
    )
    result = run(auth.login(email="builder@example.test", password="password1"))
    assert result.user == AuthUserView("profile-1", "Builder", "builder@example.test")
    assert active.created == []

    disabled = Profiles(replace(PROFILE, is_active=False))
    denied = service(
        profiles=disabled,
        provider=Provider(
            SupabaseAuthResult(
                user=SupabaseUser("supabase-1", "builder@example.test", "Builder"), tokens=TOKENS
            )
        ),
    )
    with pytest.raises(BuilderAccessDenied):
        run(denied.login(email="builder@example.test", password="password1"))


def test_session_and_refresh_and_logout() -> None:
    sessions = Sessions()
    auth = service(
        sessions=sessions,
        provider=Provider(
            SupabaseAuthResult(
                user=SupabaseUser("supabase-1", "builder@example.test", "Builder"),
                tokens=ProviderTokens("new-access", "new-refresh"),
            )
        ),
    )
    assert run(auth.session("credential-1")) == AuthUserView(
        "profile-1", "Builder", "builder@example.test"
    )
    refreshed = run(auth.refresh("credential-1"))
    assert refreshed.credential == "credential-1"
    assert sessions.replacements == [ProviderTokens("new-access", "new-refresh")]
    assert run(auth.logout("credential-1")) is True
    assert sessions.revoked is True


def test_refresh_identity_mismatch_is_denied() -> None:
    other = replace(VERIFIED, subject="subject-2")
    auth = service(
        validator=Validator(other),
        provider=Provider(
            SupabaseAuthResult(
                user=SupabaseUser("supabase-1", "builder@example.test", "Builder"),
                tokens=ProviderTokens("new-access", "new-refresh"),
            )
        ),
    )
    with pytest.raises(BuilderAccessDenied):
        run(auth.refresh("credential-1"))


def test_login_without_tokens_is_email_not_confirmed() -> None:
    auth = service()
    with pytest.raises(BuilderAuthError) as error:
        run(auth.login(email="builder@example.test", password="password1"))
    assert error.value.code == "email_not_confirmed"
