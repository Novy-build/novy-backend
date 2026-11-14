import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.codec_options import CodecOptions
from bson.errors import BSONError
from pymongo import ASCENDING, ReadPreference
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from novy.application.builder_profiles import BuilderProfileStoreError
from novy.domain.builder import (
    BuilderIdentity,
    BuilderProfile,
    BuilderProfileAlreadyExists,
    BuilderProfileDataError,
)

BUILDER_PROFILES_COLLECTION = "builder_profiles"
BUILDER_IDENTITY_INDEX = "builder_identity_unique_v1"


class _StrictDocument(dict[str, Any]):
    def __setitem__(self, key: str, value: Any) -> None:
        if key in self:
            raise ValueError("Duplicate BSON key")
        super().__setitem__(key, value)


def _profile_id(identity: BuilderIdentity) -> ObjectId:
    # Stable IDs prevent concurrent application creates from producing duplicate identities.
    encoded = json.dumps(
        [identity.issuer, identity.subject], ensure_ascii=True, separators=(",", ":")
    ).encode()
    return ObjectId(hashlib.sha256(encoded).digest()[:12])


class MongoBuilderProfileRepository:
    def __init__(self, collection: AsyncCollection[dict[str, Any]]) -> None:
        self._write_collection: AsyncCollection[dict[str, Any]] = collection.with_options(
            codec_options=CodecOptions(tz_aware=True, tzinfo=UTC),
            write_concern=WriteConcern(w="majority"),
            read_preference=ReadPreference.PRIMARY,
        )
        self._read_collection = self._write_collection.with_options(
            codec_options=CodecOptions(document_class=_StrictDocument, tz_aware=True, tzinfo=UTC),
            read_preference=ReadPreference.PRIMARY,
            read_concern=ReadConcern("linearizable"),
        )

    async def _require_identity_index(self) -> None:
        valid = False
        try:
            indexes = await self._write_collection.index_information()
            index = indexes.get(BUILDER_IDENTITY_INDEX)
            if isinstance(index, dict):
                collation = index.get("collation")
                valid = (
                    index.get("key") == [("issuer", ASCENDING), ("subject", ASCENDING)]
                    and index.get("unique") is True
                    and index.get("sparse", False) is False
                    and "partialFilterExpression" not in index
                    and index.get("hidden", False) is False
                    and (
                        "collation" not in index
                        or (isinstance(collation, dict) and collation.get("locale") == "simple")
                    )
                )
        except Exception:
            pass
        if not valid:
            raise BuilderProfileStoreError() from None

    async def create(
        self, identity: BuilderIdentity, *, display_name: str, email: str
    ) -> BuilderProfile:
        if not isinstance(identity, BuilderIdentity):
            raise ValueError("Invalid builder identity")
        now = datetime.now(UTC)
        now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        profile = BuilderProfile(
            id=str(_profile_id(identity)),
            identity=identity,
            display_name=display_name,
            email=email,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        document = {
            "_id": ObjectId(profile.id),
            "issuer": profile.identity.issuer,
            "subject": profile.identity.subject,
            "display_name": profile.display_name,
            "email": profile.email,
            "is_active": profile.is_active,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
        }
        await self._require_identity_index()
        try:
            await self._write_collection.insert_one(document)
        except DuplicateKeyError as exc:
            details = exc.details
            pattern = details.get("keyPattern") if isinstance(details, dict) else None
            if (
                isinstance(pattern, dict)
                and all(type(value) is int for value in pattern.values())
                and list(pattern.items()) == [("issuer", 1), ("subject", 1)]
            ):
                error: Exception = BuilderProfileAlreadyExists()
            elif (
                isinstance(pattern, dict)
                and list(pattern.items()) == [("_id", 1)]
                and type(pattern["_id"]) is int
            ):
                try:
                    existing = await self.find_by_identity(identity)
                except (BuilderProfileDataError, BuilderProfileStoreError):
                    existing = None
                error = (
                    BuilderProfileAlreadyExists()
                    if existing is not None
                    else BuilderProfileStoreError()
                )
            else:
                error = BuilderProfileStoreError()
        except (PyMongoError, BSONError, UnicodeError, OverflowError, ValueError, TypeError):
            error = BuilderProfileStoreError()
        else:
            return profile
        raise error from None

    async def find_by_identity(self, identity: BuilderIdentity) -> BuilderProfile | None:
        if not isinstance(identity, BuilderIdentity):
            raise ValueError("Invalid builder identity")
        await self._require_identity_index()
        document: dict[str, Any] | None = None
        error: Exception | None = None
        try:
            document = await self._read_collection.find_one(
                {
                    "_id": _profile_id(identity),
                    "issuer": identity.issuer,
                    "subject": identity.subject,
                },
                collation={"locale": "simple"},
                max_time_ms=3000,
            )
        except PyMongoError:
            error = BuilderProfileStoreError()
        except (BSONError, ValueError, OverflowError):
            error = BuilderProfileDataError()
        if error is not None:
            raise error from None
        if document is None:
            return None
        try:
            if not isinstance(document["_id"], ObjectId) or document["_id"] != _profile_id(
                identity
            ):
                raise ValueError
            stored_identity = BuilderIdentity(
                issuer=document["issuer"], subject=document["subject"]
            )
            if stored_identity != identity:
                raise ValueError
            return BuilderProfile(
                id=str(document["_id"]),
                identity=stored_identity,
                display_name=document["display_name"],
                email=document["email"],
                is_active=document["is_active"],
                created_at=document["created_at"],
                updated_at=document["updated_at"],
            )
        except (KeyError, TypeError, ValueError):
            pass
        raise BuilderProfileDataError() from None
