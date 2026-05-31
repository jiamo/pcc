"""Emit a native object file from LLVM IR text.

This helper exists for build-time paths that already have valid LLVM IR
but cannot safely hand that text to the host ``clang``. Some Linux
toolchains still parse typed-pointer IR by default, while pcc's Python
frontend emits opaque ``ptr`` IR. llvmlite owns a matching LLVM build, so
use its target machine directly.
"""

from __future__ import annotations

import argparse
import sys

from llvmlite import binding as llvm


def _module_triple(mod) -> str:
    triple = ""
    try:
        triple = str(mod.triple or "")
    except Exception:
        triple = ""
    if triple and triple != "unknown-unknown-unknown":
        return triple
    return llvm.get_default_triple()


def emit_object(ir_text: str, *, target_triple: str | None = None) -> bytes:
    llvm.initialize_all_targets()
    llvm.initialize_all_asmprinters()
    mod = llvm.parse_assembly(ir_text)
    mod.verify()
    triple = target_triple or _module_triple(mod)
    target = llvm.Target.from_triple(triple)
    tm = target.create_target_machine()
    return tm.emit_object(mod)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="emit a native object file from LLVM IR text",
    )
    parser.add_argument("input", help="input .ll file")
    parser.add_argument("output", help="output .o file")
    parser.add_argument(
        "--target",
        default=None,
        help="target triple; defaults to the module triple or host triple",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            ir_text = f.read()
        obj = emit_object(ir_text, target_triple=args.target)
        with open(args.output, "wb") as f:
            f.write(obj)
    except Exception as exc:
        sys.stderr.write("ir_to_obj: " + str(exc) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
