"""Import a strict TileLang Python-DSL subset into pcc Kernel IR.

This is a semantic importer, not a TileLang runtime. It parses Python source
with ``ast`` and accepts only the first Metal matmul-style subset used by the
local TileLang reference:

* ``@T.prim_func`` with ``T.Tensor``/``T.Buffer`` parameters, or the matching
  eager ``@tilelang.jit`` shape with static ``A: T.Tensor(...)`` declarations
  before one ``T.Kernel`` region
* one ``with T.Kernel(..., threads=...)`` launch region
* ``T.alloc_shared`` / ``T.alloc_fragment`` / ``T.alloc_local``
* ``T.clear`` / zero-valued ``T.fill`` / ``T.copy`` / ``T.atomic_add`` /
  ``T.gemm``
* split-k copy span metadata for the current ``bz * (K // split_k)``,
  ``bz * T.ceildiv(K, split_k)``, and exact expanded
  ``bz * ((K + split_k - 1) // split_k)`` / ``bz * (((K - 1) // split_k) + 1)``
  source-index shapes, including
  statically evaluable outer aliases such as ``splitK = K // split_k``
* ``T.use_swizzle(..., enable=...)`` as explicit swizzle scheduling metadata
* ``T.annotate_layout({})`` as explicit no-op metadata
* ``T.annotate_layout({buf: make_swizzled_layout(buf)})`` as local-buffer
  swizzled layout metadata
* one CUDA-SM90-only ``make_wgmma_swizzled_layout`` annotation as preserved
  owner/pass metadata that is rejected explicitly for a Metal target
* ``for ... in T.Pipelined(..., num_stages=...)`` as op attributes
* ``for ... in T.serial(...)`` for the same supported body subset, preserving
  serial extent metadata
* ``for ... in T.Parallel(...)`` as schedule metadata plus one canonical
  indexed elementwise assignment subset; Metal-safe ``coalesced_width``,
  ``prefer_async=False`` and ``annotations={...}`` metadata are preserved
* ``for ... in T.vectorized(...)`` as one-dimensional schedule metadata plus
  the same canonical indexed assignment subset; explicit ``annotations={...}``
  are preserved as metadata

Unknown TileLang constructs fail closed so pcc never claims support by
accident. The importer does not execute TileLang, TVM, torch, or user code.
"""

from __future__ import annotations

import ast
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    Layout,
    LocalBuffer,
    MemoryScope,
    ScalarType,
    validate_kernel,
)


class TileLangImportError(ValueError):
    """TileLang source could not be imported into the supported Kernel IR subset."""


# --- TileLang claim-boundary labels -----------------------------------------
#
# There are two DIFFERENT things that both mention "tilelang". They must never
# be conflated, and this module only ever provides the second one:
#
# * ``tilelang-package-cpython-compat`` -- a runtime ``import tilelang`` served
#   through the package / cpython-compat path. It links libpython and executes
#   the upstream TileLang/TVM runtime. This module DOES NOT provide it.
# * ``pcc-tilelang-source-subset`` -- what ``import_tilelang_source`` and
#   ``import_tilelang_file`` provide: an ``ast`` parse of a strict TileLang
#   Python-DSL subset into pcc Kernel IR. It does not execute TileLang/TVM/torch,
#   does not perform a runtime ``import tilelang``, and is NOT a pcc-native
#   ``import tilelang`` claim.
#
# See docs/design/pcc-tilelang-claim-boundary.md for the authoritative text.
TILELANG_PACKAGE_CPYTHON_COMPAT_CLAIM = "tilelang-package-cpython-compat"
TILELANG_SOURCE_SUBSET_CLAIM = "pcc-tilelang-source-subset"
TILELANG_WGMMA_LAYOUT_REFERENCE_COMMIT = "ed00dfcd7f9c200e1150896b1be59c41ff3e8d9d"
TILELANG_WGMMA_LAYOUT_REFERENCE_PATH = "tilelang/layout/swizzle.py"
TILELANG_WGMMA_LAYOUT_REFERENCE_SHA256 = "0389a53684dec7697bd22c8e1b30f30a6a1afc5e02980de540c277080082bb55"

# Attribute stamped onto every KernelModule this module returns so downstream
# consumers can prove which claim label produced it. Frozen-dataclass instances
# still carry a ``__dict__`` (no ``slots=True``), so ``object.__setattr__``
# attaches the metadata without joining the dataclass field/eq/hash surface.
_CLAIM_ATTR = "__pcc_tilelang_claim__"


def tilelang_source_import_claim() -> dict[str, Any]:
    """Return the honest claim metadata for the source-subset importer.

    This is the *only* claim this module makes: a compiler-side AST parse into
    pcc Kernel IR. It is deliberately explicit that no TileLang runtime runs and
    that this is not a pcc-native ``import tilelang``.
    """
    return {
        "mode": TILELANG_SOURCE_SUBSET_CLAIM,
        "executes_tilelang_runtime": False,
        "is_pcc_native_import_tilelang": False,
        "links_libpython": False,
        "not_this_claim": TILELANG_PACKAGE_CPYTHON_COMPAT_CLAIM,
        "description": (
            "AST parse of a strict TileLang Python-DSL subset into pcc Kernel IR; "
            "does not run TileLang/TVM/torch, does not perform a runtime "
            "`import tilelang`, and is not a pcc-native import claim."
        ),
    }


def _attach_source_subset_claim(module: KernelModule) -> KernelModule:
    """Stamp *module* with :func:`tilelang_source_import_claim` metadata."""
    object.__setattr__(module, _CLAIM_ATTR, tilelang_source_import_claim())
    return module


def tilelang_source_import_claim_of(module: KernelModule) -> dict[str, Any]:
    """Read back the claim metadata stamped by the source-subset importer.

    Raises :class:`TileLangImportError` if *module* did not come from
    ``import_tilelang_source`` / ``import_tilelang_file`` (i.e. it carries no
    source-subset claim), so a caller can never silently treat an arbitrary
    KernelModule as a proven ``pcc-tilelang-source-subset`` artifact.
    """
    claim = getattr(module, _CLAIM_ATTR, None)
    if not isinstance(claim, dict):
        raise TileLangImportError(
            "KernelModule was not produced by import_tilelang_source/"
            "import_tilelang_file; no pcc-tilelang-source-subset claim metadata "
            "is attached"
        )
    return dict(claim)


def assert_not_native_import_tilelang_claim(claim_text: str) -> None:
    """Reject any prose that describes this path as a native ``import tilelang``.

    The source-subset importer parses text; it never executes TileLang and never
    performs a runtime ``import tilelang``. If *claim_text* asserts a pcc-native
    ``import tilelang``, a TileLang runtime execution, or (mis)applies the
    ``tilelang-package-cpython-compat`` label to this path, raise
    :class:`TileLangImportError`. This is a small, honest guard, not a parser.
    """
    text = " ".join(str(claim_text).lower().split())
    native_markers = (
        "pcc-native import tilelang",
        "pcc native import tilelang",
        "native import tilelang",
        "natively import tilelang",
        "natively imports tilelang",
        "native `import tilelang`",
    )
    runtime_markers = (
        "executes tilelang",
        "executes the tilelang runtime",
        "runs tilelang",
        "runs the tilelang runtime",
        "runtime import tilelang",
        "imports the tilelang runtime",
    )
    for marker in native_markers + runtime_markers:
        if marker in text:
            raise TileLangImportError(
                "claim text asserts a native `import tilelang` / TileLang runtime "
                f"execution, but this module only provides {TILELANG_SOURCE_SUBSET_CLAIM!r} "
                "(compiler-side source parse, no runtime import tilelang): "
                f"{claim_text!r}"
            )
    # The package/cpython-compat label must never be produced by this module.
    if TILELANG_PACKAGE_CPYTHON_COMPAT_CLAIM in text:
        raise TileLangImportError(
            f"claim text applies the {TILELANG_PACKAGE_CPYTHON_COMPAT_CLAIM!r} label, "
            "but the source-subset importer never runs the tilelang package; it only "
            f"provides {TILELANG_SOURCE_SUBSET_CLAIM!r}: {claim_text!r}"
        )


_SPLIT_K_SPAN_ALIASES = "__pcc_split_k_span_aliases__"


_DTYPES: dict[str, ScalarType] = {
    "bool": ScalarType.BOOL,
    "int8": ScalarType.I8,
    "i8": ScalarType.I8,
    "uint8": ScalarType.U8,
    "u8": ScalarType.U8,
    "int16": ScalarType.I16,
    "i16": ScalarType.I16,
    "uint16": ScalarType.U16,
    "u16": ScalarType.U16,
    "int32": ScalarType.I32,
    "i32": ScalarType.I32,
    "int64": ScalarType.I64,
    "i64": ScalarType.I64,
    "uint32": ScalarType.U32,
    "u32": ScalarType.U32,
    "uint64": ScalarType.U64,
    "u64": ScalarType.U64,
    "float16": ScalarType.F16,
    "float32": ScalarType.F32,
    "float64": ScalarType.F64,
    "f16": ScalarType.F16,
    "f32": ScalarType.F32,
    "f64": ScalarType.F64,
}


def _attr_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _attr_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _is_t_call(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Call) and _attr_name(node.func) == f"T.{name}"


def _eval_positive_int_metadata(node: ast.AST, env: Mapping[str, Any], *, name: str) -> int:
    value = _eval_expr(node, env)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TileLangImportError(f"{name} expects a positive integer, got {value!r}")
    return value


def _eval_expr(node: ast.AST, env: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise TileLangImportError(f"unknown symbolic value {node.id!r}")
    if isinstance(node, ast.Attribute):
        name = _attr_name(node)
        if name and name in env:
            return env[name]
        if name and name.startswith("T."):
            return name[2:]
        if name and name.startswith("GemmWarpPolicy."):
            return name
        raise TileLangImportError(f"unsupported attribute expression {name!r}")
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise TileLangImportError(
                f"unsupported comparison in TileLang metadata: {ast.dump(node)}"
            )
        left = _eval_expr(node.left, env)
        right = _eval_expr(node.comparators[0], env)
        op = node.ops[0]
        if isinstance(op, ast.Is):
            return left is right
        if isinstance(op, ast.IsNot):
            return left is not right
        raise TileLangImportError(
            f"unsupported comparison in TileLang metadata: {ast.dump(node)}"
        )
    if isinstance(node, ast.IfExp):
        test_value = _eval_expr(node.test, env)
        if not isinstance(test_value, bool):
            raise TileLangImportError(
                f"conditional TileLang metadata test must be boolean, got {test_value!r}"
            )
        return _eval_expr(node.body if test_value else node.orelse, env)
    if isinstance(node, ast.Tuple):
        return tuple(_eval_expr(elt, env) for elt in node.elts)
    if isinstance(node, ast.List):
        return [_eval_expr(elt, env) for elt in node.elts]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _eval_expr(node.operand, env)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left = _eval_expr(node.left, env)
        right = _eval_expr(node.right, env)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Div):
            if left % right != 0:
                raise TileLangImportError("non-integral division is not accepted in kernel metadata")
            return left // right
    if _is_t_call(node, "ceildiv"):
        if len(node.args) != 2:
            raise TileLangImportError("T.ceildiv expects exactly two arguments")
        return math.ceil(_eval_expr(node.args[0], env) / _eval_expr(node.args[1], env))
    if isinstance(node, ast.Call) and _attr_name(node.func) == "T.GemmWarpPolicy.from_warp_partition":
        if len(node.args) != 2 or node.keywords:
            raise TileLangImportError(
                "T.GemmWarpPolicy.from_warp_partition expects exactly two positional arguments"
            )
        return (
            _eval_positive_int_metadata(
                node.args[0],
                env,
                name="T.GemmWarpPolicy.from_warp_partition m_warp",
            ),
            _eval_positive_int_metadata(
                node.args[1],
                env,
                name="T.GemmWarpPolicy.from_warp_partition n_warp",
            ),
        )
    raise TileLangImportError(f"unsupported expression in TileLang metadata: {ast.dump(node)}")


def _eval_int(node: ast.AST, env: Mapping[str, Any]) -> int:
    value = _eval_expr(node, env)
    if not isinstance(value, int) or value <= 0:
        raise TileLangImportError(f"expected positive integer metadata, got {value!r}")
    return value


def _eval_bool(node: ast.AST, env: Mapping[str, Any]) -> bool:
    value = _eval_expr(node, env)
    if not isinstance(value, bool):
        raise TileLangImportError(f"expected boolean TileLang metadata, got {value!r}")
    return value


def _eval_metadata_dict(node: ast.AST, env: Mapping[str, Any], *, name: str) -> dict[str, Any]:
    if not isinstance(node, ast.Dict):
        raise TileLangImportError(f"{name} expects a dict literal")
    result: dict[str, Any] = {}
    for key_node, value_node in zip(node.keys, node.values):
        if key_node is None:
            raise TileLangImportError(f"{name} does not support dict unpacking")
        key = _eval_expr(key_node, env)
        if not isinstance(key, str):
            raise TileLangImportError(f"{name} keys must be strings, got {key!r}")
        value = _eval_expr(value_node, env)
        if not isinstance(value, (bool, int, str)):
            raise TileLangImportError(
                f"{name} values must be bool/int/str metadata, got {value!r}"
            )
        result[key] = value
    return result


def _eval_zero_start_loop_extent(call: ast.Call, env: Mapping[str, Any], *, name: str) -> int:
    if len(call.args) == 1:
        return _eval_int(call.args[0], env)
    if len(call.args) == 2:
        start = _eval_expr(call.args[0], env)
        if start != 0:
            raise TileLangImportError(
                f"{name} importer currently supports only zero-start ranges, got start={start!r}"
            )
        return _eval_int(call.args[1], env)
    raise TileLangImportError(
        f"{name} importer accepts one extent or a zero-start range"
    )


def _eval_loop_range(
    call: ast.Call,
    env: Mapping[str, Any],
    *,
    name: str,
    allow_step: bool = False,
) -> tuple[int, int, int]:
    if len(call.args) == 1:
        return 0, _eval_int(call.args[0], env), 1
    if len(call.args) in {2, 3}:
        if len(call.args) == 3 and not allow_step:
            raise TileLangImportError(
                f"{name} importer accepts one extent or a start/end range"
            )
        start = _eval_expr(call.args[0], env)
        end = _eval_int(call.args[1], env)
        step = _eval_expr(call.args[2], env) if len(call.args) == 3 else 1
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise TileLangImportError(
                f"{name} importer expects a non-negative integer start, got {start!r}"
            )
        if isinstance(step, bool) or not isinstance(step, int) or step <= 0:
            raise TileLangImportError(
                f"{name} importer expects a positive integer step, got {step!r}"
            )
        if end <= start:
            raise TileLangImportError(
                f"{name} importer expects end > start, got start={start!r}, end={end!r}"
            )
        return start, end - start, step
    raise TileLangImportError(
        f"{name} importer accepts one extent or a start/end range"
    )


def _eval_shape(node: ast.AST, env: Mapping[str, Any]) -> tuple[int, ...]:
    value = _eval_expr(node, env)
    if isinstance(value, int):
        value = (value,)
    if not isinstance(value, tuple) or not value:
        raise TileLangImportError(f"expected static TileLang shape tuple, got {value!r}")
    shape: list[int] = []
    for dim in value:
        if not isinstance(dim, int) or dim <= 0:
            raise TileLangImportError(f"expected positive integer shape dim, got {dim!r}")
        shape.append(dim)
    return tuple(shape)


def _dtype_from_expr(node: ast.AST, env: Mapping[str, Any]) -> ScalarType:
    value = _eval_expr(node, env)
    if isinstance(value, ScalarType):
        return value
    if not isinstance(value, str):
        raise TileLangImportError(f"expected TileLang dtype, got {value!r}")
    dtype = _DTYPES.get(value.removeprefix("T."))
    if dtype is None:
        raise TileLangImportError(f"unsupported TileLang dtype {value!r}")
    return dtype


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _parse_tensor_param(arg: ast.arg, env: Mapping[str, Any]) -> BufferParam:
    ann = arg.annotation
    if not isinstance(ann, ast.Call) or _attr_name(ann.func) not in {"T.Tensor", "T.Buffer"}:
        raise TileLangImportError(
            f"TileLang kernel parameter {arg.arg!r} must use T.Tensor/T.Buffer annotation"
        )
    if len(ann.args) < 2:
        raise TileLangImportError(f"Tensor parameter {arg.arg!r} must include shape and dtype")
    shape = _eval_shape(ann.args[0], env)
    dtype = _dtype_from_expr(ann.args[1], env)
    return BufferParam(
        arg.arg,
        dtype,
        rank=len(shape),
        shape=shape,
        scope=MemoryScope.GLOBAL,
        layout=Layout.ROW_MAJOR,
    )


def _find_outer_function(tree: ast.Module, outer_function: str | None) -> ast.FunctionDef | None:
    if outer_function is None:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == outer_function:
            return node
    raise TileLangImportError(f"outer function {outer_function!r} not found")


def _merge_outer_defaults(func: ast.FunctionDef | None, constants: Mapping[str, Any]) -> dict[str, Any]:
    env = dict(constants)
    if func is None:
        return env
    args = func.args.args
    defaults = func.args.defaults
    if not defaults:
        return env
    default_offset = len(args) - len(defaults)
    for arg, default in zip(args[default_offset:], defaults):
        if arg.arg not in env:
            env[arg.arg] = _eval_expr(default, env)
    return env


def _split_k_aliases(env: Mapping[str, Any]) -> Mapping[str, tuple[str, int]]:
    aliases = env.get(_SPLIT_K_SPAN_ALIASES)
    if isinstance(aliases, dict):
        return aliases
    return {}


def _merge_outer_static_aliases(
    func: ast.FunctionDef | None,
    prim_func: ast.FunctionDef,
    env: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge statically evaluable outer aliases before the selected prim_func.

    This deliberately evaluates only metadata expressions already accepted by
    ``_eval_expr``. Unsupported outer statements are left alone; if an unsupported
    value is later referenced inside the kernel, the normal unknown-symbol path
    fails closed.
    """

    if func is None:
        return dict(env)
    merged = dict(env)
    split_aliases = dict(_split_k_aliases(merged))
    if split_aliases:
        merged[_SPLIT_K_SPAN_ALIASES] = split_aliases
    for stmt in func.body:
        if stmt is prim_func:
            break
        targets: list[ast.Name] = []
        value: ast.AST | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            if isinstance(stmt.targets[0], ast.Name):
                targets = [stmt.targets[0]]
                value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            targets = [stmt.target]
            value = stmt.value
        if not targets or value is None:
            continue
        try:
            evaluated = _eval_expr(value, merged)
        except TileLangImportError:
            continue
        split_span = _split_k_span_expr(value, merged)
        for target in targets:
            merged[target.id] = evaluated
            if split_span is not None:
                split_aliases[target.id] = split_span
            elif target.id in split_aliases:
                del split_aliases[target.id]
    if split_aliases:
        merged[_SPLIT_K_SPAN_ALIASES] = split_aliases
    elif _SPLIT_K_SPAN_ALIASES in merged:
        del merged[_SPLIT_K_SPAN_ALIASES]
    return merged


def _has_prim_func_decorator(func: ast.FunctionDef) -> bool:
    return any(_attr_name(dec) == "T.prim_func" for dec in func.decorator_list)


def _find_prim_func(root: ast.AST, prim_func: str | None) -> ast.FunctionDef:
    matches: list[ast.FunctionDef] = []
    for node in ast.walk(root):
        if isinstance(node, ast.FunctionDef) and _has_prim_func_decorator(node):
            if prim_func is None or node.name == prim_func:
                matches.append(node)
    if not matches:
        wanted = prim_func if prim_func is not None else "any @T.prim_func"
        raise TileLangImportError(f"TileLang prim_func {wanted!r} not found")
    if len(matches) > 1:
        names = [m.name for m in matches]
        raise TileLangImportError(f"multiple TileLang prim_func matches {names}; choose one")
    return matches[0]


def _kernel_regions(func: ast.FunctionDef) -> list[ast.With]:
    return [
        stmt
        for stmt in func.body
        if isinstance(stmt, ast.With)
        and len(stmt.items) == 1
        and _is_t_call(stmt.items[0].context_expr, "Kernel")
    ]


def _parse_eager_tensor_declarations(
    func: ast.FunctionDef,
    env: Mapping[str, Any],
) -> tuple[BufferParam, ...]:
    params: list[BufferParam] = []
    seen: set[str] = set()
    for stmt in func.body:
        if isinstance(stmt, ast.With):
            break
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        ann = stmt.annotation
        if not isinstance(ann, ast.Call) or _attr_name(ann.func) not in {"T.Tensor", "T.Buffer"}:
            continue
        name = stmt.target.id
        if name in seen:
            raise TileLangImportError(f"duplicate TileLang tensor declaration {name!r}")
        seen.add(name)
        params.append(_parse_tensor_param(ast.arg(arg=name, annotation=ann), env))
    if not params:
        raise TileLangImportError(
            f"eager TileLang function {func.name!r} has no static T.Tensor/T.Buffer declarations"
        )
    return tuple(params)


def _symbol_ref(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        return _symbol_ref(node.value)
    raise TileLangImportError(f"expected TileLang buffer/local reference, got {ast.dump(node)}")


def _subscript_indices(node: ast.AST) -> tuple[ast.AST, ...]:
    if not isinstance(node, ast.Subscript):
        return ()
    slice_node = node.slice
    if isinstance(slice_node, ast.Tuple):
        return tuple(slice_node.elts)
    return (slice_node,)


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _is_int_constant(node: ast.AST, value: int) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
        and node.value == value
    )


def _int_constant_value(node: ast.AST) -> int | None:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        return node.value
    return None


def _linear_k_splitk_terms(node: ast.AST) -> tuple[int, int, int] | None:
    """Return (K coefficient, split_k coefficient, const) for tiny metadata exprs."""

    if _is_name(node, "K"):
        return (1, 0, 0)
    if _is_name(node, "split_k"):
        return (0, 1, 0)
    constant = _int_constant_value(node)
    if constant is not None:
        return (0, 0, constant)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        left = _linear_k_splitk_terms(node.left)
        right = _linear_k_splitk_terms(node.right)
        if left is None or right is None:
            return None
        sign = 1 if isinstance(node.op, ast.Add) else -1
        return (
            left[0] + sign * right[0],
            left[1] + sign * right[1],
            left[2] + sign * right[2],
        )
    return None


def _is_expanded_k_splitk_ceildiv(node: ast.AST) -> bool:
    return _linear_k_splitk_terms(node) == (1, 1, -1)


def _is_k_minus_one_splitk_floor(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.FloorDiv)
        and _linear_k_splitk_terms(node.left) == (1, 0, -1)
        and _is_name(node.right, "split_k")
    )


def _is_floor_plus_one_k_splitk_ceildiv(node: ast.AST) -> bool:
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add):
        return False
    return (
        _is_k_minus_one_splitk_floor(node.left)
        and _is_int_constant(node.right, 1)
    ) or (
        _is_int_constant(node.left, 1)
        and _is_k_minus_one_splitk_floor(node.right)
    )


def _split_k_span_expr(node: ast.AST, env: Mapping[str, Any]) -> tuple[str, int] | None:
    if isinstance(node, ast.Name):
        return _split_k_aliases(env).get(node.id)
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.FloorDiv)
        and _is_name(node.left, "K")
        and _is_name(node.right, "split_k")
    ):
        return "floor_div", _eval_int(node, env)
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.FloorDiv)
        and _is_expanded_k_splitk_ceildiv(node.left)
        and _is_name(node.right, "split_k")
    ):
        return "ceildiv", _eval_int(node, env)
    if _is_floor_plus_one_k_splitk_ceildiv(node):
        return "ceildiv", _eval_int(node, env)
    if _is_t_call(node, "ceildiv") and len(node.args) == 2:
        if _is_name(node.args[0], "K") and _is_name(node.args[1], "split_k"):
            return "ceildiv", _eval_int(node, env)
    return None


def _split_k_span_from_index(
    node: ast.AST,
    env: Mapping[str, Any],
    *,
    split_axis_var: str,
) -> tuple[str, int] | None:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        if _is_name(node.left, split_axis_var):
            return _split_k_span_expr(node.right, env)
        if _is_name(node.right, split_axis_var):
            return _split_k_span_expr(node.left, env)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        return (
            _split_k_span_from_index(node.left, env, split_axis_var=split_axis_var)
            or _split_k_span_from_index(node.right, env, split_axis_var=split_axis_var)
        )
    return None


def _split_k_copy_index_attrs(
    node: ast.AST,
    env: Mapping[str, Any],
    *,
    kernel_axis_vars: tuple[str, ...],
) -> dict[str, Any]:
    if len(kernel_axis_vars) < 3:
        return {}
    split_axis_var = kernel_axis_vars[2]
    for index in _subscript_indices(node):
        span = _split_k_span_from_index(index, env, split_axis_var=split_axis_var)
        if span is not None:
            mode, value = span
            return {
                "split_k_span_mode": mode,
                "split_k_span": value,
                "split_k_axis_var": split_axis_var,
            }
    return {}


def _parse_alloc(stmt: ast.Assign, env: Mapping[str, Any]) -> LocalBuffer | None:
    if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
        return None
    if not isinstance(stmt.value, ast.Call):
        return None
    call = stmt.value
    func_name = _attr_name(call.func)
    scope_by_func = {
        "T.alloc_shared": MemoryScope.SHARED,
        "T.alloc_fragment": MemoryScope.FRAGMENT,
        "T.alloc_local": MemoryScope.LOCAL,
    }
    scope = scope_by_func.get(func_name)
    if scope is None:
        return None
    if len(call.args) < 2:
        raise TileLangImportError(f"{func_name} requires shape and dtype")
    shape = _eval_shape(call.args[0], env)
    dtype = _dtype_from_expr(call.args[1], env)
    scope_kw = _kw(call, "scope")
    if scope_kw is not None:
        raw_scope = _eval_expr(scope_kw, env)
        if raw_scope not in {scope.value, "threadgroup", "thread"}:
            raise TileLangImportError(
                f"{func_name} scope override {raw_scope!r} conflicts with {scope.value!r}"
            )
    return LocalBuffer(
        stmt.targets[0].id,
        dtype,
        shape=shape,
        scope=scope,
        layout=Layout.TILE,
    )


def _attrs_from_keywords(call: ast.Call, env: Mapping[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            raise TileLangImportError("TileLang **kwargs are not supported in kernel import")
        value = _eval_expr(keyword.value, env)
        if value is not None:
            attrs[keyword.arg] = value
    return attrs


def _normalize_gemm_policy_metadata(value: Any) -> str | tuple[int, int]:
    if isinstance(value, str):
        if value.startswith("GemmWarpPolicy."):
            return value
        raise TileLangImportError(
            "T.gemm policy metadata must be a GemmWarpPolicy.* string or "
            f"warp-partition pair, got {value!r}"
        )
    if isinstance(value, (list, tuple)) and len(value) == 2:
        rows, cols = value
        if (
            not isinstance(rows, bool)
            and not isinstance(cols, bool)
            and isinstance(rows, int)
            and isinstance(cols, int)
            and rows > 0
            and cols > 0
        ):
            return (rows, cols)
    raise TileLangImportError(
        "T.gemm policy metadata must be a GemmWarpPolicy.* string or "
        f"positive warp-partition pair, got {value!r}"
    )


def _set_attr_once(attrs: dict[str, Any], key: str, value: Any) -> None:
    existing = attrs.get(key)
    if key in attrs and existing != value:
        raise TileLangImportError(
            f"conflicting TileLang metadata for {key!r}: {existing!r} vs {value!r}"
        )
    attrs[key] = value


def _inside_scheduled_thread_loop(attrs: Mapping[str, Any]) -> bool:
    return "parallel_extents" in attrs or "vectorized_extent" in attrs


def _scheduled_thread_loop_body_error(stmt: ast.stmt) -> TileLangImportError:
    return TileLangImportError(
        "executable T.Parallel/T.vectorized loop bodies are not supported "
        "outside the bounded canonical indexed assignment subset; only exact "
        "loop-indexed loads/stores and supported staging ops are accepted, got "
        f"{ast.dump(stmt)}"
    )


def _for_target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            if not isinstance(elt, ast.Name):
                raise TileLangImportError(
                    f"T.Parallel loop target must contain names only, got {ast.dump(target)}"
                )
            names.append(elt.id)
        return names
    raise TileLangImportError(
        f"T.Parallel loop target must be a name or tuple/list of names, got {ast.dump(target)}"
    )


_SCHEDULED_BINARY_OPS = {
    ast.Add: "add",
    ast.Sub: "sub",
    ast.Mult: "mul",
    ast.Div: "div",
}


def _canonical_scheduled_index(
    node: ast.AST, attrs: Mapping[str, Any]
) -> dict[str, Any]:
    if "parallel_extents" in attrs:
        names = attrs.get("parallel_vars")
        values = list(node.elts) if isinstance(node, ast.Tuple) else [node]
        if (
            not isinstance(names, list)
            or len(values) != len(names)
            or any(
                not isinstance(value, ast.Name) or value.id != name
                for value, name in zip(values, names)
            )
        ):
            raise _scheduled_thread_loop_body_error(ast.Expr(value=node))
        return {"kind": "thread_id_x"}
    name = attrs.get("vectorized_var")
    values = list(node.elts) if isinstance(node, ast.Tuple) else [node]
    if len(values) == 2 and isinstance(values[0], ast.Constant) and values[0].value == 0:
        values = values[1:]
    if (
        not isinstance(name, str)
        or len(values) != 1
        or not isinstance(values[0], ast.Name)
        or values[0].id != name
    ):
        raise _scheduled_thread_loop_body_error(ast.Expr(value=node))
    return {"kind": "thread_id_x"}


def _scheduled_expr_record(
    node: ast.AST, attrs: Mapping[str, Any]
) -> tuple[dict[str, Any], set[str]]:
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool):
            return {"kind": "literal", "value": value}, set()
        if isinstance(value, int) and -(1 << 31) <= value < (1 << 32):
            return {"kind": "literal", "value": value}, set()
        if isinstance(value, float) and math.isfinite(value):
            return {"kind": "literal", "value": value}, set()
        raise _scheduled_thread_loop_body_error(ast.Expr(value=node))
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        index = _canonical_scheduled_index(node.slice, attrs)
        name = node.value.id
        return {"kind": "load", "buffer": name, "index": index}, {name}
    if isinstance(node, ast.BinOp) and type(node.op) in _SCHEDULED_BINARY_OPS:
        left, left_refs = _scheduled_expr_record(node.left, attrs)
        right, right_refs = _scheduled_expr_record(node.right, attrs)
        return (
            {
                "kind": "binary",
                "op": _SCHEDULED_BINARY_OPS[type(node.op)],
                "left": left,
                "right": right,
            },
            left_refs | right_refs,
        )
    raise _scheduled_thread_loop_body_error(ast.Expr(value=node))


def _parse_scheduled_assignment(
    stmt: ast.Assign, attrs: Mapping[str, Any]
) -> KernelOp:
    if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Subscript):
        raise _scheduled_thread_loop_body_error(stmt)
    target = stmt.targets[0]
    if not isinstance(target.value, ast.Name):
        raise _scheduled_thread_loop_body_error(stmt)
    target_name = target.value.id
    index = _canonical_scheduled_index(target.slice, attrs)
    value, refs = _scheduled_expr_record(stmt.value, attrs)
    all_refs = {target_name} | refs
    return KernelOp(
        "indexed_store",
        (target_name, *tuple(sorted(all_refs - {target_name}))),
        {"index": index, "value": value},
    )


def _swizzled_layout_target(node: ast.AST, env: Mapping[str, Any]) -> str:
    if not isinstance(node, ast.Call):
        raise TileLangImportError(
            f"T.annotate_layout currently supports only make_swizzled_layout(...), got {ast.dump(node)}"
        )
    func_name = _attr_name(node.func)
    if func_name not in {"make_swizzled_layout", "tilelang.layout.make_swizzled_layout"}:
        raise TileLangImportError(
            f"T.annotate_layout layout helper {func_name!r} is not supported"
        )
    if len(node.args) != 1:
        raise TileLangImportError("make_swizzled_layout expects exactly one buffer argument")
    for keyword in node.keywords:
        if keyword.arg not in {"k_major", "allow_pad"}:
            raise TileLangImportError(
                f"make_swizzled_layout keyword {keyword.arg!r} is not supported"
            )
        value = _eval_expr(keyword.value, env)
        if value is not True:
            raise TileLangImportError(
                "make_swizzled_layout non-default options are not supported"
            )
    return _symbol_ref(node.args[0])


def _wgmma_layout_annotation(node: ast.AST, env: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(node, ast.Call):
        return None
    func_name = _attr_name(node.func)
    accepted_names = {
        "make_wgmma_swizzled_layout",
        "tilelang.layout.make_wgmma_swizzled_layout",
    }
    if func_name not in accepted_names:
        return None
    if len(node.args) != 1:
        raise TileLangImportError(
            "make_wgmma_swizzled_layout expects exactly one buffer argument"
        )
    continuity = -1
    k_major = True
    seen: set[str] = set()
    for keyword in node.keywords:
        if keyword.arg is None or keyword.arg not in {"continuity", "k_major"}:
            raise TileLangImportError(
                f"make_wgmma_swizzled_layout keyword {keyword.arg!r} is not supported"
            )
        if keyword.arg in seen:
            raise TileLangImportError(
                f"make_wgmma_swizzled_layout keyword {keyword.arg!r} is repeated"
            )
        seen.add(keyword.arg)
        value = _eval_expr(keyword.value, env)
        if keyword.arg == "continuity":
            if value is None:
                continuity = -1
            elif isinstance(value, bool) or not isinstance(value, int) or value < -1:
                raise TileLangImportError(
                    "make_wgmma_swizzled_layout continuity must be a static integer >= -1 or None"
                )
            else:
                continuity = value
        else:
            if not isinstance(value, bool):
                raise TileLangImportError(
                    "make_wgmma_swizzled_layout k_major must be a static boolean"
                )
            k_major = value
    return _symbol_ref(node.args[0]), {
        "entries": 1,
        "layout_helper": "tilelang.layout.make_wgmma_swizzled_layout",
        "layout_owner": "tilelang",
        "layout_pass": "tilelang.transform.LayoutInference",
        "required_target": "cuda-sm90-wgmma",
        "metal_legal": False,
        "wgmma": True,
        "continuity": continuity,
        "k_major": k_major,
        "reference_commit": TILELANG_WGMMA_LAYOUT_REFERENCE_COMMIT,
        "reference_path": TILELANG_WGMMA_LAYOUT_REFERENCE_PATH,
        "reference_sha256": TILELANG_WGMMA_LAYOUT_REFERENCE_SHA256,
    }


def _apply_layout_annotation(
    call: ast.Call,
    env: Mapping[str, Any],
    extra_attrs: Mapping[str, Any],
    locals_: list[LocalBuffer],
) -> KernelOp | None:
    if call.keywords:
        raise TileLangImportError("T.annotate_layout keyword arguments are not supported")
    if len(call.args) != 1 or not isinstance(call.args[0], ast.Dict):
        raise TileLangImportError("T.annotate_layout expects one dict argument")
    if extra_attrs:
        raise TileLangImportError("T.annotate_layout cannot appear inside a scheduled loop")

    mapping = call.args[0]
    if not mapping.keys:
        return KernelOp("layout_annotation", (), {"entries": 0})

    locals_by_name = {local.name: index for index, local in enumerate(locals_)}
    for key, value in zip(mapping.keys, mapping.values):
        if key is None:
            raise TileLangImportError("T.annotate_layout dict unpacking is not supported")
        buffer_name = _symbol_ref(key)
        wgmma_annotation = _wgmma_layout_annotation(value, env)
        if wgmma_annotation is not None:
            if len(mapping.keys) != 1:
                raise TileLangImportError(
                    "the first make_wgmma_swizzled_layout subset requires exactly one annotation entry"
                )
            target_name, attrs = wgmma_annotation
            if target_name != buffer_name:
                raise TileLangImportError(
                    f"T.annotate_layout key {buffer_name!r} does not match layout target {target_name!r}"
                )
            index = locals_by_name.get(buffer_name)
            if index is None:
                raise TileLangImportError(
                    f"T.annotate_layout target {buffer_name!r} must be a previously allocated local buffer"
                )
            local = locals_[index]
            if local.scope != MemoryScope.SHARED or len(local.shape) != 2:
                raise TileLangImportError(
                    "make_wgmma_swizzled_layout requires a rank-2 shared buffer"
                )
            return KernelOp("layout_annotation", (buffer_name,), attrs)
        target_name = _swizzled_layout_target(value, env)
        if target_name != buffer_name:
            raise TileLangImportError(
                f"T.annotate_layout key {buffer_name!r} does not match layout target {target_name!r}"
            )
        index = locals_by_name.get(buffer_name)
        if index is None:
            raise TileLangImportError(
                f"T.annotate_layout target {buffer_name!r} must be a previously allocated local buffer"
            )
        local = locals_[index]
        if local.scope != MemoryScope.SHARED:
            raise TileLangImportError(
                f"T.annotate_layout swizzled layout currently supports shared buffers only, got {local.scope.value!r}"
            )
        locals_[index] = replace(local, layout=Layout.SWIZZLED)
    return None


def _parse_expr_call(
    expr: ast.Expr,
    env: Mapping[str, Any],
    extra_attrs: Mapping[str, Any],
    *,
    kernel_axis_vars: tuple[str, ...],
) -> KernelOp | None:
    if not isinstance(expr.value, ast.Call):
        return None
    call = expr.value
    func_name = _attr_name(call.func)
    attrs = dict(extra_attrs)
    if func_name == "T.clear":
        if len(call.args) != 1:
            raise TileLangImportError("T.clear expects one argument")
        if call.keywords:
            raise TileLangImportError("T.clear keyword arguments are not supported")
        attrs["value"] = 0
        return KernelOp("fill", (_symbol_ref(call.args[0]),), attrs)
    if func_name == "T.fill":
        if call.keywords:
            raise TileLangImportError("T.fill keyword arguments are not supported")
        if len(call.args) != 2:
            raise TileLangImportError("T.fill expects a buffer and a value")
        value = _eval_expr(call.args[1], env)
        if type(value) not in {int, float} or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            raise TileLangImportError(
                "T.fill value must be a finite static numeric scalar"
            )
        attrs["value"] = value
        return KernelOp("fill", (_symbol_ref(call.args[0]),), attrs)
    if func_name == "T.copy":
        if len(call.args) != 2:
            raise TileLangImportError("T.copy expects source and destination")
        attrs.update(_attrs_from_keywords(call, env))
        attrs.update(
            _split_k_copy_index_attrs(
                call.args[0],
                env,
                kernel_axis_vars=kernel_axis_vars,
            )
        )
        return KernelOp("copy", (_symbol_ref(call.args[0]), _symbol_ref(call.args[1])), attrs)
    if func_name == "T.atomic_add":
        if len(call.args) != 2:
            raise TileLangImportError("T.atomic_add expects destination and source")
        if call.keywords:
            raise TileLangImportError("T.atomic_add keyword arguments are not supported")
        return KernelOp("atomic_add", (_symbol_ref(call.args[0]), _symbol_ref(call.args[1])), attrs)
    if func_name == "T.gemm":
        if len(call.args) < 3:
            raise TileLangImportError("T.gemm expects A, B, and C operands")
        if len(call.args) > 5:
            raise TileLangImportError(
                "T.gemm importer accepts only A, B, C, optional transpose_A, "
                "and optional transpose_B positional operands"
            )
        attrs.update(_attrs_from_keywords(call, env))
        if "policy" in attrs:
            attrs["policy"] = _normalize_gemm_policy_metadata(attrs["policy"])
        if len(call.args) >= 4:
            _set_attr_once(attrs, "transpose_A", _eval_bool(call.args[3], env))
        if len(call.args) >= 5:
            _set_attr_once(attrs, "transpose_B", _eval_bool(call.args[4], env))
        return KernelOp(
            "gemm",
            (_symbol_ref(call.args[0]), _symbol_ref(call.args[1]), _symbol_ref(call.args[2])),
            attrs,
        )
    if func_name == "T.gemm_sp":
        if len(call.args) < 4:
            raise TileLangImportError("T.gemm_sp expects A_sparse, E, B, and C operands")
        if len(call.args) > 7:
            raise TileLangImportError(
                "T.gemm_sp importer accepts only A_sparse, E, B, C, optional "
                "transpose_A, optional transpose_E, and optional transpose_B "
                "positional operands"
            )
        attrs.update(_attrs_from_keywords(call, env))
        if "policy" in attrs:
            attrs["policy"] = _normalize_gemm_policy_metadata(attrs["policy"])
        if len(call.args) >= 5:
            _set_attr_once(attrs, "transpose_A", _eval_bool(call.args[4], env))
        if len(call.args) >= 6:
            _set_attr_once(attrs, "transpose_E", _eval_bool(call.args[5], env))
        if len(call.args) >= 7:
            _set_attr_once(attrs, "transpose_B", _eval_bool(call.args[6], env))
        for key in ("transpose_A", "transpose_E", "transpose_B"):
            if key in attrs and not isinstance(attrs[key], bool):
                raise TileLangImportError(f"T.gemm_sp {key} must be boolean")
        return KernelOp(
            "gemm_sp",
            (
                _symbol_ref(call.args[0]),
                _symbol_ref(call.args[1]),
                _symbol_ref(call.args[2]),
                _symbol_ref(call.args[3]),
            ),
            attrs,
        )
    if func_name in {"T.wgmma_gemm_sp", "T.tcgen05_gemm_sp"}:
        raise TileLangImportError(
            f"{func_name} is not supported by the Metal TileLang importer yet; "
            "CUDA/Hopper/Blackwell sparse GEMM intrinsics require target-specific "
            "semantics and cannot be accepted by the Metal importer"
        )
    if func_name == "T.use_swizzle":
        attrs.update(_attrs_from_keywords(call, env))
        if call.args:
            if len(call.args) > 1:
                raise TileLangImportError("T.use_swizzle accepts at most one panel_size positional argument")
            _set_attr_once(attrs, "panel_size", _eval_int(call.args[0], env))
        if "enable" not in attrs:
            attrs["enable"] = True
        if not isinstance(attrs.get("enable"), bool):
            raise TileLangImportError("T.use_swizzle enable must be boolean")
        if attrs["enable"] and "order" not in attrs:
            attrs["order"] = "row"
        order = attrs.get("order")
        if order is not None and order not in {"row", "col"}:
            raise TileLangImportError("T.use_swizzle order must be 'row' or 'col'")
        panel_size = attrs.get("panel_size")
        if panel_size is not None and (not isinstance(panel_size, int) or panel_size <= 0):
            raise TileLangImportError("T.use_swizzle panel_size must be a positive integer")
        return KernelOp("swizzle", (), attrs)
    if func_name and func_name.startswith("T."):
        raise TileLangImportError(f"TileLang construct {func_name} is not supported by this importer")
    return None


def _parse_body(
    stmts: list[ast.stmt],
    env: Mapping[str, Any],
    extra_attrs: Mapping[str, Any],
    *,
    kernel_axis_vars: tuple[str, ...],
) -> tuple[list[LocalBuffer], list[KernelOp]]:
    locals_: list[LocalBuffer] = []
    ops: list[KernelOp] = []
    for stmt in stmts:
        if isinstance(stmt, ast.Assign):
            local = _parse_alloc(stmt, env)
            if local is not None:
                locals_.append(local)
                continue
            if _inside_scheduled_thread_loop(extra_attrs):
                ops.append(_parse_scheduled_assignment(stmt, extra_attrs))
                continue
            raise TileLangImportError(f"unsupported assignment in TileLang kernel: {ast.dump(stmt)}")
        if isinstance(stmt, ast.Expr):
            if isinstance(stmt.value, ast.Call) and _attr_name(stmt.value.func) == "T.annotate_layout":
                op = _apply_layout_annotation(stmt.value, env, extra_attrs, locals_)
                if op is not None:
                    ops.append(op)
                continue
            op = _parse_expr_call(
                stmt,
                env,
                extra_attrs,
                kernel_axis_vars=kernel_axis_vars,
            )
            if op is not None:
                ops.append(op)
                continue
            if _inside_scheduled_thread_loop(extra_attrs):
                raise _scheduled_thread_loop_body_error(stmt)
            raise TileLangImportError(f"unsupported expression in TileLang kernel: {ast.dump(stmt)}")
        if isinstance(stmt, ast.For):
            loop_attrs = dict(extra_attrs)
            if _is_t_call(stmt.iter, "Pipelined"):
                if not stmt.iter.args:
                    raise TileLangImportError("T.Pipelined requires a loop extent")
                start, extent, step = _eval_loop_range(
                    stmt.iter,
                    env,
                    name="T.Pipelined",
                    allow_step=True,
                )
                loop_attrs["pipeline_extent"] = extent
                if start:
                    loop_attrs["pipeline_start"] = start
                if step != 1:
                    loop_attrs["pipeline_step"] = step
                num_stages = _kw(stmt.iter, "num_stages")
                if num_stages is not None:
                    loop_attrs["num_stages"] = _eval_expr(num_stages, env)
            elif _is_t_call(stmt.iter, "serial"):
                if stmt.iter.keywords:
                    raise TileLangImportError("T.serial keyword arguments are not supported")
                start, extent, step = _eval_loop_range(
                    stmt.iter,
                    env,
                    name="T.serial",
                    allow_step=True,
                )
                loop_attrs["serial_extent"] = extent
                if start:
                    loop_attrs["serial_start"] = start
                if step != 1:
                    loop_attrs["serial_step"] = step
            elif _is_t_call(stmt.iter, "Parallel"):
                if not stmt.iter.args:
                    raise TileLangImportError("T.Parallel requires at least one loop extent")
                parallel_annotations: dict[str, Any] | None = None
                for keyword in stmt.iter.keywords:
                    if keyword.arg == "coalesced_width":
                        loop_attrs["parallel_coalesced_width"] = _eval_int(keyword.value, env)
                    elif keyword.arg == "prefer_async":
                        prefer_async = _eval_bool(keyword.value, env)
                        if prefer_async:
                            raise TileLangImportError(
                                "T.Parallel prefer_async=True requests CUDA/PTX async-copy "
                                "lowering and is not supported for Metal"
                            )
                        loop_attrs["parallel_prefer_async"] = False
                    elif keyword.arg == "annotations":
                        parallel_annotations = _eval_metadata_dict(
                            keyword.value,
                            env,
                            name="T.Parallel annotations",
                        )
                    elif keyword.arg == "loop_layout":
                        raise TileLangImportError(
                            "T.Parallel loop_layout requires Fragment layout semantics "
                            "and is not supported by this Metal importer"
                        )
                    else:
                        raise TileLangImportError(
                            f"T.Parallel keyword {keyword.arg!r} is not supported"
                        )
                extents = [_eval_int(arg, env) for arg in stmt.iter.args]
                names = _for_target_names(stmt.target)
                if len(names) != len(extents):
                    raise TileLangImportError(
                        f"T.Parallel target arity {len(names)} does not match "
                        f"extent arity {len(extents)}"
                )
                loop_attrs["parallel_extents"] = extents
                loop_attrs["parallel_vars"] = names
                if parallel_annotations is not None:
                    loop_attrs["parallel_annotations"] = parallel_annotations
            elif _is_t_call(stmt.iter, "vectorized"):
                if not stmt.iter.args:
                    raise TileLangImportError("T.vectorized requires a loop extent")
                annotations_node: ast.AST | None = None
                for keyword in stmt.iter.keywords:
                    if keyword.arg != "annotations":
                        raise TileLangImportError(
                            "T.vectorized supports only annotations= keyword metadata"
                        )
                    annotations_node = keyword.value
                names = _for_target_names(stmt.target)
                if len(names) != 1:
                    raise TileLangImportError("T.vectorized target must be a single name")
                loop_attrs["vectorized_extent"] = _eval_zero_start_loop_extent(
                    stmt.iter,
                    env,
                    name="T.vectorized",
                )
                loop_attrs["vectorized_var"] = names[0]
                if annotations_node is not None:
                    loop_attrs["vectorized_annotations"] = _eval_metadata_dict(
                        annotations_node,
                        env,
                        name="T.vectorized annotations",
                    )
            else:
                raise TileLangImportError(
                    "only for-loops over T.Pipelined, T.serial, T.Parallel, or "
                    "T.vectorized are supported"
                )
            nested_locals, nested_ops = _parse_body(
                stmt.body,
                env,
                loop_attrs,
                kernel_axis_vars=kernel_axis_vars,
            )
            if any(op.op == "indexed_store" for op in nested_ops):
                if "parallel_extents" in loop_attrs:
                    extents = loop_attrs["parallel_extents"]
                    extent = math.prod(extents)
                    loop_kind = "T.Parallel"
                else:
                    extents = [loop_attrs["vectorized_extent"]]
                    extent = extents[0]
                    loop_kind = "T.vectorized"
                ops.append(
                    KernelOp(
                        "parallel",
                        (),
                        {
                            "extent": extent,
                            "loop_kind": loop_kind,
                            "loop_extents": list(extents),
                        },
                    )
                )
            locals_.extend(nested_locals)
            ops.extend(nested_ops)
            continue
        if _inside_scheduled_thread_loop(extra_attrs):
            raise _scheduled_thread_loop_body_error(stmt)
        raise TileLangImportError(f"unsupported statement in TileLang kernel: {ast.dump(stmt)}")
    return locals_, ops


def _parse_kernel_region(stmt: ast.With, env: Mapping[str, Any]) -> tuple[tuple[int, ...], int, list[LocalBuffer], list[KernelOp]]:
    if len(stmt.items) != 1 or not _is_t_call(stmt.items[0].context_expr, "Kernel"):
        raise TileLangImportError("expected a single with T.Kernel(...) region")
    call = stmt.items[0].context_expr
    grid = tuple(_eval_int(arg, env) for arg in call.args)
    axis_vars: tuple[str, ...] = ()
    optional_vars = stmt.items[0].optional_vars
    if optional_vars is not None:
        axis_vars = tuple(_for_target_names(optional_vars))
        if len(axis_vars) != len(grid):
            raise TileLangImportError(
                f"T.Kernel target arity {len(axis_vars)} does not match grid arity {len(grid)}"
            )
    threads_node = _kw(call, "threads")
    if threads_node is None:
        raise TileLangImportError("T.Kernel import requires an explicit threads= argument")
    threads = _eval_int(threads_node, env)
    locals_, ops = _parse_body(stmt.body, env, {}, kernel_axis_vars=axis_vars)
    return grid, threads, locals_, ops


def import_tilelang_source(
    source: str,
    *,
    outer_function: str | None = None,
    prim_func: str | None = None,
    constants: Mapping[str, Any] | None = None,
    module_name: str | None = None,
) -> KernelModule:
    """Import a strict TileLang Python-DSL subset into :class:`KernelModule`.

    ``constants`` supplies static values for shape/grid symbols such as ``M``,
    ``N``, ``K``, and block sizes. Defaults from ``outer_function`` are used
    when they are statically evaluable.
    """
    tree = ast.parse(source)
    outer = _find_outer_function(tree, outer_function)
    env = _merge_outer_defaults(outer, constants or {})
    search_root: ast.AST = outer if outer is not None else tree
    eager_style = False
    try:
        func = _find_prim_func(search_root, prim_func)
    except TileLangImportError:
        if outer is None or prim_func is not None or len(_kernel_regions(outer)) != 1:
            raise
        func = outer
        eager_style = True
    env = _merge_outer_static_aliases(outer, func, env)
    if eager_style:
        params = _parse_eager_tensor_declarations(func, env)
    else:
        params = tuple(_parse_tensor_param(arg, env) for arg in func.args.args)

    kernel_regions = _kernel_regions(func)
    if len(kernel_regions) != 1:
        raise TileLangImportError(
            f"TileLang prim_func {func.name!r} must contain exactly one T.Kernel region"
        )
    grid, threads, locals_, ops = _parse_kernel_region(kernel_regions[0], env)
    module = KernelModule(
        module_name or f"{func.name}_tilelang_import",
        funcs=(
            KernelFunc(
                name=func.name,
                params=params,
                locals=tuple(locals_),
                body=tuple(ops),
                grid=grid,
                threads=threads,
            ),
        ),
    )
    return _attach_source_subset_claim(validate_kernel(module))


def import_tilelang_file(path: str | Path, **kwargs: Any) -> KernelModule:
    """Read *path* and import it with :func:`import_tilelang_source`."""
    source = Path(path).read_text(encoding="utf-8")
    return import_tilelang_source(source, **kwargs)


__all__ = [
    "TileLangImportError",
    "TILELANG_PACKAGE_CPYTHON_COMPAT_CLAIM",
    "TILELANG_SOURCE_SUBSET_CLAIM",
    "TILELANG_WGMMA_LAYOUT_REFERENCE_COMMIT",
    "TILELANG_WGMMA_LAYOUT_REFERENCE_PATH",
    "TILELANG_WGMMA_LAYOUT_REFERENCE_SHA256",
    "tilelang_source_import_claim",
    "tilelang_source_import_claim_of",
    "assert_not_native_import_tilelang_claim",
    "import_tilelang_source",
    "import_tilelang_file",
]
