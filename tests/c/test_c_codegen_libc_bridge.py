from __future__ import annotations

from pcc.llvm_capi.compat import ir_c as ir
from pcc.c_codegen_libc_bridge import lookup_codegen_signature, registry_to_codegen_map


def test_printf_is_vararg():
    _ret, args, vararg = lookup_codegen_signature(ir, "printf")
    assert len(args) == 1
    assert vararg is True


def test_errno_is_platform_split():
    assert "__errno_location" in registry_to_codegen_map(ir, "linux")
    assert "__error" in registry_to_codegen_map(ir, "darwin")
