"""Wheel build hook — produce libpy_runtime_pcc_py.a without cc/clang.

The hook fires at wheel-build time (`python -m build`,
`pip install .`, or `pip install python-cc` going through sdist):

1. Run pcc itself (CPython-hosted) to compile pcc/py_runtime — both
   the C runtime sources and the pcc-Python runtime ports.
2. Prefer the self backend so the build doesn't shell out to clang
   either. The user only needs Python + the system linker
   (ld64 / ld).
3. Bundle the resulting `libpy_runtime_pcc_py.a` into the wheel so
   users on the same platform don't need to rebuild on first run.

Honours environment overrides:
- PCC_BUILD_BACKEND={self|llvm}   override backend (default: self)
- PCC_BUILD_TARGET=<make target>  override make target
- PCC_BUILD_SKIP=1                skip the runtime build entirely
                                  (use when the wheel is being shipped
                                  alongside a pre-built archive, or
                                  during quick dev iterations)

If the build fails (e.g. self backend not yet ready on this host),
the hook falls back to ``--backend llvm`` and warns. If both fail,
the hook leaves the archive out of the wheel and prints a clear
"first run will rebuild" notice — pcc.py_frontend.pipeline already
has the lazy-build fallback at first invocation.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Hatchling hook: build the pcc-Python runtime archive into the wheel."""

    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        if os.environ.get("PCC_BUILD_SKIP") == "1":
            self.app.display_info("PCC_BUILD_SKIP=1, skipping runtime build")
            return

        root = Path(self.root)
        runtime_dir = root / "pcc" / "py_runtime"
        target = os.environ.get(
            "PCC_BUILD_TARGET", "libpy_runtime_pcc_py.a"
        )
        archive = runtime_dir / target

        if not runtime_dir.is_dir():
            self.app.display_warning(
                f"runtime dir {runtime_dir} not present in source tree; "
                "skipping pre-build (lazy first-run will handle it)"
            )
            return

        backend = os.environ.get("PCC_BUILD_BACKEND", "self")
        ok = self._run_make(runtime_dir, target, backend)
        if not ok and backend != "llvm":
            self.app.display_warning(
                f"pcc runtime build with --backend={backend} failed; "
                "retrying with --backend=llvm (clang will be invoked)"
            )
            ok = self._run_make(runtime_dir, target, "llvm")
        if not ok:
            self.app.display_warning(
                "pcc runtime build failed under both self and llvm "
                "backends; the wheel will not bundle "
                f"{target}. First run of `pcc` will rebuild it on the "
                "user's machine via the existing lazy path."
            )
            return

        if not archive.is_file():
            self.app.display_warning(
                f"runtime build claimed success but {archive} is "
                "missing; wheel will not bundle the archive"
            )
            return

        rel = f"pcc/py_runtime/{target}"
        build_data.setdefault("force_include", {})[str(archive)] = rel
        self.app.display_info(f"bundled runtime archive: {rel}")

    def _run_make(
        self, runtime_dir: Path, target: str, backend: str,
    ) -> bool:
        """Invoke the runtime Makefile under a chosen backend.

        Returns True iff the make exits 0. We deliberately don't fail
        the wheel build on a make error — the lazy first-run path in
        pcc.py_frontend.pipeline handles a missing archive cleanly.
        """
        env = dict(os.environ)
        # Inject the backend flag directly into the PCC command so
        # the Makefile doesn't need to learn a new variable. The
        # existing `$(PCC) --cpp-arg=... --emit-obj ...` invocations
        # in pcc/py_runtime/Makefile pick this up unchanged.
        env["PCC"] = f"{sys.executable} -m pcc --backend {backend}"

        cmd = ["make", "-C", str(runtime_dir), target]
        try:
            subprocess.run(
                cmd,
                check=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
            return True
        except FileNotFoundError:
            self.app.display_warning(
                "make is not on PATH; cannot pre-build runtime"
            )
            return False
        except subprocess.TimeoutExpired:
            self.app.display_warning(
                "runtime build timed out after 10 minutes"
            )
            return False
        except subprocess.CalledProcessError as exc:
            tail = (exc.stderr or exc.stdout or "")[-2000:]
            self.app.display_warning(
                f"make {target} (backend={backend}) failed:\n{tail}"
            )
            return False
