"""Builder profile persistence and active-status lookup.

Callers must supply an already verified builder-realm identity and explicitly
authorize onboarding. Status lookup is not authentication or authorization;
it never provisions, upserts, or reactivates a profile.
"""

from typing import Protocol

from novy.domain.builder import BuilderAccessDenied, BuilderIdentity, BuilderProfile


class BuilderProfileStoreError(Exception):
    def __init__(self) -> None:
        super().__init__("Builder profile storage is unavailable")


class BuilderProfileRepository(Protocol):
    async def create(
        self, identity: BuilderIdentity, *, display_name: str, email: str
    ) -> BuilderProfile: ...

    async def find_by_identity(self, identity: BuilderIdentity) -> BuilderProfile | None: ...


async def require_active_builder_profile(
    repository: BuilderProfileRepository, identity: BuilderIdentity
) -> BuilderProfile:
    """Return an active profile without authenticating or authorizing the caller."""
    profile = await repository.find_by_identity(identity)
    if profile is None or profile.identity != identity or profile.is_active is not True:
        raise BuilderAccessDenied()
    return profile
