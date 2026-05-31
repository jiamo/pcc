"""Class-local constants for ``L1CodeGen``.

The self-hosted stage compiler still expects these names to exist directly
on ``L1CodeGen``.  Keep the literal data out of ``layer1.py`` while assigning
the imported values back onto the public class.
"""
from __future__ import annotations


EXTERN_SCAFFOLD_MODULES = (
    "pcc.extern",
    "pcc.llvm_capi",
    "pcc.llvm_capi.compat",
)
IR_RUNTIME_COMPAT_MODULE = "pcc.llvm_capi.compat"
UNSAFE_SCAFFOLD_MODULES = ("pcc.unsafe",)
COMPILE_TIME_ONLY_MODULES = (
    "__future__",
    "typing",
    "abc",
    "click",
    "pcc.extern",
)
COMPILE_TIME_ONLY_IMPORT_FROMS = {
    "abc": ("ABC", "abstractmethod"),
    "dataclasses": ("dataclass", "field", "replace"),
}
TEST_FACADE_IMPORT_MODULES = (
    "pytest",
    "pcc.test_runner",
)
ANNOTATION_ONLY_IMPORT_MODULES = (
    "llvmlite.binding",
    "llvmlite.ir",
)


__all__ = [
    "ANNOTATION_ONLY_IMPORT_MODULES",
    "COMPILE_TIME_ONLY_IMPORT_FROMS",
    "COMPILE_TIME_ONLY_MODULES",
    "EXTERN_SCAFFOLD_MODULES",
    "IR_RUNTIME_COMPAT_MODULE",
    "TEST_FACADE_IMPORT_MODULES",
    "UNSAFE_SCAFFOLD_MODULES",
]
