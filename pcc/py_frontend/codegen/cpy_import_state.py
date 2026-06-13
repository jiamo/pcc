"""CPython import fallback state helpers for L1CodeGen."""

from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

_I8 = ir.IntType(8)
_CSTR = _I8.as_pointer()


class CpyImportStateMixin:
    def _cpy_module_global(self, local_name: str) -> ir.GlobalVariable:
        """Return (or create) the module-level ``i8*`` global that
        stores the imported CPython ``PyObject *``. Shared across
        functions so a user's ``main()`` can read a module bound by a
        top-level ``import`` statement."""
        gname = f".cpy.modref.{local_name}"
        existing = self.module.globals.get(gname)
        if isinstance(existing, ir.GlobalVariable):
            return existing
        g = ir.GlobalVariable(self.module, _CSTR, name=gname)
        g.linkage = "internal"
        g.initializer = ir.Constant(_CSTR, None)
        return g

    def _cpy_modules(self) -> dict:
        """Module-wide map of imported local name → global variable."""
        if not hasattr(self, "_cpy_module_env"):
            self._cpy_module_env = {}
        return self._cpy_module_env

    def _native_extension_module_global(self, local_name: str) -> ir.GlobalVariable:
        """Return (or create) the module-level PyObject* global for a
        pcc-native extension module loaded without libpython."""
        gname = f".pcc.ext.modref.{local_name}"
        existing = self.module.globals.get(gname)
        if isinstance(existing, ir.GlobalVariable):
            return existing
        g = ir.GlobalVariable(self.module, _CSTR, name=gname)
        g.linkage = "internal"
        g.initializer = ir.Constant(_CSTR, None)
        return g

    def _native_extension_modules(self) -> dict:
        if not hasattr(self, "_native_extension_module_env"):
            self._native_extension_module_env = {}
        return self._native_extension_module_env

    def _native_extension_star_modules(self) -> dict[str, ir.GlobalVariable]:
        if not hasattr(self, "_native_extension_star_module_env"):
            self._native_extension_star_module_env = {}
        return self._native_extension_star_module_env

    def _native_extension_star_module_global(
        self, module_name: str
    ) -> ir.GlobalVariable:
        star_modules = self._native_extension_star_modules()
        gv = star_modules.get(module_name)
        if gv is not None:
            return gv
        gv = self._native_extension_module_global(f"starimport.{module_name}")
        star_modules[module_name] = gv
        return gv

    def _load_from_native_extension_star_imports(self, name: str) -> Optional[ir.Value]:
        if not getattr(self, "_native_extension_star_module_env", {}):
            return None
        module_name = self.ast_module.name or "__main__"
        module_name_ptr = self._ptr_to_cstr(
            self._cstr_global(module_name, f".pcc.ext.star.module.{module_name}")
        )
        attr_ptr = self._ptr_to_cstr(
            self._cstr_global(name, f".pcc.ext.star.attr.{name}")
        )
        return self.builder.call(
            self.runtime["py_module_attr_get"],
            [module_name_ptr, attr_ptr],
            name=self._fresh(f"pcc.ext.star.{name}"),
        )

    def _cpy_star_modules(self) -> dict[str, ir.GlobalVariable]:
        """Globals storing modules imported via ``from x import *``."""
        if not hasattr(self, "_cpy_star_module_env"):
            self._cpy_star_module_env = {}
        return self._cpy_star_module_env

    def _cpy_star_module_global(self, module_name: str) -> ir.GlobalVariable:
        star_modules = self._cpy_star_modules()
        gv = star_modules.get(module_name)
        if gv is not None:
            return gv
        gv = self._cpy_module_global(f"starimport.{module_name}")
        star_modules[module_name] = gv
        return gv

    def _load_from_cpy_star_imports(self, name: str) -> Optional[ir.Value]:
        """Resolve an otherwise-unbound name from a prior star import."""
        star_modules = getattr(self, "_cpy_star_module_env", {})
        if not star_modules:
            return None
        attr_ptr = self._ptr_to_cstr(self._cstr_global(name, f".cpy.star.attr.{name}"))
        values = tuple(star_modules.values())
        idx = len(values) - 1
        gv = values[idx]
        mod_val = self.builder.load(gv, name=self._fresh(f"cpy.star.mod.{idx}"))
        val = self.builder.call(
            self.runtime["py_cpy_getattr"],
            [mod_val, attr_ptr],
            name=self._fresh(f"cpy.star.{name}"),
        )
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(val)
        return val

    def _ensure_cpy_init(self) -> None:
        """Emit a one-time ``py_cpy_ensure_init()`` in the current
        function. Idempotent both in IR (py_cpy_ensure_init's atomic
        guard) and in emission (we only emit it once per function
        compilation)."""
        if not hasattr(self, "_cpy_init_emitted_fns"):
            self._cpy_init_emitted_fns = set()
        fn_id = id(self.current_function)
        if fn_id in self._cpy_init_emitted_fns:
            return
        self.builder.call(self.runtime["py_cpy_ensure_init"], [])
        self._cpy_init_emitted_fns.add(fn_id)

    # -- With-statement (context manager) -----------------------------
