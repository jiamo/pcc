"""Top-level ``pcc`` package exports.

Keep package import side effects minimal so subpackages such as
``pcc.py_frontend`` can be imported without pulling the full C
evaluator / llvmlite stack into the process.
"""
from __future__ import annotations

__all__ = [
    "module",
    "build",
    "BuildArtifact",
    "Module",
    "valueclass",
    "ValueBox",
    "ValuePayload",
]


def __getattr__(name: str):
    if name in __all__:
        from .api import BuildArtifact, Module, build, module
        from .value_model import ValueBox, ValuePayload, valueclass

        exports = {
            "module": module,
            "build": build,
            "BuildArtifact": BuildArtifact,
            "Module": Module,
            "valueclass": valueclass,
            "ValueBox": ValueBox,
            "ValuePayload": ValuePayload,
        }
        return exports[name]
    raise AttributeError(f"module 'pcc' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)


# Roadmap real-wire hooks are intentionally installed from the package root so
# both `python -m pcc` and `from pcc.py_frontend.pipeline import compile_python`
# see the same observability/pass/cache wiring. Disable with
# PCC_DISABLE_ROADMAP_DEEPWIRE=1 when bisecting bootstrap regressions.
try:
    from .roadmap_deepwire import install as _pcc_roadmap_deepwire_install
    _pcc_roadmap_deepwire_install()
except Exception:
    pass
