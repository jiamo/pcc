"""pcc.llvm_capi.compat — env-var-gated ``ir`` / ``binding`` shim.

P6C.2-wire β4.4 / β5 / default-flip: swap between ``pcc.llvm_capi``
(new default) and ``llvmlite`` (legacy opt-out) at import time based
on per-subsystem env flags.

## Flags (read once, at import time)

| Env var | Scope | Default |
|---|---|---|
| ``PCC_USE_LLVMLITE`` | all subsystems → llvmlite | off (= use native) |
| ``PCC_USE_LLVMLITE_PY`` | Python frontend codegen → llvmlite | off |
| ``PCC_USE_LLVMLITE_C`` | C frontend codegen → llvmlite | off |
| ``PCC_USE_LLVMLITE_PASSES`` | IR passes → llvmlite | off |

Default (no env var set) routes through ``pcc.llvm_capi`` — pcc's
text-first native IR builder + ctypes LLVM-C binding. Setting any of
the env vars forces the relevant subsystem back to ``llvmlite`` (for
debugging / regression isolation).

## Usage pattern in codegen modules

    from pcc.llvm_capi.compat import ir_py as ir     # Python codegen
    from pcc.llvm_capi.compat import ir_c as ir      # C codegen
    from pcc.llvm_capi.compat import ir_passes as ir # IR passes
    from pcc.llvm_capi.compat import binding_passes as binding

Each subsystem is gated independently so regressions in one don't
force a global rollback.

## Legacy flags (accepted for backwards-compat)

The earlier opt-in flags (``PCC_USE_LLVMCAPI`` / ``PCC_USE_LLVMCAPI_*``)
were retired at flip time. Setting them is a no-op — they don't revert
to llvmlite (native is now the default). If you need llvmlite, use
the reverse-opt-out forms above.
"""
from __future__ import annotations

import os


def _subsystem_uses_llvmlite(name: str) -> bool:
    """Return True if this subsystem should fall back to llvmlite."""
    if os.environ.get("PCC_USE_LLVMLITE") == "1":
        return True
    return os.environ.get(f"PCC_USE_LLVMLITE_{name}") == "1"


USE_LLVMLITE_PY = _subsystem_uses_llvmlite("PY")
USE_LLVMLITE_C = _subsystem_uses_llvmlite("C")
USE_LLVMLITE_PASSES = _subsystem_uses_llvmlite("PASSES")


def _pick(use_llvmlite: bool):
    """Returns ``(ir, binding)`` tuple for the subsystem."""
    if use_llvmlite:
        from llvmlite import ir as _ir
        from llvmlite import binding as _bind
        return _ir, _bind
    from . import ir as _ir
    from . import binding as _bind
    return _ir, _bind


# Per-subsystem exports. Each subsystem imports the pair it needs.
ir_py, binding_py = _pick(USE_LLVMLITE_PY)
ir_c, binding_c = _pick(USE_LLVMLITE_C)
ir_passes, binding_passes = _pick(USE_LLVMLITE_PASSES)


# Legacy export: plain ``ir`` / ``binding`` alias to Python codegen path
# (most common consumer). Kept for backwards-compat.
ir, binding = ir_py, binding_py


def set_struct_body(struct_ty, body, packed: bool = False) -> None:
    """Cross-backend setter for identified struct bodies.

    ``llvmlite.ir.IdentifiedStructType.set_body(*elements)`` uses
    a vararg positional signature without ``packed`` support. pcc's
    ``llvm_capi.ir.IdentifiedStructType.set_body(body, packed=...)``
    takes a single iterable + a packed keyword. This helper adapts
    to whichever backend is active so codegen has one call shape.

    Usage: replace ``st.set_body(*body)`` with
    ``set_struct_body(st, body)``.
    """
    cls_module = type(struct_ty).__module__
    if cls_module.startswith("pcc.llvm_capi"):
        struct_ty.set_body(body, packed=packed)
    else:
        # llvmlite: ``set_body(*elements)`` — no ``packed`` kwarg.
        struct_ty.set_body(*body)


__all__ = [
    "ir", "binding",
    "ir_py", "binding_py",
    "ir_c", "binding_c",
    "ir_passes", "binding_passes",
    "USE_LLVMLITE_PY", "USE_LLVMLITE_C", "USE_LLVMLITE_PASSES",
    "set_struct_body",
]
