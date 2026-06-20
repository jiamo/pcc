from __future__ import annotations

import pytest

from pcc.kernel_ir.tilelang_dynamic_shape import (
    UINT32_MAX,
    UINT64_MAX,
    TileLangDynamicShapeContract,
    TileLangDynamicShapeError,
)


DYNAMIC_VECTOR_FILL = """
import tilelang.language as T

def dynamic_fill(N, dtype, threads):
    @T.prim_func
    def fill_kernel(C: T.Tensor((N,), dtype)):
        with T.Kernel(T.ceildiv(N, threads), threads=threads) as bx:
            T.fill(C, 1.25)
    return fill_kernel
"""


def _contract(source: str = DYNAMIC_VECTOR_FILL, **overrides: object):
    kwargs: dict[str, object] = {
        "source": source,
        "outer_function": "dynamic_fill",
        "prim_func": "fill_kernel",
        "symbol": "N",
        "min_value": 1,
        "max_value": 1024,
        "element_nbytes": 4,
        "max_buffer_nbytes": 4096,
        "base_constants": {"dtype": "float32", "threads": 32},
    }
    kwargs.update(overrides)
    return TileLangDynamicShapeContract(**kwargs)


def test_bounded_dynamic_dimension_specializes_to_static_kernel_ir():
    contract = _contract()
    specialization = contract.specialize(65)

    func = specialization.module.funcs[0]
    assert func.params[0].shape == (65,)
    assert func.grid == (3,)
    assert func.threads == 32
    assert specialization.required_buffer_nbytes == 260
    assert specialization.grid_extent == 3
    assert specialization.cache_key == contract.specialization_key(65)
    assert "no runtime execution" in specialization.claim_mode


def test_specialization_cache_identity_is_stable_and_complete():
    first = _contract()
    same = _contract()
    changed_source = _contract(DYNAMIC_VECTOR_FILL.replace("1.25", "2.5"))
    changed_bound = _contract(max_value=2048, max_buffer_nbytes=8192)

    assert first.contract_id == same.contract_id
    assert first.specialization_key(7) == same.specialization_key(7)
    assert first.specialization_key(7) != first.specialization_key(8)
    assert first.specialization_key(7) != changed_source.specialization_key(7)
    assert first.specialization_key(7) != changed_bound.specialization_key(7)


@pytest.mark.parametrize("value", [True, 0, 1025, 1.5])
def test_specialization_rejects_noninteger_or_out_of_bound_values(value: object):
    with pytest.raises(TileLangDynamicShapeError):
        _contract().specialize(value)  # type: ignore[arg-type]


def test_specialization_rejects_buffer_limit_before_import():
    contract = _contract(max_value=128, max_buffer_nbytes=128)
    with pytest.raises(TileLangDynamicShapeError, match="buffer bytes"):
        contract.specialize(33)


def test_specialization_rejects_uint64_byte_size_overflow():
    contract = _contract(
        max_value=UINT64_MAX,
        element_nbytes=8,
        max_buffer_nbytes=UINT64_MAX,
    )
    with pytest.raises(TileLangDynamicShapeError, match="overflows uint64"):
        contract.specialize(UINT64_MAX)


def test_specialization_rejects_uint32_grid_overflow():
    value = UINT32_MAX + 1
    contract = _contract(
        max_value=value,
        element_nbytes=1,
        max_buffer_nbytes=value,
        base_constants={"dtype": "float32", "threads": 1},
    )
    with pytest.raises(TileLangDynamicShapeError, match="launch grid.*exceeds uint32"):
        contract.specialize(value)


@pytest.mark.parametrize(
    "source",
    [
        DYNAMIC_VECTOR_FILL.replace("(N,), dtype", "(N * N,), dtype"),
        DYNAMIC_VECTOR_FILL.replace("T.ceildiv(N, threads)", "T.ceildiv(N + 1, threads)"),
        DYNAMIC_VECTOR_FILL.replace("T.fill(C, 1.25)", "T.fill(C, N)"),
    ],
)
def test_contract_fails_closed_on_unsupported_symbolic_expressions(source: str):
    with pytest.raises(TileLangDynamicShapeError, match="unsupported dynamic expression"):
        _contract(source)


def test_contract_requires_exactly_one_shape_and_grid_use():
    source = DYNAMIC_VECTOR_FILL.replace(
        "C: T.Tensor((N,), dtype)",
        "C: T.Tensor((7,), dtype)",
    )
    with pytest.raises(TileLangDynamicShapeError, match="one Tensor.*one T.ceildiv"):
        _contract(source)
