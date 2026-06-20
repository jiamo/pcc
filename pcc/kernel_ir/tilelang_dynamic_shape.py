"""Bounded launch-time specialization for one TileLang shape dimension.

The Kernel IR remains fully static. This module owns a narrow contract that
validates one symbolic vector extent and specializes it through the strict
source importer before any device execution is possible.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from pcc.kernel_ir.ir import KernelModule
from pcc.kernel_ir.tilelang_import import TileLangImportError, import_tilelang_source


UINT32_MAX = (1 << 32) - 1
UINT64_MAX = (1 << 64) - 1
DYNAMIC_SHAPE_CONTRACT_VERSION = 1


class TileLangDynamicShapeError(ValueError):
    """A symbolic shape or requested specialization violates the contract."""


def _attr_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _find_function(tree: ast.AST, name: str, *, label: str) -> ast.FunctionDef:
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(matches) != 1:
        raise TileLangDynamicShapeError(
            f"dynamic-shape contract requires exactly one {label} function {name!r}"
        )
    return matches[0]


def _validate_symbol_uses(
    source: str,
    *,
    outer_function: str,
    prim_func: str,
    symbol: str,
) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise TileLangDynamicShapeError(f"invalid TileLang source: {exc}") from exc
    outer = _find_function(tree, outer_function, label="outer")
    prim = _find_function(outer, prim_func, label="prim_func")
    outer_args = {arg.arg for arg in outer.args.args}
    if symbol not in outer_args:
        raise TileLangDynamicShapeError(
            f"dynamic symbol {symbol!r} must be an argument of {outer_function!r}"
        )

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(prim):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    shape_uses = 0
    grid_uses = 0
    for node in ast.walk(prim):
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load) or node.id != symbol:
            continue
        parent = parents.get(node)
        grandparent = parents.get(parent) if parent is not None else None
        if (
            isinstance(parent, ast.Tuple)
            and isinstance(grandparent, ast.Call)
            and grandparent.args
            and grandparent.args[0] is parent
            and _attr_name(grandparent.func) == "T.Tensor"
            and len(parent.elts) == 1
            and parent.elts[0] is node
        ):
            shape_uses += 1
            continue
        if (
            isinstance(parent, ast.Call)
            and _attr_name(parent.func) == "T.ceildiv"
            and len(parent.args) == 2
            and not parent.keywords
            and parent.args[0] is node
            and isinstance(parent.args[1], (ast.Name, ast.Constant))
        ):
            grid_uses += 1
            continue
        rendered = ast.unparse(parent if parent is not None else node)
        raise TileLangDynamicShapeError(
            f"unsupported dynamic expression for {symbol!r}: {rendered}; "
            "the first contract accepts only Tensor((N,), ...) and T.ceildiv(N, threads)"
        )
    if shape_uses != 1 or grid_uses != 1:
        raise TileLangDynamicShapeError(
            f"the first dynamic-shape contract requires one Tensor((N,), ...) use and "
            f"one T.ceildiv(N, threads) use; got shape={shape_uses}, grid={grid_uses}"
        )


def _canonical_constants(constants: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(constants),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise TileLangDynamicShapeError(
            "dynamic-shape base constants must have a finite canonical JSON representation"
        ) from exc


@dataclass(frozen=True)
class TileLangShapeSpecialization:
    """One static Kernel IR specialization and its deterministic cache identity."""

    module: KernelModule
    cache_key: str
    symbol: str
    value: int
    required_buffer_nbytes: int
    grid_extent: int
    target: str = "metal"
    claim_mode: str = "bounded TileLang launch-time specialization; no runtime execution"


@dataclass(frozen=True)
class TileLangDynamicShapeContract:
    """Fail-closed contract for one bounded symbolic vector dimension."""

    source: str
    outer_function: str
    prim_func: str
    symbol: str
    min_value: int
    max_value: int
    element_nbytes: int
    max_buffer_nbytes: int
    base_constants: Mapping[str, Any]
    threads_constant: str = "threads"
    target: str = "metal"

    def __post_init__(self) -> None:
        if self.target != "metal":
            raise TileLangDynamicShapeError("the first dynamic-shape contract supports target='metal' only")
        if not self.symbol.isidentifier():
            raise TileLangDynamicShapeError(f"bad dynamic symbol {self.symbol!r}")
        for label, value in (("min_value", self.min_value), ("max_value", self.max_value)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > UINT64_MAX:
                raise TileLangDynamicShapeError(f"{label} must be an integer in [1, UINT64_MAX]")
        if self.min_value > self.max_value:
            raise TileLangDynamicShapeError("min_value cannot exceed max_value")
        if (
            isinstance(self.element_nbytes, bool)
            or not isinstance(self.element_nbytes, int)
            or self.element_nbytes < 1
            or self.element_nbytes > 16
        ):
            raise TileLangDynamicShapeError("element_nbytes must be an integer in [1, 16]")
        if (
            isinstance(self.max_buffer_nbytes, bool)
            or not isinstance(self.max_buffer_nbytes, int)
            or self.max_buffer_nbytes < 1
            or self.max_buffer_nbytes > UINT64_MAX
        ):
            raise TileLangDynamicShapeError("max_buffer_nbytes must be in [1, UINT64_MAX]")
        constants = dict(self.base_constants)
        if self.symbol in constants:
            raise TileLangDynamicShapeError(
                f"base_constants must omit dynamic symbol {self.symbol!r}"
            )
        threads = constants.get(self.threads_constant)
        if isinstance(threads, bool) or not isinstance(threads, int) or not 1 <= threads <= 1024:
            raise TileLangDynamicShapeError(
                f"base constant {self.threads_constant!r} must be an integer in [1, 1024]"
            )
        _canonical_constants(constants)
        _validate_symbol_uses(
            self.source,
            outer_function=self.outer_function,
            prim_func=self.prim_func,
            symbol=self.symbol,
        )
        object.__setattr__(self, "base_constants", MappingProxyType(constants))

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()

    @property
    def contract_id(self) -> str:
        payload = {
            "version": DYNAMIC_SHAPE_CONTRACT_VERSION,
            "source_sha256": self.source_sha256,
            "outer_function": self.outer_function,
            "prim_func": self.prim_func,
            "symbol": self.symbol,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "element_nbytes": self.element_nbytes,
            "max_buffer_nbytes": self.max_buffer_nbytes,
            "base_constants": json.loads(_canonical_constants(self.base_constants)),
            "threads_constant": self.threads_constant,
            "target": self.target,
        }
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _checked_value(self, value: int) -> tuple[int, int]:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TileLangDynamicShapeError(f"{self.symbol} specialization must be an integer")
        if value < self.min_value or value > self.max_value:
            raise TileLangDynamicShapeError(
                f"{self.symbol}={value} is outside [{self.min_value}, {self.max_value}]"
            )
        if value > UINT64_MAX // self.element_nbytes:
            raise TileLangDynamicShapeError(
                f"{self.symbol}={value} byte-size multiplication overflows uint64"
            )
        required_nbytes = value * self.element_nbytes
        if required_nbytes > self.max_buffer_nbytes:
            raise TileLangDynamicShapeError(
                f"{self.symbol}={value} requires {required_nbytes} buffer bytes, "
                f"above limit {self.max_buffer_nbytes}"
            )
        threads = int(self.base_constants[self.threads_constant])
        grid_extent = 1 + ((value - 1) // threads)
        if grid_extent > UINT32_MAX:
            raise TileLangDynamicShapeError(
                f"{self.symbol}={value} launch grid {grid_extent} exceeds uint32"
            )
        return required_nbytes, grid_extent

    def specialization_key(self, value: int) -> str:
        required_nbytes, grid_extent = self._checked_value(value)
        payload = {
            "contract_id": self.contract_id,
            "symbol_values": {self.symbol: value},
            "required_buffer_nbytes": required_nbytes,
            "grid_extent": grid_extent,
        }
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def specialize(self, value: int) -> TileLangShapeSpecialization:
        required_nbytes, grid_extent = self._checked_value(value)
        cache_key = self.specialization_key(value)
        constants = dict(self.base_constants)
        constants[self.symbol] = value
        try:
            module = import_tilelang_source(
                self.source,
                outer_function=self.outer_function,
                prim_func=self.prim_func,
                constants=constants,
                module_name=f"{self.prim_func}_{self.symbol}{value}_{cache_key[:12]}",
            )
        except TileLangImportError as exc:
            raise TileLangDynamicShapeError(
                f"static specialization {self.symbol}={value} failed strict TileLang import: {exc}"
            ) from exc
        return TileLangShapeSpecialization(
            module=module,
            cache_key=cache_key,
            symbol=self.symbol,
            value=value,
            required_buffer_nbytes=required_nbytes,
            grid_extent=grid_extent,
            target=self.target,
        )


__all__ = [
    "DYNAMIC_SHAPE_CONTRACT_VERSION",
    "UINT32_MAX",
    "UINT64_MAX",
    "TileLangDynamicShapeContract",
    "TileLangDynamicShapeError",
    "TileLangShapeSpecialization",
]
