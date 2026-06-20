"""K-P0-TVM-HMM-FENCE — buffer handles, packed args, deferred free.

CPU-only state-machine tests. Asserts the REAL invariants: a buffer's release
is DELAYED until its fence completes, and the device launcher ABI never accepts
a GC-managed PyObject.
"""

import pytest

from pcc.kernel_ir.hmm_fence import (
    BufferState,
    HmmFenceError,
    PccBufferHandle,
    PccDeferredFreeQueue,
    PccFenceToken,
    PccPackedArgs,
)


def test_fence_delays_free_until_completion():
    buf = PccBufferHandle(nbytes=4096, dtype="f32", device="cpu")
    fence = PccFenceToken()
    q = PccDeferredFreeQueue()

    q.schedule_free(buf, fence)
    assert buf.state is BufferState.PENDING_FREE

    # Fence not yet complete: reclaim frees NOTHING.
    assert q.reclaim() == []
    assert buf.state is BufferState.PENDING_FREE
    assert q.pending_count == 1

    # Complete the fence: now the buffer may be reclaimed.
    fence.complete()
    reclaimed = q.reclaim()
    assert reclaimed == [buf.handle_id]
    assert buf.state is BufferState.FREED
    assert q.pending_count == 0


def test_multiple_buffers_freed_per_fence():
    q = PccDeferredFreeQueue()
    f1 = PccFenceToken()
    f2 = PccFenceToken()
    b1 = PccBufferHandle(nbytes=16, dtype="i32")
    b2 = PccBufferHandle(nbytes=16, dtype="i32")
    b3 = PccBufferHandle(nbytes=16, dtype="i32")

    q.schedule_free(b1, f1)
    q.schedule_free(b2, f1)
    q.schedule_free(b3, f2)

    f1.complete()
    reclaimed = set(q.reclaim())
    assert reclaimed == {b1.handle_id, b2.handle_id}
    assert b3.state is BufferState.PENDING_FREE  # f2 still in flight

    f2.complete()
    assert q.reclaim() == [b3.handle_id]


def test_double_free_rejected():
    buf = PccBufferHandle(nbytes=8, dtype="i64")
    fence = PccFenceToken()
    q = PccDeferredFreeQueue()
    q.schedule_free(buf, fence)
    fence.complete()
    q.reclaim()
    with pytest.raises(HmmFenceError, match="already freed"):
        q.schedule_free(buf, PccFenceToken())


def test_packed_args_reject_pyobject_scalar():
    args = PccPackedArgs(launch_device="cpu")
    args.add_scalar("f32", 3.14)  # POD OK
    args.add_scalar("i32", [1, 2, 3])  # GC-managed list => must raise
    with pytest.raises(HmmFenceError, match="PyObject"):
        args.validate()


def test_packed_args_reject_nonpod_dtype():
    args = PccPackedArgs()
    args.add_scalar("object", 1)
    with pytest.raises(HmmFenceError, match="not POD"):
        args.validate()


def test_packed_args_reject_integer_scalar_out_of_dtype_range():
    args = PccPackedArgs()
    args.add_scalar("i64", 2**100)
    with pytest.raises(HmmFenceError, match="out of range"):
        args.validate()


def test_packed_args_reject_unsigned_scalar_negative_value():
    args = PccPackedArgs()
    args.add_scalar("u64", -1)
    with pytest.raises(HmmFenceError, match="out of range"):
        args.validate()


def test_packed_args_accept_unsigned_scalar_boundary_value():
    args = PccPackedArgs()
    args.add_scalar("u64", (1 << 64) - 1)
    assert args.validate() is args


def test_packed_args_reject_raw_object_buffer():
    args = PccPackedArgs()
    args.buffers.append(object())  # type: ignore[arg-type]
    with pytest.raises(HmmFenceError, match="not a PccBufferHandle"):
        args.validate()


def test_packed_args_reject_cross_device_buffer():
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=32, dtype="f16", device="cpu"))
    with pytest.raises(HmmFenceError, match="may only be used with the device"):
        args.validate()


def test_packed_args_valid_roundtrip():
    args = PccPackedArgs(launch_device="cpu")
    args.add_scalar("i32", 7)
    args.add_scalar("f32", 1.5)
    args.add_buffer(PccBufferHandle(nbytes=64, dtype="f32", device="cpu"))
    d = args.to_dict()
    assert d["launch_device"] == "cpu"
    assert [s["value"] for s in d["scalars"]] == [7, 1.5]
    # Buffer is a DLPack-shaped POD descriptor, not a PyObject.
    assert set(d["buffers"][0]) == {"handle_id", "nbytes", "dtype", "device"}


def test_buffer_handle_is_not_pyobject_in_descriptor():
    # The device IR sees only the descriptor, which carries no object reference.
    buf = PccBufferHandle(nbytes=128, dtype="f32")
    desc = buf.dlpack_descriptor()
    assert "pyobject" not in desc
    assert isinstance(desc["handle_id"], int)
