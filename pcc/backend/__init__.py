from __future__ import annotations

"""Backend selection primitives for optional back-end execution paths.

The project currently defaults to the existing LLVM/llvmlite path.  This module
only introduces the contract used by task A1/A2: explicit backend choice and
cache-stable backend identity.
"""

from dataclasses import dataclass

import os


_ENV_BACKEND = "PCC_BACKEND"
_DEFAULT_BACKEND = "llvm"


class BackendUnavailable(ValueError):
    """Raised when a requested backend is known but not yet implemented."""


@dataclass(frozen=True)
class BackendConfig:
    kind: str
    semver: str
    supported: bool
    capabilities: tuple[str, ...]
    requested: str | None = None

    def cache_signature(self) -> str:
        return f"{self.kind}:{self.semver}:" + \
               ("support" if self.supported else "unsupported")

    def capabilities_csv(self) -> str:
        return ",".join(self.capabilities) if self.capabilities else ""


_BACKEND_TABLE = {
    "llvm": {
        "semver": "llvmlite-default",
        "supported": True,
        "capabilities": (
            "llvm-ir",
            "llvm-binding",
            "mcjit",
            "emit-object",
        ),
    },
    "llvm_capi": {
        "semver": "llvm-capi-wip",
        # Placeholder backend in phase A/B: available path is tracked as a
        # placeholder selection in the cache/config surface, but not mandatory for
        # default-path execution.
        "supported": True,
        "capabilities": (
            "llvm-ir",
            "llvm-c",
            "mcjit",
            "emit-object",
        ),
    },
    "self": {
        # Partial asm-first bootstrap path only; execution/object emission still
        # intentionally unsupported.
        "semver": "self-aarch64-asm-v0",
        "supported": False,
        "capabilities": (
            "emit-asm",
            "emit-object",
            "run-native-via-system-cc",
            "aarch64-darwin-mvp",
        ),
    },
}


def _normalize_backend_name(value: str | None) -> str:
    if not value:
        return _DEFAULT_BACKEND
    candidate = value.strip().lower()
    if not candidate:
        return _DEFAULT_BACKEND
    if candidate == "llvmlite":
        return "llvm"
    if candidate == "llvm-capi":
        return "llvm_capi"
    return candidate


def resolve_backend(
    requested: str | None = None,
    *,
    allow_unimplemented: bool = False,
) -> BackendConfig:
    """Resolve and return a concrete backend configuration.

    Args:
      requested: user-supplied backend name (`llvm`, `llvm_capi`, `self`).
      allow_unimplemented: when True, return config objects even for known-but-
        unimplemented backends such as `self`.
    """
    env_raw = os.environ.get(_ENV_BACKEND)
    kind = _normalize_backend_name(requested if requested is not None else env_raw)

    if kind not in _BACKEND_TABLE:
        known = ", ".join(sorted(_BACKEND_TABLE))
        raise ValueError(f"unknown backend {kind!r}; expected one of: {known}")

    info = _BACKEND_TABLE[kind]
    config = BackendConfig(
        kind=kind,
        semver=info["semver"],
        supported=bool(info["supported"]),
        capabilities=tuple(info["capabilities"]),
        requested=None if requested is None else _normalize_backend_name(requested),
    )

    if not allow_unimplemented and not config.supported:
        raise BackendUnavailable(
            f"backend '{kind}' is selected but not implemented in this build"
        )
    return config


def resolve_backend_or_raise(requested: str | None = None) -> BackendConfig:
    """Resolve backend and raise an explicit error for unsupported kinds."""
    return resolve_backend(requested, allow_unimplemented=False)


def backend_signature(config: BackendConfig | str | None) -> str:
    """Return a cache-safe backend string identity."""
    if isinstance(config, str):
        kind = _normalize_backend_name(config)
        if kind in _BACKEND_TABLE:
            info = _BACKEND_TABLE[kind]
            return BackendConfig(
                kind=kind,
                semver=info["semver"],
                supported=bool(info["supported"]),
                capabilities=tuple(info["capabilities"]),
            ).cache_signature()
        return f"unknown:{kind}"
    if config is None:
        return backend_signature(_DEFAULT_BACKEND)
    return config.cache_signature()


def backend_env_name() -> str:
    return _ENV_BACKEND


def all_backend_names() -> tuple[str, ...]:
    return tuple(sorted(_BACKEND_TABLE))


def backend_capabilities(config: BackendConfig | str | None) -> tuple[str, ...]:
    if isinstance(config, str):
        kind = _normalize_backend_name(config)
        if kind in _BACKEND_TABLE:
            return tuple(_BACKEND_TABLE[kind]["capabilities"])
        return ()
    if config is None:
        return _BACKEND_TABLE[_DEFAULT_BACKEND]["capabilities"]
    return config.capabilities
