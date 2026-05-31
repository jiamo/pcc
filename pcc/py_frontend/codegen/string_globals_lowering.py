"""String/global constant helpers for Layer-1 Python codegen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir


_I8 = ir.IntType(8)
_I32 = ir.IntType(32)


class StringGlobalsLoweringMixin:
    def _utf8_byte_values(self, payload: str) -> list[int]:
        """Return UTF-8 byte values for ``payload`` without a bytes object.

        The self-hosted compiler cannot depend on CPython ``bytes`` or
        ``str.encode`` here: these globals are emitted while compiling
        user programs. Keep the helper in ordinary Python so CPython
        and pcc-Python execute the same code path.
        """
        out: list[int] = []
        i = 0
        n = len(payload)
        while i < n:
            cp = ord(payload[i])
            if cp <= 127:
                out.append(cp)
            elif cp <= 2047:
                out.append(192 | (cp >> 6))
                out.append(128 | (cp & 63))
            elif cp <= 65535:
                out.append(224 | (cp >> 12))
                out.append(128 | ((cp >> 6) & 63))
                out.append(128 | (cp & 63))
            else:
                out.append(240 | (cp >> 18))
                out.append(128 | ((cp >> 12) & 63))
                out.append(128 | ((cp >> 6) & 63))
                out.append(128 | (cp & 63))
            i += 1
        return out

    def _cstr_literal(self, payload: str) -> tuple[ir.GlobalVariable, int]:
        """Intern a UTF-8 byte array as an internal global.

        Returns ``(gv, byte_len)`` where ``byte_len`` excludes the
        trailing NUL. Emitted globals are named ``.pystr.<N>`` per the
        L2 convention in the task brief.
        """
        data = self._utf8_byte_values(payload)
        existing = self._str_pool.get(payload)
        if existing is not None:
            # Array length minus the NUL terminator.
            arr_ty = existing.type.pointee
            return existing, arr_ty.count - 1
        self._str_counter += 1
        name = ".pystr." + str(self._str_counter)
        body = data + [0]
        arr_ty = ir.ArrayType(_I8, len(body))
        gv = ir.GlobalVariable(self.module, arr_ty, name=name)
        gv.linkage = "internal"
        gv.global_constant = True
        gv.initializer = ir.Constant(arr_ty, body)
        self._str_pool[payload] = gv
        return gv, len(data)

    def _attr_name_ptr(self, name: str) -> ir.Value:
        """Return an i8* pointing at a NUL-terminated attribute name.

        These globals are short-lived (attribute-access use only) and
        intentionally distinct from :meth:`_cstr_literal` so a later
        optimiser can fold them if it wishes.
        """
        existing = self._attr_pool.get(name)
        if existing is None:
            data = self._utf8_byte_values(name) + [0]
            arr_ty = ir.ArrayType(_I8, len(data))
            sym = ".pyattr." + str(name)
            # Multiple distinct attrs may share a name; disambiguate.
            if sym in self.module.globals:
                sym = ".pyattr." + str(name) + "." + str(len(self._attr_pool))
            gv = ir.GlobalVariable(self.module, arr_ty, name=sym)
            gv.linkage = "internal"
            gv.global_constant = True
            gv.initializer = ir.Constant(arr_ty, data)
            self._attr_pool[name] = gv
            existing = gv
        expr = (
            "getelementptr inbounds ("
            + str(existing.value_type)
            + ", "
            + str(existing.type)
            + " @"
            + str(existing.name)
            + ", i32 0, i32 0)"
        )
        return ir.Value(ir.PointerType(_I8), expr)

    def _cstr_global(self, payload: str, name: str) -> ir.GlobalVariable:
        data = self._utf8_byte_values(payload) + [0]
        arr_ty = ir.ArrayType(_I8, len(data))
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.GlobalVariable):
            return existing
        gv = ir.GlobalVariable(self.module, arr_ty, name=name)
        gv.linkage = "internal"
        gv.global_constant = True
        gv.initializer = ir.Constant(arr_ty, data)
        return gv

    def _pooled_cstr_global(
        self,
        payload: str,
        prefix: str = ".cstr",
    ) -> ir.GlobalVariable:
        existing = self._cstr_pool.get(payload)
        if existing is not None:
            return existing
        self._cstr_counter += 1
        name = str(prefix) + "." + str(self._cstr_counter)
        while name in self.module.globals:
            self._cstr_counter += 1
            name = str(prefix) + "." + str(self._cstr_counter)
        gv = self._cstr_global(payload, name)
        self._cstr_pool[payload] = gv
        return gv

    def _pooled_cstr_ptr(
        self,
        payload: str,
        prefix: str = ".cstr",
    ) -> ir.Value:
        return self._ptr_to_cstr(self._pooled_cstr_global(payload, prefix))

    def _ptr_to_cstr(self, gv: ir.GlobalVariable) -> ir.Value:
        zero = ir.Constant(_I32, 0)
        return self.builder.gep(gv, [zero, zero], inbounds=True)

    def _get_fmt_int(self) -> ir.GlobalVariable:
        if self._fmt_int is None:
            self._fmt_int = self._cstr_global("%ld\n", ".fmt_int")
        return self._fmt_int

    def _get_fmt_float(self) -> ir.GlobalVariable:
        if self._fmt_float is None:
            # Use %g for a Python-ish default; this is NOT bit-for-bit
            # Python's repr and will be upgraded in Phase 2 when the
            # runtime lib is wired in for repr.
            self._fmt_float = self._cstr_global("%g\n", ".fmt_float")
        return self._fmt_float

    def _get_fmt_bool_true(self) -> ir.GlobalVariable:
        if self._fmt_bool_true is None:
            self._fmt_bool_true = self._cstr_global("True\n", ".fmt_true")
        return self._fmt_bool_true

    def _get_fmt_bool_false(self) -> ir.GlobalVariable:
        if self._fmt_bool_false is None:
            self._fmt_bool_false = self._cstr_global("False\n", ".fmt_false")
        return self._fmt_bool_false
