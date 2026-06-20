"""PCC TVM oracle — one bounded TVM reference seam.

Row K-P0-TVM-CXX-ORACLE. We do NOT translate the whole TVM stack. We pick ONE
bounded, well-documented seam — the *shape* of a TIR ``PrimFunc`` object as it
is serialized — and provide a golden the pcc Kernel IR output can be compared
against.

The chosen seam: a TIR ``PrimFunc`` serializes to a small, stable JSON-ish
object with ``params`` (each a ``Var`` with a name + a dtype), ``buffer_map``
(param Var -> Buffer with dtype/shape/scope), and a ``body`` of statements. We
model just enough of that shape to (a) project a pcc ``KernelFunc`` into it and
(b) assert the projection is stable (round-trips) and matches a hand-written
golden. This gives a reference oracle without a TVM dependency.

This is a *comparison oracle only* — it does not import TVM, does not run any
TVM pass, and does not claim TIR equivalence beyond the serialized object shape.

Importable standalone::

    from pcc.kernel_ir.tvm_oracle import project_to_tir_shape, tir_shape_dump
"""

from __future__ import annotations

from typing import Any

from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    LocalBuffer,
    ScalarParam,
    validate_kernel,
)

# TVM TIR dtype spellings for our POD scalar tags. This is the bounded mapping
# that anchors the oracle to the real TVM TIR dtype vocabulary.
_TIR_DTYPE = {
    "bool": "bool",
    "i32": "int32",
    "i64": "int64",
    "u32": "uint32",
    "u64": "uint64",
    "f16": "float16",
    "f32": "float32",
    "f64": "float64",
}


class TvmOracleError(ValueError):
    """A kernel func could not be projected to the TIR object shape."""


def _tir_dtype(tag: str) -> str:
    d = _TIR_DTYPE.get(tag)
    if d is None:
        raise TvmOracleError(f"no TIR dtype for scalar tag {tag!r}")
    return d


def _project_func(func: KernelFunc) -> dict[str, Any]:
    """Project one pcc KernelFunc into the TIR PrimFunc object shape."""
    params: list[dict[str, Any]] = []
    buffer_map: list[dict[str, Any]] = []
    alloc_buffers: list[dict[str, Any]] = []

    for p in func.params:
        if isinstance(p, ScalarParam):
            # A scalar param is a TIR Var of a scalar dtype (a "handle"-free Var).
            params.append({"var": p.name, "dtype": _tir_dtype(p.dtype.value)})
        elif isinstance(p, BufferParam):
            # A buffer param is a TIR Var of dtype "handle" bound in buffer_map
            # to a Buffer with element dtype/rank/scope.
            params.append({"var": p.name, "dtype": "handle"})
            buffer_map.append(
                {
                    "var": p.name,
                    "buffer": {
                        "name": p.name,
                        "dtype": _tir_dtype(p.dtype.value),
                        "ndim": p.rank,
                        "scope": p.scope.value,
                    },
                }
            )
            if p.shape is not None:
                buffer_map[-1]["buffer"]["shape"] = list(p.shape)
        else:  # pragma: no cover - validate_kernel already rejects these
            raise TvmOracleError(f"non-projectable param {p!r}")

    for local in func.locals:
        if not isinstance(local, LocalBuffer):  # pragma: no cover
            raise TvmOracleError(f"non-projectable local {local!r}")
        alloc_buffers.append(
            {
                "name": local.name,
                "dtype": _tir_dtype(local.dtype.value),
                "shape": list(local.shape),
                "scope": local.scope.value,
                "layout": local.layout.value,
            }
        )

    body = []
    for op in func.body:
        record = {"stmt": op.op, "args": list(op.args)}
        if op.attrs:
            record["attrs"] = dict(op.attrs)
        body.append(record)
    projected = {
        "prim_func": func.name,
        "params": params,
        "buffer_map": buffer_map,
        "body": body,
    }
    if alloc_buffers:
        projected["alloc_buffers"] = alloc_buffers
    return projected


def project_to_tir_shape(module: KernelModule) -> dict[str, Any]:
    """Project a validated pcc kernel module into the TVM TIR object shape."""
    validate_kernel(module)
    return {
        "ir_module": module.name,
        "tir_object_shape_version": 1,
        "functions": [_project_func(f) for f in module.funcs],
    }


def tir_shape_dump(module: KernelModule) -> str:
    """Deterministic golden dump of the TIR-shape projection."""
    import json

    return json.dumps(project_to_tir_shape(module), indent=2, sort_keys=True)


def matches_oracle(module: KernelModule, oracle: dict[str, Any]) -> bool:
    """True if the module's TIR-shape projection equals *oracle* exactly."""
    return project_to_tir_shape(module) == oracle


__all__ = [
    "TvmOracleError",
    "project_to_tir_shape",
    "tir_shape_dump",
    "matches_oracle",
]
