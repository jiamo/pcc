"""Wheel build hook — produce libpy_runtime_pcc_py.a + native pcc1 binary.

The hook fires at wheel-build time (`python -m build`,
`pip install .`, or `pip install python-cc` going through sdist):

1. Run pcc itself (CPython-hosted) to compile pcc/py_runtime — both
   the C runtime sources and the pcc-Python runtime ports — into
   ``libpy_runtime_pcc_py.a``.
2. Run pcc again to self-compile ``pcc/__main__.py`` into a native
   ``pcc1`` executable, mirroring ``scripts/bootstrap.sh`` stage1.
3. Bundle both artifacts into the wheel: the archive at
   ``pcc/py_runtime/libpy_runtime_pcc_py.a`` (consumed by the lazy
   first-run path), the binary at ``.data/scripts/pcc1`` as the explicit
   native bootstrap helper.

Prefer the ``self`` backend so the build doesn't shell out to clang.
Fall back to ``--backend llvm`` (uses clang) if self fails.

Honours environment overrides:
- PCC_BUILD_BACKEND={self|llvm}   override backend (default: self)
- PCC_BUILD_TARGET=<make target>  override make target
- PCC_BUILD_SKIP=1                skip both runtime + binary build
                                  (dev iteration only)

Archive failure is tolerated (lazy first-run in
``pcc.py_frontend.pipeline`` rebuilds it). Binary failure raises —
publishing sdist-only means the user's ``pip install`` must produce a
working native ``pcc1`` helper. The Python ``pcc`` launcher exists, but
published sdists should still prove the native bootstrap helper can be built.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Hatchling hook: build the pcc-Python runtime archive + native pcc1."""

    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        if os.environ.get("PCC_BUILD_SKIP") == "1":
            self.app.display_info("PCC_BUILD_SKIP=1, skipping runtime + binary build")
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

        # ---- 1. runtime archive (soft-fail: lazy path can rebuild) ----
        ok = self._run_make(runtime_dir, target, backend)
        if not ok and backend != "llvm":
            self.app.display_warning(
                f"pcc runtime build with --backend={backend} failed; "
                "retrying with --backend=llvm (clang will be invoked)"
            )
            ok = self._run_make(runtime_dir, target, "llvm")
        if ok and archive.is_file():
            rel = f"pcc/py_runtime/{target}"
            build_data.setdefault("force_include", {})[str(archive)] = rel
            self.app.display_info(f"bundled runtime archive: {rel}")
        else:
            self.app.display_warning(
                "pcc runtime archive build failed; wheel will not bundle "
                f"{target}. First run of `pcc` will rebuild it via the "
                "lazy path in pcc.py_frontend.pipeline."
            )

        # ---- 2. native pcc1 binary (hard-fail for published sdists) ----
        out_binary = runtime_dir / "_native" / "pcc1"
        bin_backend = backend
        ok = self._run_pcc_self_compile(root, out_binary, bin_backend)
        if not ok and bin_backend != "llvm":
            self.app.display_warning(
                f"pcc self-compile with --backend={bin_backend} failed; "
                "retrying with --backend=llvm (clang will be invoked)"
            )
            ok = self._run_pcc_self_compile(root, out_binary, "llvm")
        if not ok:
            raise RuntimeError(
                "pcc self-compile failed under both self and llvm backends. "
                "The published wheel requires a native `pcc1` helper. Inspect the warnings "
                "above, or set PCC_BUILD_SKIP=1 for a dev iteration only."
            )

        build_data.setdefault("shared_scripts", {})[str(out_binary)] = "pcc1"
        build_data["pure_python"] = False
        build_data["infer_tag"] = True
        self.app.display_info("bundled native pcc1 binary at .data/scripts/pcc1")

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

    def _run_pcc_self_compile(
        self, root: Path, out_binary: Path, backend: str,
    ) -> bool:
        """Compile pcc/__main__.py into a native pcc binary.

        Mirrors ``scripts/bootstrap.sh`` stage1: CPython-hosted pcc
        compiles its own entry point with ``--backend self
        --python-libpython=off``. Returns True iff pcc exits 0 and the
        output file exists and is executable.
        """
        main_py = root / "pcc" / "__main__.py"
        if not main_py.is_file():
            self.app.display_warning(
                f"{main_py} not present in source tree; cannot self-compile"
            )
            return False

        out_binary.parent.mkdir(parents=True, exist_ok=True)
        if out_binary.exists():
            out_binary.unlink()

        env = dict(os.environ)
        env.setdefault("PCC_RUNTIME_CC", "pcc")
        env.setdefault("PCC_RUNTIME_HIGH", "py")

        cmd = [
            sys.executable, "-m", "pcc",
            "--backend", backend,
            "--python-libpython", "off",
            "--ir-scaffold=on",
            str(main_py),
            "-o", str(out_binary),
        ]
        try:
            subprocess.run(
                cmd,
                check=True,
                env=env,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=600,
            )
        except FileNotFoundError:
            self.app.display_warning(
                "python interpreter not on PATH; cannot self-compile"
            )
            return False
        except subprocess.TimeoutExpired:
            self.app.display_warning(
                "pcc self-compile timed out after 10 minutes"
            )
            return False
        except subprocess.CalledProcessError as exc:
            tail = (exc.stderr or exc.stdout or "")[-2000:]
            self.app.display_warning(
                f"pcc self-compile (backend={backend}) failed:\n{tail}"
            )
            return False

        if not out_binary.is_file() or not os.access(out_binary, os.X_OK):
            self.app.display_warning(
                f"pcc self-compile claimed success but {out_binary} is "
                "missing or not executable"
            )
            return False
        return True
