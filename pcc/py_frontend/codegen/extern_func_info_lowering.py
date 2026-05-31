"""Extern metadata helper lowering for Layer-1 Python codegen."""
from __future__ import annotations

from typing import Optional

from ..export_meta import decode_type
from ..py_ast import Arg, DynType, FuncDef, SourceSpan
from .errors import L1CodegenError


_DYN = DynType(name="dyn")


class ExternFuncInfoLoweringMixin:
    def _extern_info_to_funcdef(self, name: str, info: dict) -> Optional[FuncDef]:
        call_sig = info.get("call_sig")
        if call_sig is None:
            return None
        span = SourceSpan(
            file="<extern>",
            line=0,
            col=0,
            end_line=0,
            end_col=0,
        )
        args = []
        for arg in call_sig:
            args.append(
                Arg(
                    name=arg["name"],
                    annotation=decode_type(arg.get("annotation")),
                    default=arg.get("default"),
                    kind=arg.get("kind", "pos"),
                    has_default=arg.get(
                        "has_default",
                        arg.get("default") is not None,
                    ),
                )
            )
        return FuncDef(
            span=span,
            name=name,
            args=tuple(args),
            return_ty=decode_type(info.get("return_ty")) or DynType(name="dyn"),
            body=(),
            decorators=(),
        )

    def _find_user_funcdef(self, name: str) -> FuncDef:
        for stmt in self._ast_body:
            if isinstance(stmt, FuncDef) and stmt.name == name:
                return stmt
        # Cross-module: name was imported from a native sibling via
        # ``from .other import name`` during multi-file compile.
        cm = self._cross_module_func_defs
        if name in cm and cm[name] is not None:
            return cm[name]
        block_defs = getattr(self, "_module_block_func_defs", {})
        if name in block_defs:
            return block_defs[name]
        raise L1CodegenError(f"no FuncDef for user function {name!r}")
