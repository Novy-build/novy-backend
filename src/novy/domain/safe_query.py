"""Safe query validation for application data.

Rejects arbitrary MongoDB operators in user input. Validates query
parameters rather than passing them through as raw operators.
"""

from __future__ import annotations

from typing import Any

# MongoDB operators that must never appear in user-supplied query input.
_FORBIDDEN_OPERATORS = frozenset(
    {
        "$gt",
        "$gte",
        "$lt",
        "$lte",
        "$ne",
        "$eq",
        "$in",
        "$nin",
        "$and",
        "$or",
        "$not",
        "$nor",
        "$exists",
        "$type",
        "$expr",
        "$jsonSchema",
        "$mod",
        "$regex",
        "$text",
        "$where",
        "$all",
        "$elemMatch",
        "$size",
        "$bitsAllClear",
        "$bitsAllSet",
        "$bitsAnyClear",
        "$bitsAnySet",
        "$geoIntersects",
        "$geoWithin",
        "$near",
        "$nearSphere",
        "$center",
        "$centerSphere",
        "$box",
        "$polygon",
        "$uniqueDocs",
        "$addToSet",
        "$push",
        "$pull",
        "$pop",
        "$set",
        "$unset",
        "$inc",
        "$mul",
        "$rename",
        "$min",
        "$max",
        "$currentDate",
        "$bit",
    }
)


class UnsafeQueryError(Exception):
    """Raised when a query contains unsafe operators or keys."""

    def __init__(self, message: str = "Unsafe query") -> None:
        super().__init__(message)


def validate_safe_value(value: Any, *, depth: int = 0) -> None:
    """Recursively validate that a value contains no MongoDB operators.

    Raises ``UnsafeQueryError`` if any forbidden operator key is found.
    """
    if depth > 10:
        raise UnsafeQueryError("Query nesting too deep")
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise UnsafeQueryError("Non-string key in query")
            if key.startswith("$"):
                raise UnsafeQueryError(f"Forbidden operator: {key}")
            if key in _FORBIDDEN_OPERATORS:
                raise UnsafeQueryError(f"Forbidden operator: {key}")
            validate_safe_value(value[key], depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            validate_safe_value(item, depth=depth + 1)
    elif isinstance(value, tuple):
        for item in value:
            validate_safe_value(item, depth=depth + 1)


def validate_field_id(value: str) -> None:
    """Validate that a field ID is a safe, non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise UnsafeQueryError("Invalid field ID")
    if value.startswith("$"):
        raise UnsafeQueryError("Field ID must not start with $")
    if len(value) > 256:
        raise UnsafeQueryError("Field ID too long")


def validate_record_values(values: dict[str, Any]) -> None:
    """Validate that record values are keyed by field ID and contain no operators."""
    if not isinstance(values, dict):
        raise UnsafeQueryError("Values must be a dict")
    for key in values:
        validate_field_id(key)
    validate_safe_value(values)
