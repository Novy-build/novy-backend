"""Transport schema for the React export request.

The envelope is strict; deep project/layout validation is performed by the
domain validator so that error locations are precise and stable.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

MAX_EXPORT_BODY_BYTES = 1024 * 1024


class ExportSecurityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    secretsIncluded: Literal[False]
    rawMediaIncluded: Literal[False]
    biometricTemplatesIncluded: Literal[False]


class GeneratedAppManifestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    manifestVersion: Literal[2]
    generatedAt: str
    project: dict[str, Any]
    layout: dict[str, Any]
    appData: dict[str, Any]
    runtime: dict[str, Any]
    security: ExportSecurityModel
