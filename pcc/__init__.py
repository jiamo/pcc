"""Top-level ``pcc`` package exports.

Keep package import side effects minimal so subpackages such as
``pcc.py_frontend`` can be imported without pulling the full C
evaluator / llvmlite stack into the process.
"""
from __future__ import annotations

__all__ = ["module", "build", "BuildArtifact", "Module"]


def __getattr__(name: str):
    if name in __all__:
        from .api import BuildArtifact, Module, build, module

        exports = {
            "module": module,
            "build": build,
            "BuildArtifact": BuildArtifact,
            "Module": Module,
        }
        return exports[name]
    raise AttributeError(f"module 'pcc' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
