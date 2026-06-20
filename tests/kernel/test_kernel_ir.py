"""K-P0-TVM-KERNEL-IR — kernel-only IR boundary + escape rejection + golden.

These tests assert REAL invariants: the validator actually RAISES on host
escapes (list/dict/PyObject/weakref/finalizer), and the golden dump round-trips.
"""

import json
import weakref

import pytest

from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelIRError,
    KernelModule,
    KernelOp,
    Layout,
    LocalBuffer,
    MemoryScope,
    ScalarParam,
    ScalarType,
    dump_kernel,
    validate_kernel,
)


def _good_module():
    func = KernelFunc(
        name="saxpy",
        params=(
            ScalarParam("alpha", ScalarType.F32),
            BufferParam("x", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            BufferParam("y", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
        ),
        body=(
            KernelOp("parallel", ("x", "y"), {"extent": 256}),
            KernelOp("fill", ("y",), {"value": 0}),
            KernelOp("copy", ("x", "y")),
        ),
        grid=(1,),
        threads=256,
    )
    return KernelModule("saxpy_mod", funcs=(func,))


def test_good_module_validates():
    m = _good_module()
    assert validate_kernel(m) is m


def test_reject_list_param():
    func = KernelFunc(name="k", params=(["not", "a", "scalar"],))
    m = KernelModule("m", funcs=(func,))
    with pytest.raises(KernelIRError, match="host escape"):
        validate_kernel(m)


def test_reject_dict_param():
    func = KernelFunc(name="k", params=({"a": 1},))
    m = KernelModule("m", funcs=(func,))
    with pytest.raises(KernelIRError) as ei:
        validate_kernel(m)
    assert "dict" in str(ei.value)


def test_reject_weakref_param():
    class Holder:
        pass

    obj = Holder()
    ref = weakref.ref(obj)
    func = KernelFunc(name="k", params=(ref,))
    m = KernelModule("m", funcs=(func,))
    with pytest.raises(KernelIRError):
        validate_kernel(m)


def test_reject_finalizer_bearing_object():
    class HasDel:
        def __del__(self):  # finalizer => host object, must never cross frontier
            pass

    func = KernelFunc(name="k", params=(HasDel(),))
    m = KernelModule("m", funcs=(func,))
    with pytest.raises(KernelIRError, match="finalizer|host escape"):
        validate_kernel(m)


def test_reject_arbitrary_pyobject():
    class Arbitrary:
        pass

    func = KernelFunc(name="k", params=(Arbitrary(),))
    m = KernelModule("m", funcs=(func,))
    with pytest.raises(KernelIRError):
        validate_kernel(m)


def test_reject_unknown_op():
    func = KernelFunc(
        name="k",
        params=(ScalarParam("a", ScalarType.I32),),
        body=(KernelOp("launch_cuda_kernel", ("a",)),),
    )
    m = KernelModule("m", funcs=(func,))
    with pytest.raises(KernelIRError, match="accepted tile primitive"):
        validate_kernel(m)


def test_reject_live_object_op_arg():
    # An op arg must be a name reference, never a live object.
    func = KernelFunc(
        name="k",
        params=(BufferParam("x", ScalarType.F32, rank=1),),
        body=(KernelOp("copy", (object(),)),),  # type: ignore[arg-type]
    )
    m = KernelModule("m", funcs=(func,))
    with pytest.raises(KernelIRError, match="name reference"):
        validate_kernel(m)


def test_reject_empty_module():
    with pytest.raises(KernelIRError, match="no kernel functions"):
        validate_kernel(KernelModule("empty"))


def test_reject_duplicate_param_name():
    func = KernelFunc(
        name="k",
        params=(
            ScalarParam("a", ScalarType.I32),
            ScalarParam("a", ScalarType.F32),
        ),
    )
    m = KernelModule("m", funcs=(func,))
    with pytest.raises(KernelIRError, match="duplicate"):
        validate_kernel(m)


def test_device_local_buffer_validates_and_dumps():
    func = KernelFunc(
        name="scratch",
        params=(
            BufferParam("src", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            BufferParam("dst", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
        ),
        locals=(
            LocalBuffer("tile", ScalarType.F32, shape=(16,), scope=MemoryScope.SHARED),
        ),
        body=(
            KernelOp("copy", ("src", "tile")),
            KernelOp("barrier", ()),
            KernelOp("copy", ("tile", "dst")),
        ),
    )
    module = KernelModule("scratch_mod", funcs=(func,))

    validate_kernel(module)
    parsed = json.loads(dump_kernel(module))
    local = parsed["funcs"][0]["locals"][0]
    assert local == {
        "kind": "local_buffer",
        "name": "tile",
        "dtype": "f32",
        "rank": 1,
        "shape": [16],
        "scope": "shared",
        "layout": "row_major",
    }


def test_narrow_integer_metadata_buffers_validate_and_dump():
    func = KernelFunc(
        name="metadata",
        params=(
            BufferParam("meta", ScalarType.I16, rank=2, shape=(2, 4), scope=MemoryScope.GLOBAL),
        ),
        locals=(
            LocalBuffer("selectors", ScalarType.U8, shape=(8,), scope=MemoryScope.LOCAL),
        ),
        body=(KernelOp("copy", ("meta", "selectors")),),
        grid=(1,),
        threads=8,
    )
    module = KernelModule("metadata_mod", funcs=(func,))

    validate_kernel(module)
    parsed = json.loads(dump_kernel(module))
    fn = parsed["funcs"][0]
    assert fn["params"][0]["dtype"] == "i16"
    assert fn["locals"][0]["dtype"] == "u8"


def test_reject_global_scope_local_buffer():
    func = KernelFunc(
        name="k",
        locals=(
            LocalBuffer("bad", ScalarType.F32, shape=(16,), scope=MemoryScope.GLOBAL),
        ),
    )
    with pytest.raises(KernelIRError, match="local buffers cannot use global"):
        validate_kernel(KernelModule("m", funcs=(func,)))


def test_reject_unknown_symbol_reference():
    func = KernelFunc(
        name="k",
        params=(BufferParam("x", ScalarType.F32, rank=1),),
        body=(KernelOp("copy", ("x", "missing")),),
    )
    with pytest.raises(KernelIRError, match="unknown symbol"):
        validate_kernel(KernelModule("m", funcs=(func,)))


def test_reject_duplicate_param_local_name():
    func = KernelFunc(
        name="k",
        params=(BufferParam("x", ScalarType.F32, rank=1),),
        locals=(
            LocalBuffer("x", ScalarType.F32, shape=(16,), scope=MemoryScope.SHARED),
        ),
    )
    with pytest.raises(KernelIRError, match="duplicate symbol"):
        validate_kernel(KernelModule("m", funcs=(func,)))


def test_structured_indexed_ops_validate_and_reject_hidden_attr_escape():
    params = (
        BufferParam("out", ScalarType.F32, rank=1),
        ScalarParam("n", ScalarType.U32),
    )
    good = KernelModule(
        "m",
        funcs=(
            KernelFunc(
                name="k",
                params=params,
                body=(
                    KernelOp(
                        "scalar_assign",
                        (),
                        {
                            "target": "i",
                            "dtype": "u32",
                            "declare": True,
                            "expr": {"kind": "thread_id_x"},
                        },
                    ),
                    KernelOp(
                        "if_begin",
                        ("i", "n"),
                        {
                            "condition": {
                                "kind": "compare",
                                "op": "lt",
                                "left": {"kind": "name", "name": "i"},
                                "right": {"kind": "name", "name": "n"},
                            }
                        },
                    ),
                    KernelOp(
                        "indexed_store",
                        ("out", "i"),
                        {
                            "index": {"kind": "name", "name": "i"},
                            "value": {"kind": "literal", "value": 1.0},
                        },
                    ),
                    KernelOp("if_end"),
                ),
            ),
        ),
    )
    assert validate_kernel(good) is good

    escaped = KernelModule(
        "m",
        funcs=(
            KernelFunc(
                name="k",
                params=params,
                body=(
                    KernelOp(
                        "scalar_assign",
                        (),
                        {
                            "target": "i",
                            "dtype": "u32",
                            "declare": True,
                            "expr": {"kind": "thread_id_x"},
                            "hidden_host_object": object(),
                        },
                    ),
                ),
            ),
        ),
    )
    with pytest.raises(KernelIRError, match="unexpected attrs"):
        validate_kernel(escaped)


def test_golden_dump_roundtrips():
    m = _good_module()
    text = dump_kernel(m)
    parsed = json.loads(text)
    # Golden shape assertions (stable, deterministic).
    assert parsed["module"] == "saxpy_mod"
    assert parsed["kernel_ir_version"] == 1
    assert len(parsed["funcs"]) == 1
    fn = parsed["funcs"][0]
    assert fn["name"] == "saxpy"
    assert fn["threads"] == 256
    kinds = [p["kind"] for p in fn["params"]]
    assert kinds == ["scalar", "buffer", "buffer"]
    ops = [op["op"] for op in fn["body"]]
    assert ops == ["parallel", "fill", "copy"]
    # Deterministic: two dumps are byte-identical.
    assert dump_kernel(m) == text
