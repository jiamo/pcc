"""Module naming helpers for Layer-1 codegen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir


_VOID = ir.VoidType()


class ModuleNameLoweringMixin:
    def _module_symbol_suffix(self, module_name: Optional[str] = None) -> str:
        name = module_name or self.module.name or "mod"
        return name.replace(".", "_").replace("-", "_")

    def _emit_module_teardown_call(self, module_name: Optional[str] = None) -> None:
        fini_name = self._module_teardown_name(module_name)
        existing = self.module.globals.get(fini_name)
        if existing is None:
            fini_fn = ir.Function(
                self.module,
                ir.FunctionType(_VOID, []),
                name=fini_name,
            )
            fini_fn.linkage = "external"
        else:
            fini_fn = existing
        self.builder.call(fini_fn, [])

    def _module_teardown_name(self, module_name: Optional[str] = None) -> str:
        return f"_pcc_py_module_fini_{self._module_symbol_suffix(module_name)}"

