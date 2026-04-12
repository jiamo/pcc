"""Transform pass that reuses dominating SSA values proven redundant by GVN."""

from __future__ import annotations

from ..ast import c_ast
from .ast_utils import ASTTransformer
from .base import ASTPass
from .context import PassContext
from .ssa_gvn import SSAGVNPass


class _SSAGVNRewriter(ASTTransformer):
    _SCALAR_TYPES = {
        "_Bool",
        "bool",
        "char",
        "signed char",
        "unsigned char",
        "short",
        "short int",
        "signed short",
        "signed short int",
        "unsigned short",
        "unsigned short int",
        "int",
        "signed",
        "signed int",
        "unsigned",
        "unsigned int",
        "long",
        "long int",
        "signed long",
        "signed long int",
        "unsigned long",
        "unsigned long int",
        "long long",
        "long long int",
        "signed long long",
        "signed long long int",
        "unsigned long long",
        "unsigned long long int",
        "float",
        "double",
        "long double",
    }

    def __init__(self, ctx: PassContext):
        self.ctx = ctx
        self.changed = False
        self._current_func_name: str | None = None
        self._current_return_type: str = ""
        self._current_types: dict[str, str] = {}
        self._coord_decisions: dict[str, str] = {}

    def visit_FuncDef(self, node):
        func_name = getattr(getattr(node, "decl", None), "name", None)
        if not func_name:
            self._visit_children(node)
            return node

        old_name = self._current_func_name
        old_return_type = self._current_return_type
        old_types = self._current_types
        old_decisions = self._coord_decisions

        self._current_func_name = func_name
        self._current_return_type = self._decl_type_name(
            getattr(getattr(node, "decl", None), "type", None)
        )
        self._current_types = self._collect_function_types(node)
        self._coord_decisions = self._decisions_for_function(func_name)
        self._visit_children(node)

        self._current_func_name = old_name
        self._current_return_type = old_return_type
        self._current_types = old_types
        self._coord_decisions = old_decisions
        return node

    def visit_Decl(self, node):
        if node.init is not None:
            replacement = self._replacement_for_expr(
                node.init,
                action="rewrite_decl",
                target_name=node.name,
            )
            if replacement is not None:
                node.init = replacement
                return node
            node.init = self.visit(node.init)
        return node

    def visit_Assignment(self, node):
        if node.lvalue is not None:
            node.lvalue = self.visit(node.lvalue)

        if (
            node.op == "="
            and isinstance(node.lvalue, c_ast.ID)
            and node.rvalue is not None
        ):
            replacement = self._replacement_for_expr(
                node.rvalue,
                action="rewrite_assign",
                target_name=node.lvalue.name,
            )
            if replacement is not None:
                node.rvalue = replacement
                return node

        if node.rvalue is not None:
            node.rvalue = self.visit(node.rvalue)
        return node

    def visit_Return(self, node):
        if node.expr is not None:
            replacement = self._replacement_for_expr(
                node.expr,
                action="rewrite_return",
                return_type=self._current_return_type,
            )
            if replacement is not None:
                node.expr = replacement
                return node
            node.expr = self.visit(node.expr)
        return node

    def _replacement_for_expr(
        self,
        expr,
        *,
        action: str,
        target_name: str | None = None,
        return_type: str = "",
    ):
        coord_key = str(expr.coord) if getattr(expr, "coord", None) else None
        if coord_key is None:
            return None

        replacement_name = self._coord_decisions.get(coord_key)
        if not replacement_name:
            return None
        if target_name and replacement_name == target_name:
            return None

        replacement_type = self._type_name(replacement_name)
        if not self._is_rewrite_safe_type(replacement_type):
            return None
        if target_name is not None:
            target_type = self._type_name(target_name)
            if replacement_type != target_type:
                return None
        else:
            if replacement_type != return_type:
                return None

        self.changed = True
        detail = f"{self._current_func_name or '<anon>'}:{replacement_name}"
        self.ctx.record("ssa-gvn-rewrite", action, coord_key, detail)
        self.ctx.bump(f"ssa_gvn_rewrite.{action}")
        return c_ast.ID(replacement_name, coord=expr.coord)

    def _decisions_for_function(self, func_name: str) -> dict[str, str]:
        decisions: dict[str, str] = {}
        result = self.ctx.ssa_gvn_results.get(func_name)
        ssa_func = self.ctx.ssa_functions.get(func_name)
        if result is None or ssa_func is None:
            return decisions

        instruction_blocks = {
            instruction.name: block.name
            for block in ssa_func.blocks
            for instruction in block.instructions
        }

        for value_name, leader_name in result.redundant_values.items():
            try:
                instruction = ssa_func.instruction(value_name)
            except KeyError:
                continue
            if instruction_blocks.get(value_name) == instruction_blocks.get(leader_name):
                continue
            if not instruction.source_coord:
                continue

            for binding_name, binding_value in instruction.available_bindings:
                if binding_value != leader_name:
                    continue
                if not self._is_rewrite_safe_type(self._type_name(binding_name)):
                    continue
                decisions.setdefault(instruction.source_coord, binding_name)
                break

        return decisions

    def _type_name(self, var_name: str) -> str:
        type_name = self._current_types.get(var_name, "")
        if type_name:
            return type_name
        if not self._current_func_name:
            return ""
        func_info = self.ctx.functions.get(self._current_func_name)
        if func_info is None:
            return ""
        var_info = func_info.var_infos.get(var_name)
        if var_info is None:
            return ""
        return self._normalize_type_name(var_info.type_name or "")

    @classmethod
    def _normalize_type_name(cls, type_name: str) -> str:
        tokens = [
            token
            for token in type_name.replace("\t", " ").split()
            if token not in {"const", "volatile", "restrict", "__restrict", "register"}
        ]
        return " ".join(tokens)

    @classmethod
    def _is_rewrite_safe_type(cls, type_name: str) -> bool:
        normalized = cls._normalize_type_name(type_name)
        if not normalized:
            return False
        if any(ch in normalized for ch in "*[]()"):
            return False
        if normalized.startswith(("struct ", "union ", "enum ")):
            return False
        return normalized in cls._SCALAR_TYPES

    def _decl_type_name(self, node) -> str:
        if isinstance(node, c_ast.FuncDecl):
            return self._decl_type_name(node.type)
        if isinstance(node, c_ast.TypeDecl):
            return self._decl_type_name(node.type)
        if isinstance(node, c_ast.IdentifierType):
            return self._normalize_type_name(" ".join(node.names))
        return ""

    def _collect_function_types(self, funcdef) -> dict[str, str]:
        types: dict[str, str] = {}
        func_type = getattr(getattr(funcdef, "decl", None), "type", None)
        params = getattr(getattr(func_type, "args", None), "params", None) or ()
        for param in params:
            if isinstance(param, c_ast.Decl) and param.name:
                type_name = self._decl_type_name(param.type)
                if type_name:
                    types[param.name] = type_name

        def _walk(node):
            if node is None:
                return
            if isinstance(node, c_ast.Decl) and node.name:
                type_name = self._decl_type_name(node.type)
                if type_name:
                    types.setdefault(node.name, type_name)
            for _, child in node.children():
                if isinstance(child, c_ast.Node):
                    _walk(child)
                elif isinstance(child, list):
                    for item in child:
                        if isinstance(item, c_ast.Node):
                            _walk(item)

        _walk(getattr(funcdef, "body", None))
        return types


class SSAGVNRewritePass(ASTPass):
    """Reuse dominating source variables for SSA-GVN-proven redundant exprs."""

    name = "ssa-gvn-rewrite"

    def __init__(self):
        self._gvn = SSAGVNPass()

    def run(self, ast, ctx: PassContext):
        if not isinstance(ast, c_ast.FileAST):
            return None

        if not ctx.ssa_gvn_results:
            self._gvn.run(ast, ctx)

        rewriter = _SSAGVNRewriter(ctx)
        ast = rewriter.visit(ast)
        return ast if rewriter.changed else None
