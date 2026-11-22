"""Default-deny per-operation policy for runtime access.

Policies specify resources and operations. ``public`` grants one specific
operation on one specific resource. ``owner_only`` admits only the actual
active project builder via the narrow runtime session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PolicyError(Exception):
    """Raised when a policy check fails."""

    def __init__(self, message: str = "Policy denied") -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """A single policy rule for a resource+operation."""

    resource: str
    operation: str
    effect: str  # "allow" or "deny"
    scope: str  # "public", "owner_only", "member", "anonymous"
    exposed_fields: list[str] | None = None
    constraints: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PolicySet:
    """A set of policy rules for a project+environment."""

    project_id: str
    environment: str
    rules: list[PolicyRule]

    def find_rule(self, resource: str, operation: str) -> PolicyRule | None:
        for rule in self.rules:
            if rule.resource == resource and rule.operation == operation:
                return rule
        return None

    def is_allowed(
        self,
        resource: str,
        operation: str,
        *,
        is_owner: bool = False,
        is_member: bool = False,
        is_anonymous: bool = False,
    ) -> bool:
        rule = self.find_rule(resource, operation)
        if rule is None:
            return False  # default deny
        if rule.effect != "allow":
            return False
        if rule.scope == "public":
            return True
        if rule.scope == "owner_only":
            return is_owner
        if rule.scope == "member":
            return is_member
        if rule.scope == "anonymous":
            return is_anonymous
        return False

    def exposed_fields_for(self, resource: str, operation: str) -> list[str] | None:
        rule = self.find_rule(resource, operation)
        if rule is None:
            return None
        return rule.exposed_fields
