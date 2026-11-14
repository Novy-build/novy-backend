import asyncio
import inspect
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from novy.application.builder_profiles import (
    BuilderProfileRepository,
    require_active_builder_profile,
)
from novy.domain.builder import (
    BuilderAccessDenied,
    BuilderIdentity,
    BuilderProfile,
    BuilderProfileDataError,
)

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
IDENTITY = BuilderIdentity(issuer="https://issuer.example.test", subject="synthetic-subject")
SENSITIVE = "synthetic-sensitive-marker"


def profile(*, is_active: bool = True) -> BuilderProfile:
    return BuilderProfile(
        id="synthetic-profile",
        identity=IDENTITY,
        display_name="Synthetic Builder",
        email="builder@example.test",
        is_active=is_active,
        created_at=NOW,
        updated_at=NOW,
    )


def repository(result: BuilderProfile | None = None, error: Exception | None = None) -> MagicMock:
    fake = MagicMock(spec=BuilderProfileRepository)
    for name, _method in inspect.getmembers(BuilderProfileRepository, inspect.iscoroutinefunction):
        operation = cast(AsyncMock, getattr(fake, name))
        operation.return_value = result
        operation.side_effect = error
    return fake


def lookup(fake: MagicMock, identity: BuilderIdentity = IDENTITY) -> BuilderProfile:
    return asyncio.run(
        require_active_builder_profile(cast(BuilderProfileRepository, fake), identity)
    )


def assert_single_lookup(fake: MagicMock, identity: BuilderIdentity = IDENTITY) -> None:
    assert len(fake.mock_calls) == 1
    call = fake.mock_calls[0]
    assert (call.args == (identity,) and not call.kwargs) or (
        not call.args and call.kwargs == {"identity": identity}
    )
    operation = cast(AsyncMock, getattr(fake, call[0]))
    assert operation.await_count == 1


def test_identity_equality_includes_issuer_and_subject() -> None:
    assert IDENTITY == BuilderIdentity(issuer=IDENTITY.issuer, subject=IDENTITY.subject)
    assert IDENTITY != BuilderIdentity(
        issuer="https://other.example.test", subject=IDENTITY.subject
    )
    assert IDENTITY != BuilderIdentity(issuer=IDENTITY.issuer, subject="other-subject")
    assert len({IDENTITY, BuilderIdentity("https://other.example.test", IDENTITY.subject)}) == 2


@pytest.mark.parametrize("field", ["issuer", "subject"])
@pytest.mark.parametrize("value", ["", "   ", "\t\n", "\ud800", None, 1, {"$ne": SENSITIVE}])
def test_identity_rejects_invalid_values_without_echoing_them(field: str, value: object) -> None:
    values: dict[str, object] = {"issuer": IDENTITY.issuer, "subject": IDENTITY.subject}
    values[field] = value
    with pytest.raises((BuilderProfileDataError, ValueError, TypeError)) as exc_info:
        BuilderIdentity(issuer=cast(str, values["issuer"]), subject=cast(str, values["subject"]))
    assert SENSITIVE not in str(exc_info.value)
    assert SENSITIVE not in repr(exc_info.value)


@pytest.mark.parametrize("field", ["id", "display_name", "email"])
@pytest.mark.parametrize("value", ["", "   ", "\t\n", "\ud800", None, 1, {"$ne": SENSITIVE}])
def test_profile_rejects_invalid_strings_without_echoing_them(field: str, value: object) -> None:
    with pytest.raises((BuilderProfileDataError, ValueError, TypeError)) as exc_info:
        replace(profile(), **cast(Any, {field: value}))
    assert SENSITIVE not in str(exc_info.value)
    assert SENSITIVE not in repr(exc_info.value)


@pytest.mark.parametrize("value", [None, 1, "true", {"$ne": SENSITIVE}])
def test_profile_rejects_invalid_identity(value: object) -> None:
    with pytest.raises((BuilderProfileDataError, ValueError, TypeError)) as exc_info:
        replace(profile(), identity=cast(BuilderIdentity, value))
    assert SENSITIVE not in str(exc_info.value)
    assert SENSITIVE not in repr(exc_info.value)


@pytest.mark.parametrize("value", [None, 0, 1, "true", {"$ne": SENSITIVE}])
def test_profile_requires_actual_boolean(value: object) -> None:
    with pytest.raises((BuilderProfileDataError, ValueError, TypeError)) as exc_info:
        replace(profile(), is_active=cast(bool, value))
    assert SENSITIVE not in str(exc_info.value)
    assert SENSITIVE not in repr(exc_info.value)


def test_profile_requires_is_active_argument() -> None:
    with pytest.raises(TypeError):
        BuilderProfile(  # type: ignore[call-arg]
            id="synthetic-profile",
            identity=IDENTITY,
            display_name="Synthetic Builder",
            email="builder@example.test",
            created_at=NOW,
            updated_at=NOW,
        )


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
@pytest.mark.parametrize(
    "value", [datetime(2026, 1, 2), None, "not-a-datetime", {"$ne": SENSITIVE}]
)
def test_profile_requires_aware_timestamps(field: str, value: object) -> None:
    with pytest.raises((BuilderProfileDataError, ValueError, TypeError)) as exc_info:
        replace(profile(), **cast(Any, {field: value}))
    assert SENSITIVE not in str(exc_info.value)
    assert SENSITIVE not in repr(exc_info.value)


def test_identity_and_profile_are_frozen_and_slotted() -> None:
    builder = profile()
    assert not hasattr(IDENTITY, "__dict__")
    assert not hasattr(builder, "__dict__")
    with pytest.raises(FrozenInstanceError):
        IDENTITY.subject = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        builder.is_active = False  # type: ignore[misc]


def test_active_lookup_returns_original_profile_and_only_reads() -> None:
    builder = profile()
    fake = repository(builder)
    assert lookup(fake) is builder
    assert_single_lookup(fake)


def test_lookup_passes_exact_issuer_and_subject() -> None:
    other = BuilderIdentity(issuer="https://other.example.test", subject=IDENTITY.subject)
    builder = replace(profile(), identity=other)
    fake = repository(builder)
    assert lookup(fake, other) is builder
    assert_single_lookup(fake, other)


def test_missing_and_disabled_are_denied_generically_without_writes() -> None:
    messages = []
    for result in (None, profile(is_active=False)):
        fake = repository(result)
        with pytest.raises(BuilderAccessDenied) as exc_info:
            lookup(fake)
        messages.append(str(exc_info.value))
        assert IDENTITY.subject not in str(exc_info.value)
        assert IDENTITY.issuer not in str(exc_info.value)
        assert_single_lookup(fake)
    assert messages[0] == messages[1]


def test_lookup_denies_a_profile_for_a_different_identity() -> None:
    other = BuilderIdentity(issuer="https://other.example.test", subject=IDENTITY.subject)
    fake = repository(replace(profile(), identity=other))
    with pytest.raises(BuilderAccessDenied):
        lookup(fake)
    assert_single_lookup(fake)


@pytest.mark.parametrize("value", [None, 1, "true"])
def test_lookup_denies_corrupt_non_boolean_active_status(value: object) -> None:
    builder = profile()
    object.__setattr__(builder, "is_active", value)
    fake = repository(builder)
    with pytest.raises(BuilderAccessDenied):
        lookup(fake)
    assert_single_lookup(fake)


def test_lookup_propagates_repository_failure_without_writes() -> None:
    error = RuntimeError("synthetic storage failure")
    fake = repository(error=error)
    with pytest.raises(RuntimeError) as exc_info:
        lookup(fake)
    assert exc_info.value is error
    assert_single_lookup(fake)
