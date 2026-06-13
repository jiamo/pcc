"""Make the CPU-only vthread oracles importable standalone.

The oracles live under ``pcc/vthread/`` but must import without pulling in
``pcc/__init__.py`` (the concurrency contract forbids touching it, and the
oracles have no dependency on the compiler package). We load the two modules by
file path and register them under short names so tests can ``import`` them
plainly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _repo_root() -> Path:
    # Walk up to the directory containing AGENTS.md (robust to pytest
    # import-mode level shifts; see MEMORY reference_test_repo_root_walk_up).
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "AGENTS.md").is_file():
            return parent
    raise RuntimeError("could not locate repo root (AGENTS.md not found)")


def _load(mod_name: str, rel_path: str):
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = _repo_root() / rel_path
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


# Register under stable short names used by the test modules.
_load("vthread_timer_oracle", "pcc/vthread/timer_oracle.py")
_load("vthread_io_waitset_oracle", "pcc/vthread/io_waitset_oracle.py")
