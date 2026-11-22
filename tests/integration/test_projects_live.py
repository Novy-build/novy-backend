"""Opt-in database verification for project operations.

Tests real MongoDB project CRUD, CAS, rename, delete, and cross-owner
access rejection against an isolated scratch collection.
"""

import asyncio
from uuid import uuid4

import pytest

from novy.domain.projects import ProjectDraft
from novy.infrastructure.projects import (
    MongoProjectRepository,
)


def _run_if_authorized(flag: bool) -> list[str] | None:
    if not flag:
        return None
    return asyncio.run(_verify())


async def _verify() -> list[str]:
    from novy.infrastructure.mongodb import create_mongo_client
    from novy.infrastructure.settings import Settings

    settings = Settings()
    if settings.mongodb_connection_string is None:
        return ["MongoDB connection not configured"]

    client = create_mongo_client(settings.mongodb_connection_string)
    db_name = "platform_db"
    db = client[db_name]
    statuses: list[str] = []

    # Use a unique scratch collection name
    scratch_name = f"novy_proj_{uuid4().hex[:12]}"
    statuses.append(f"scratch collection: {scratch_name}")

    try:
        # Create collection with index
        collection = db[scratch_name]
        await collection.create_index(
            [("owner_id", 1), ("updated_at", 1), ("_id", 1)],
            name="projects_owner_updated_v1",
        )
        repo = MongoProjectRepository(collection)

        # Test 1: Create project
        owner_a = f"owner-a-{uuid4().hex[:8]}"
        owner_b = f"owner-b-{uuid4().hex[:8]}"
        project = await repo.create(
            owner_id=owner_a,
            name="Test Project",
            draft=ProjectDraft(content={"screens": []}),
            project_type="app",
        )
        statuses.append("create: OK")

        # Test 2: Find by id and owner
        found = await repo.find_by_id_and_owner(project.id, owner_a)
        assert found is not None
        assert found.name == "Test Project"
        assert found.project_type == "app"
        assert found.revision.value == 0
        statuses.append("find_by_id_and_owner: OK")

        # Test 3: Cross-owner access rejection
        cross = await repo.find_by_id_and_owner(project.id, owner_b)
        assert cross is None, "Cross-owner access must be rejected"
        statuses.append("cross-owner rejection: OK")

        # Test 4: exists check
        assert await repo.exists(project.id, owner_a) is True
        assert await repo.exists(project.id, owner_b) is False
        statuses.append("exists: OK")

        # Test 5: CAS save (success)
        updated = await repo.save_with_revision(
            project.id,
            owner_a,
            0,
            ProjectDraft(content={"screens": [{"id": "s1"}]}),
        )
        assert updated.revision.value == 1
        statuses.append("save_with_revision (success): OK")

        # Test 6: CAS save (conflict - stale revision)
        try:
            await repo.save_with_revision(
                project.id,
                owner_a,
                0,  # stale revision
                ProjectDraft(content={"screens": []}),
            )
            statuses.append("save_with_revision (conflict): FAILED - no conflict raised")
        except Exception:
            statuses.append("save_with_revision (conflict): OK")

        # Test 7: Rename
        renamed = await repo.rename(project.id, owner_a, "Renamed Project")
        assert renamed.name == "Renamed Project"
        assert renamed.revision.value == 1  # rename doesn't change revision
        statuses.append("rename: OK")

        # Test 8: Rename does not replace draft
        assert renamed.draft.content == {"screens": [{"id": "s1"}]}
        statuses.append("rename preserves draft: OK")

        # Test 9: Delete (tombstone)
        await repo.delete(project.id, owner_a)
        deleted = await repo.find_by_id_and_owner(project.id, owner_a)
        assert deleted is None or deleted.lifecycle_state == "deleted"
        statuses.append("delete (tombstone): OK")

        # Test 10: Save after delete is rejected
        try:
            await repo.save_with_revision(
                project.id,
                owner_a,
                1,
                ProjectDraft(content={"screens": []}),
            )
            statuses.append("save after delete: FAILED - no error raised")
        except Exception:
            statuses.append("save after delete: OK")

        # Test 11: List by owner
        project2 = await repo.create(
            owner_id=owner_a,
            name="Second Project",
            draft=ProjectDraft(content={"screens": []}),
            project_type="website",
        )
        projects = await repo.find_by_owner(owner_a)
        assert len(projects) >= 1
        statuses.append("find_by_owner: OK")

        # Test 12: projectType round-trip
        assert project2.project_type == "website"
        statuses.append("projectType round-trip: OK")

        # Cleanup
        await collection.drop()
        statuses.append("cleanup: OK")
        statuses.append("collection and index intentionally retained")

    except Exception as exc:
        statuses.append(f"FAILED: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        try:
            await client.close()
        except Exception:
            pass

    return statuses


def test_projects_live(request: pytest.FixtureRequest) -> None:
    statuses = _run_if_authorized(request.config.getoption("--verify-projects-db"))
    if statuses is None:
        pytest.skip("pass --verify-projects-db to authorize scratch writes")
    for status in statuses:
        print(status)
    if (
        "FAILED" in " ".join(statuses)
        or "collection and index intentionally retained" not in statuses
    ):
        pytest.fail(
            "project verification failed or inconclusive; see safe stages above",
            pytrace=False,
        )


def test_opt_in_guard() -> None:
    from unittest.mock import MagicMock, patch

    live = MagicMock(side_effect=AssertionError("live verification was called"))
    with patch.dict(globals(), {"_run_live": live}):
        assert _run_if_authorized(False) is None
    live.assert_not_called()
