#!/usr/bin/env python3
"""scripts/pcc_multi.py — multi-file Python compile entry point.

Wraps :func:`pcc.py_frontend.pipeline.compile_python_multi` with a
small argparse-based CLI so the three-stage bootstrap
(``scripts/bootstrap.sh``) and other callers can build a single
native executable from several ``.py`` sources without going
through the click-based ``pcc`` entry point.

Usage::

    python3 scripts/pcc_multi.py \\
        --entry pkg.main              \\
        --out pcc1                    \\
        pkg/main.py=pkg.main          \\
        pkg/util.py=pkg.util          \\
        pkg/lib.py=pkg.lib

Each positional argument is either a bare ``.py`` path (module
name inferred from the filename stem) or ``path=module.name`` to
set an explicit dotted module name — required when the stem
collides (``__init__.py``, ``__main__.py``) or when the file
uses relative imports.

The CLI tolerates ``--emit-llvm`` (write combined LLVM IR
instead of an executable) and ``--verbose`` for pipeline timing.
"""
from __future__ import annotations

import argparse
import sys


def _parse_src_arg(spec: str):
    """Return ``(path, module_name_or_None)`` for one positional."""
    if "=" in spec:
        path, _, mod = spec.partition("=")
        return path, mod
    return spec, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="pcc_multi",
        description="Multi-file Python compile for the pcc bootstrap.",
    )
    ap.add_argument(
        "--entry", required=True,
        help="Dotted module name that provides the program entry "
             "(its top-level body becomes @main).",
    )
    ap.add_argument(
        "--out", required=True,
        help="Output path: native executable, or .ll when "
             "--emit-llvm is given.",
    )
    ap.add_argument(
        "--emit-llvm", action="store_true",
        help="Write combined LLVM IR instead of linking.",
    )
    ap.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print each pipeline step + timings to stderr.",
    )
    ap.add_argument(
        "sources", nargs="+",
        help="One or more '<path>' or '<path>=<module.name>' entries.",
    )
    args = ap.parse_args(argv)

    src_paths = []
    module_names = []
    for spec in args.sources:
        path, mod = _parse_src_arg(spec)
        src_paths.append(path)
        if mod is None:
            # Filename stem fallback — matches compile_python default.
            import os
            base = os.path.basename(path)
            if base.endswith(".py"):
                base = base[:-3]
            module_names.append(base)
        else:
            module_names.append(mod)

    from pcc.py_frontend.pipeline import (
        compile_python_multi, PyPipelineError,
    )
    try:
        compile_python_multi(
            src_paths,
            args.out,
            verbose=args.verbose,
            emit_llvm_only=args.emit_llvm,
            entry_module=args.entry,
            module_names=module_names,
        )
    except PyPipelineError as e:
        print(f"pcc_multi: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
