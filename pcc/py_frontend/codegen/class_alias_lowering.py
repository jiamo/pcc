"""Class alias tracking helpers for Layer-1 codegen."""
from __future__ import annotations

from ..py_ast import Assign, Name


class ClassAliasLoweringMixin:
    def _resolve_class_alias(self, name: str) -> str:
        return self._class_aliases.get(name, name)

    def _maybe_register_class_alias_assign(self, stmt: Assign) -> bool:
        if len(stmt.targets) != 1:
            return False
        target = stmt.targets[0]
        value = stmt.value
        if not isinstance(target, Name) or not isinstance(value, Name):
            return False
        class_name = self._resolve_class_alias(value.ident)
        if class_name not in self.class_lowering.classes:
            return False
        self._class_aliases[target.ident] = class_name
        return True

