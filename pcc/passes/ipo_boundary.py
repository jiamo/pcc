"""Explicit source-level IPO boundary passes for LLVM leaf aliases."""

from __future__ import annotations

from ..ast import c_ast
from .ast_utils import collect_ids
from .base import ASTPass
from .context import PassContext


def _is_extern_decl(node) -> bool:
    return isinstance(node, c_ast.Decl) and "extern" in tuple(
        getattr(node, "storage", ()) or ()
    )


class DeadArgElimAnalysisPass(ASTPass):
    """Record unused function parameters without rewriting ABI signatures."""

    name = "deadargelim-analysis"

    def run(self, ast, ctx: PassContext):
        if not isinstance(ast, c_ast.FileAST):
            return None

        for ext in ast.ext or []:
            if not isinstance(ext, c_ast.FuncDef):
                continue
            decl = getattr(ext, "decl", None)
            func_type = getattr(decl, "type", None)
            args = getattr(func_type, "args", None)
            if decl is None or args is None:
                continue

            used_names = collect_ids(ext.body)
            func_name = decl.name or "<anon>"
            for param in args.params or []:
                if not isinstance(param, c_ast.Decl) or not param.name:
                    continue
                if param.name in used_names:
                    continue
                ctx.record(self.name, "dead_param", f"{func_name}::{param.name}")
                ctx.bump("deadargelim.dead_params")

        return None


class ElimAvailExternPass(ASTPass):
    """Remove unused file-scope extern declarations from the translation unit."""

    name = "elim-avail-extern-src"

    def run(self, ast, ctx: PassContext):
        if not isinstance(ast, c_ast.FileAST) or not ast.ext:
            return None

        referenced: set[str] = set()
        for ext in ast.ext:
            if isinstance(ext, c_ast.FuncDef):
                referenced.update(collect_ids(ext.body))
                continue
            if isinstance(ext, c_ast.Decl) and ext.init is not None:
                referenced.update(collect_ids(ext.init))

        removed = 0
        new_ext = []
        for ext in ast.ext:
            if _is_extern_decl(ext) and ext.init is None and ext.name:
                if ext.name not in referenced:
                    removed += 1
                    ctx.record(self.name, "remove_extern_decl", ext.name)
                    continue
            new_ext.append(ext)

        if not removed:
            return None

        ctx.bump("elim_avail_extern.removed", removed)
        ast.ext = new_ext
        return ast
