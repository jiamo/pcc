"""Shared result / capability types for pcc.dist metadata oracles.

The single most important convention in this package: an operation that is
*not available in this local-only slice* must return an explicit
:class:`CapabilityResult` with ``status == "SKIPPED_WITH_REASON"`` (or raise a
:class:`DistUnavailableError`), never silently no-op or return ``None``.

Standalone-importable: ``import pcc.dist.results``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# Capability status vocabulary. Kept as plain strings (not an Enum) so the
# metadata round-trips through JSON/manifests without custom encoders.
STATUS_AVAILABLE = "AVAILABLE"
STATUS_SKIPPED = "SKIPPED_WITH_REASON"
STATUS_ERROR = "ERROR"

_VALID_STATUS = frozenset({STATUS_AVAILABLE, STATUS_SKIPPED, STATUS_ERROR})


class DistError(Exception):
    """Base class for all pcc.dist metadata-layer errors."""


class DistUnavailableError(DistError):
    """Raised when a caller demands a capability this local slice cannot provide.

    Carries the same ``reason`` a :class:`CapabilityResult` would report, so a
    caller can catch it and surface a mode-labeled message.
    """

    def __init__(self, capability: str, reason: str) -> None:
        super().__init__(f"{capability!r} unavailable: {reason}")
        self.capability = capability
        self.reason = reason


@dataclass(frozen=True)
class CapabilityResult:
    """The outcome of probing one capability (e.g. a transport mode).

    ``status`` is one of :data:`STATUS_AVAILABLE`, :data:`STATUS_SKIPPED`,
    :data:`STATUS_ERROR`. When skipped, ``reason`` must be a non-empty, human
    readable, mode-labeled sentence explaining *why it is unavailable in this
    slice* — never an empty string.
    """

    capability: str
    status: str
    reason: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUS:
            raise ValueError(
                f"invalid status {self.status!r}; expected one of {sorted(_VALID_STATUS)}"
            )
        if self.status == STATUS_SKIPPED and not self.reason.strip():
            raise ValueError(
                f"capability {self.capability!r} is SKIPPED_WITH_REASON but reason is empty"
            )

    @property
    def available(self) -> bool:
        return self.status == STATUS_AVAILABLE

    @property
    def skipped(self) -> bool:
        return self.status == STATUS_SKIPPED

    def raise_if_unavailable(self) -> "CapabilityResult":
        """Return self if available, else raise :class:`DistUnavailableError`."""
        if not self.available:
            raise DistUnavailableError(self.capability, self.reason or self.status)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "status": self.status,
            "reason": self.reason,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapabilityResult":
        return cls(
            capability=str(data["capability"]),
            status=str(data["status"]),
            reason=str(data.get("reason", "")),
            detail=dict(data.get("detail", {})),
        )


def available(capability: str, **detail: Any) -> CapabilityResult:
    return CapabilityResult(capability, STATUS_AVAILABLE, detail=detail)


def skipped(capability: str, reason: str, **detail: Any) -> CapabilityResult:
    return CapabilityResult(capability, STATUS_SKIPPED, reason=reason, detail=detail)


def errored(capability: str, reason: str, **detail: Any) -> CapabilityResult:
    return CapabilityResult(capability, STATUS_ERROR, reason=reason, detail=detail)
