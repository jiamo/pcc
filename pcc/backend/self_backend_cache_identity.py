"""Content identity for the self-backend IR-to-object implementation."""

from __future__ import annotations

import hashlib
from pathlib import Path

_IDENTITY_SCHEMA = b"pcc.self-backend-emitter-source.v1\0"


def self_backend_emitter_source_identity(source_root: str | Path | None = None) -> str:
    """Hash only sources that can change self-backend emission.

    Per-object cache keys separately bind the normalized IR shard, target,
    object-cache schema, assembler command, and assembler version.  Hashing the
    whole compiler source tree here would invalidate every object after an
    unrelated frontend, package, runtime, or documentation change.
    """

    if source_root is None:
        backend_dir = Path(__file__).resolve().parent
    else:
        backend_dir = Path(source_root).resolve() / "pcc" / "backend"
    sources = sorted(
        (
            path
            for path in backend_dir.glob("self_backend*.py")
            if path.name != Path(__file__).name
        ),
        key=lambda path: path.name,
    )
    if not sources:
        raise FileNotFoundError(f"no self-backend sources under {backend_dir}")

    digest = hashlib.sha256()
    digest.update(_IDENTITY_SCHEMA)
    for path in sources:
        name = path.name.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(path.read_bytes())
    return digest.hexdigest()


__all__ = ["self_backend_emitter_source_identity"]
