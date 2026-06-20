"""DLPack ownership and classic ``DLManagedTensor`` ABI for Metal tensors.

The Python capsule contains a real C-compatible ``DLManagedTensor`` pointer;
the kernel-facing descriptor remains pcc-owned POD metadata. The bridge
preserves these ownership rules:

* a DLPack managed tensor can be consumed only once,
* aliases keep the native buffer alive until the last deleter runs,
* deleters schedule release behind a ``PccFenceToken``,
* the native ``id<MTLBuffer>`` is released only after that fence completes.

The Kernel IR still sees ``PccBufferHandle`` descriptors, never a PyObject.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from typing import Any

from pcc.kernel_ir.hmm_fence import (
    PccBufferHandle,
    PccDeferredFreeQueue,
    PccFenceToken,
)
from pcc.kernel_ir.metal_buffer import (
    MetalNativeBufferAllocation,
    MetalNativeBufferAllocationSet,
)
from pcc.kernel_ir.metal_launch import MetalLaunchPlan, MetalRuntimeArg

STATUS_METAL_DLPACK_TENSOR_EXPORTED = "metal_dlpack_tensor_exported"
STATUS_METAL_DLPACK_TENSOR_IMPORTED = "metal_dlpack_tensor_imported"
STATUS_METAL_DLPACK_ALIAS_DROPPED = "metal_dlpack_alias_dropped"
STATUS_METAL_DLPACK_RELEASE_DEFERRED = "metal_dlpack_release_deferred"
STATUS_METAL_DLPACK_RECLAIM_PENDING = "metal_dlpack_reclaim_pending"
STATUS_METAL_DLPACK_NATIVE_RELEASED = "metal_dlpack_native_released"
STATUS_METAL_DLPACK_CAPSULE_EXPORTED = "metal_dlpack_capsule_exported"
STATUS_METAL_DLPACK_CAPSULE_IMPORTED = "metal_dlpack_capsule_imported"

DLPACK_CAPSULE_NAME = "dltensor"
USED_DLPACK_CAPSULE_NAME = "used_dltensor"
_DLPACK_CAPSULE_NAME_BYTES = DLPACK_CAPSULE_NAME.encode("utf-8")
_USED_DLPACK_CAPSULE_NAME_BYTES = USED_DLPACK_CAPSULE_NAME.encode("utf-8")

_DLPACK_DTYPES = {
    "bool": {"code": "bool", "bits": 1, "lanes": 1},
    "i8": {"code": "int", "bits": 8, "lanes": 1},
    "u8": {"code": "uint", "bits": 8, "lanes": 1},
    "i16": {"code": "int", "bits": 16, "lanes": 1},
    "u16": {"code": "uint", "bits": 16, "lanes": 1},
    "i32": {"code": "int", "bits": 32, "lanes": 1},
    "u32": {"code": "uint", "bits": 32, "lanes": 1},
    "i64": {"code": "int", "bits": 64, "lanes": 1},
    "u64": {"code": "uint", "bits": 64, "lanes": 1},
    "f16": {"code": "float", "bits": 16, "lanes": 1},
    "f32": {"code": "float", "bits": 32, "lanes": 1},
    "f64": {"code": "float", "bits": 64, "lanes": 1},
}

_DLPACK_DTYPE_CODES = {
    "int": 0,
    "uint": 1,
    "float": 2,
    "bool": 6,
}
_DLPACK_DEVICE_TYPE_METAL = 8


class DLDevice(ctypes.Structure):
    _fields_ = [
        ("device_type", ctypes.c_int),
        ("device_id", ctypes.c_int32),
    ]


class DLDataType(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint8),
        ("bits", ctypes.c_uint8),
        ("lanes", ctypes.c_uint16),
    ]


class DLTensor(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.c_void_p),
        ("device", DLDevice),
        ("ndim", ctypes.c_int32),
        ("dtype", DLDataType),
        ("shape", ctypes.POINTER(ctypes.c_int64)),
        ("strides", ctypes.POINTER(ctypes.c_int64)),
        ("byte_offset", ctypes.c_uint64),
    ]


class DLManagedTensor(ctypes.Structure):
    pass


DLManagedTensorDeleter = ctypes.CFUNCTYPE(
    None, ctypes.POINTER(DLManagedTensor)
)
DLManagedTensor._fields_ = [
    ("dl_tensor", DLTensor),
    ("manager_ctx", ctypes.c_void_p),
    ("deleter", DLManagedTensorDeleter),
]


class MetalDlpackOwnershipError(ValueError):
    """A Metal DLPack ownership invariant was violated."""


@dataclass(frozen=True)
class MetalDlpackTensorDescriptor:
    """DLPack-shaped tensor metadata for one native Metal buffer."""

    name: str
    dtype: str
    shape: tuple[int, ...]
    device: str
    handle_id: int
    native_mtlbuffer_ptr: int
    nbytes: int
    dl_device_type: str = "kDLMetal"
    dl_device_id: int = 0
    byte_offset: int = 0
    strides: tuple[int, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "dl_dtype": dict(_dlpack_dtype(self.dtype)),
            "shape": list(self.shape),
            "strides": list(self.strides) if self.strides is not None else None,
            "byte_offset": self.byte_offset,
            "device": self.device,
            "dl_device_type": self.dl_device_type,
            "dl_device_id": self.dl_device_id,
            "handle_id": self.handle_id,
            "native_mtlbuffer_ptr": self.native_mtlbuffer_ptr,
            "nbytes": self.nbytes,
            "descriptor_contains_pyobject": False,
        }


@dataclass(frozen=True)
class ImportedMetalDlpackTensor:
    """A consumed DLPack tensor re-entered as pcc launcher metadata."""

    status: str
    descriptor: MetalDlpackTensorDescriptor
    buffer_handle: PccBufferHandle
    native_mtlbuffer_ptr: int
    runtime_launch_executed: bool = False
    whole_program_gpu: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "descriptor": self.descriptor.to_dict(),
            "buffer_handle": self.buffer_handle.dlpack_descriptor(),
            "native_mtlbuffer_ptr": self.native_mtlbuffer_ptr,
            "runtime_launch_executed": self.runtime_launch_executed,
            "whole_program_gpu": self.whole_program_gpu,
        }


@dataclass(frozen=True)
class MetalDlpackReleaseResult:
    """Result of dropping one exported DLPack alias."""

    status: str
    handle_id: int
    active_aliases: int
    pending_count: int
    fence_completed: bool
    native_release_executed: bool = False
    runtime_launch_executed: bool = False
    whole_program_gpu: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "handle_id": self.handle_id,
            "active_aliases": self.active_aliases,
            "pending_count": self.pending_count,
            "fence_completed": self.fence_completed,
            "native_release_executed": self.native_release_executed,
            "runtime_launch_executed": self.runtime_launch_executed,
            "whole_program_gpu": self.whole_program_gpu,
        }


@dataclass(frozen=True)
class MetalDlpackReclaimResult:
    """Result of reclaiming fence-completed DLPack tensor releases."""

    status: str
    reclaimed_handle_ids: tuple[int, ...]
    pending_count: int
    native_release_executed: bool
    runtime_launch_executed: bool = False
    whole_program_gpu: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reclaimed_handle_ids": list(self.reclaimed_handle_ids),
            "pending_count": self.pending_count,
            "native_release_executed": self.native_release_executed,
            "runtime_launch_executed": self.runtime_launch_executed,
            "whole_program_gpu": self.whole_program_gpu,
        }


@dataclass
class MetalDlpackManagedTensor:
    """One DLPack-style exported alias.

    ``consume`` models the one-shot DLPack capsule consumption rule. ``deleter``
    models the consumer's eventual deleter call; it must receive the fence that
    protects in-flight GPU use.
    """

    descriptor: MetalDlpackTensorDescriptor
    _owner: MetalDlpackTensorOwner = field(repr=False)
    _consumed: bool = field(default=False, init=False, repr=False)
    _deleter_called: bool = field(default=False, init=False, repr=False)

    @property
    def consumed(self) -> bool:
        return self._consumed

    @property
    def deleter_called(self) -> bool:
        return self._deleter_called

    def consume(self) -> MetalDlpackTensorDescriptor:
        if self._consumed:
            raise MetalDlpackOwnershipError(
                f"DLPack tensor for {self.descriptor.name!r} was already consumed"
            )
        self._consumed = True
        return self.descriptor

    def deleter(self, fence: PccFenceToken) -> MetalDlpackReleaseResult:
        if self._deleter_called:
            raise MetalDlpackOwnershipError(
                f"DLPack deleter for {self.descriptor.name!r} was already called"
            )
        result = self._owner.release_alias(self.descriptor.handle_id, fence)
        self._deleter_called = True
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": STATUS_METAL_DLPACK_TENSOR_EXPORTED,
            "descriptor": self.descriptor.to_dict(),
            "consumed": self._consumed,
            "deleter_called": self._deleter_called,
            "runtime_launch_executed": False,
            "whole_program_gpu": False,
        }


@dataclass(frozen=True)
class MetalDlpackCapsuleExport:
    """A host PyCapsule carrying a classic C ``DLManagedTensor`` pointer."""

    status: str
    capsule: object
    descriptor: MetalDlpackTensorDescriptor
    pointer_id: int
    abi: str = "DLManagedTensor"
    stream: int | None = None
    runtime_launch_executed: bool = False
    whole_program_gpu: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "capsule_name": pycapsule_name(self.capsule),
            "descriptor": self.descriptor.to_dict(),
            "pointer_id": self.pointer_id,
            "abi": self.abi,
            "stream": self.stream,
            "runtime_launch_executed": self.runtime_launch_executed,
            "whole_program_gpu": self.whole_program_gpu,
        }


@dataclass
class MetalDlpackProtocolExport:
    """One-shot Python DLPack protocol producer for external frameworks.

    The current contract is classic ``DLManagedTensor`` on kDLMetal's default
    stream. A caller-supplied fence guards the eventual framework deleter; the
    native Metal allocation is not reclaimed until that fence completes.
    """

    tensor: MetalDlpackManagedTensor
    release_fence: PccFenceToken = field(repr=False)
    _capsule_export: MetalDlpackCapsuleExport | None = field(
        default=None, init=False, repr=False
    )

    def __dlpack_device__(self) -> tuple[int, int]:
        return (_DLPACK_DEVICE_TYPE_METAL, self.tensor.descriptor.dl_device_id)

    def __dlpack__(
        self,
        *,
        stream: int | None = None,
        max_version: tuple[int, int] | None = None,
        dl_device: tuple[int, int] | None = None,
        copy: bool | None = None,
    ) -> object:
        if self._capsule_export is not None:
            raise MetalDlpackOwnershipError(
                "DLPack protocol export was already consumed"
            )
        if max_version is not None:
            raise MetalDlpackOwnershipError(
                "versioned DLPack export is not implemented; classic "
                "DLManagedTensor is required"
            )
        expected_device = self.__dlpack_device__()
        if dl_device is not None and tuple(dl_device) != expected_device:
            raise MetalDlpackOwnershipError(
                f"DLPack consumer requested device {dl_device!r}, expected "
                f"{expected_device!r}"
            )
        if copy is True:
            raise MetalDlpackOwnershipError(
                "pcc DLPack export cannot satisfy a framework-requested copy"
            )
        exported = export_metal_dlpack_py_capsule(
            self.tensor,
            stream=stream,
            release_fence=self.release_fence,
        )
        self._capsule_export = exported
        return exported.capsule

    def to_dict(self) -> dict[str, Any]:
        return {
            "framework_protocol": "python-dlpack",
            "abi": "DLManagedTensor",
            "device": "kDLMetal",
            "default_stream_only": True,
            "consumed": self._capsule_export is not None,
            "descriptor": self.tensor.descriptor.to_dict(),
            "whole_program_gpu": False,
        }


@dataclass
class ImportedMetalDlpackCapsule:
    """A consumed external ``DLManagedTensor`` owned until its deleter runs."""

    status: str
    imported: ImportedMetalDlpackTensor
    _dlmanaged_pointer: ctypes.POINTER(DLManagedTensor) = field(repr=False)
    _pcc_managed_tensor: MetalDlpackManagedTensor | None = field(
        default=None, repr=False
    )
    _release_fence: PccFenceToken | None = field(default=None, init=False, repr=False)
    _deleter_called: bool = field(default=False, init=False, repr=False)
    runtime_launch_executed: bool = False
    whole_program_gpu: bool = False

    def deleter(self, fence: PccFenceToken) -> MetalDlpackReleaseResult:
        if not isinstance(fence, PccFenceToken):
            raise MetalDlpackOwnershipError("DLPack deleter requires a PccFenceToken")
        if self._deleter_called or self._release_fence is not None:
            raise MetalDlpackOwnershipError("DLPack capsule deleter was already scheduled")
        if self._pcc_managed_tensor is not None:
            self._deleter_called = True
            return _release_pcc_dlmanaged_pointer(self._dlmanaged_pointer, fence)
        self._release_fence = fence
        if fence.completed:
            return self.reclaim_completed()
        return MetalDlpackReleaseResult(
            status=STATUS_METAL_DLPACK_RELEASE_DEFERRED,
            handle_id=self.imported.buffer_handle.handle_id,
            active_aliases=0,
            pending_count=1,
            fence_completed=False,
        )

    def reclaim_completed(self) -> MetalDlpackReleaseResult:
        fence = self._release_fence
        if fence is None:
            raise MetalDlpackOwnershipError("external DLPack deleter was not scheduled")
        if self._deleter_called:
            raise MetalDlpackOwnershipError("external DLPack deleter was already called")
        if not fence.completed:
            return MetalDlpackReleaseResult(
                status=STATUS_METAL_DLPACK_RECLAIM_PENDING,
                handle_id=self.imported.buffer_handle.handle_id,
                active_aliases=0,
                pending_count=1,
                fence_completed=False,
            )
        deleter = self._dlmanaged_pointer.contents.deleter
        if not bool(deleter):
            raise MetalDlpackOwnershipError("external DLManagedTensor has no deleter")
        deleter(self._dlmanaged_pointer)
        self._deleter_called = True
        return MetalDlpackReleaseResult(
            status=STATUS_METAL_DLPACK_NATIVE_RELEASED,
            handle_id=self.imported.buffer_handle.handle_id,
            active_aliases=0,
            pending_count=0,
            fence_completed=True,
            native_release_executed=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "capsule_name": USED_DLPACK_CAPSULE_NAME,
            "abi": "DLManagedTensor",
            "external_producer": self._pcc_managed_tensor is None,
            "imported": self.imported.to_dict(),
            "runtime_launch_executed": self.runtime_launch_executed,
            "whole_program_gpu": self.whole_program_gpu,
        }


@dataclass
class MetalDlpackTensorOwner:
    """Owns DLPack-style aliases for one launch allocation set."""

    allocation_set: MetalNativeBufferAllocationSet
    launch_plan: MetalLaunchPlan
    device: str = "metal:0"
    release_queue: PccDeferredFreeQueue = field(default_factory=PccDeferredFreeQueue)
    _active_aliases: dict[int, int] = field(default_factory=dict, init=False, repr=False)
    _handles: dict[int, PccBufferHandle] = field(default_factory=dict, init=False, repr=False)
    _released_handle_ids: set[int] = field(default_factory=set, init=False, repr=False)

    def export(self, name: str) -> MetalDlpackManagedTensor:
        if self.allocation_set.released:
            raise MetalDlpackOwnershipError("cannot export from a released allocation set")
        arg = _buffer_arg_by_name(self.launch_plan, name)
        allocation = _allocation_for_handle(self.allocation_set, _require_handle_id(arg))
        descriptor = _descriptor_for_arg(
            arg,
            allocation,
            device=self.device,
        )
        if descriptor.handle_id in self._released_handle_ids:
            raise MetalDlpackOwnershipError(
                f"cannot export released DLPack handle {descriptor.handle_id}"
            )
        self._active_aliases[descriptor.handle_id] = (
            self._active_aliases.get(descriptor.handle_id, 0) + 1
        )
        self._handles.setdefault(
            descriptor.handle_id,
            PccBufferHandle(
                nbytes=descriptor.nbytes,
                dtype=descriptor.dtype,
                device=descriptor.device,
                handle_id=descriptor.handle_id,
            ),
        )
        return MetalDlpackManagedTensor(descriptor=descriptor, _owner=self)

    def release_alias(
        self,
        handle_id: int,
        fence: PccFenceToken,
    ) -> MetalDlpackReleaseResult:
        if not isinstance(fence, PccFenceToken):
            raise MetalDlpackOwnershipError("DLPack deleter requires a PccFenceToken")
        active = self._active_aliases.get(handle_id, 0)
        if active <= 0:
            raise MetalDlpackOwnershipError(f"no active DLPack alias for handle {handle_id}")
        active -= 1
        if active:
            self._active_aliases[handle_id] = active
            return MetalDlpackReleaseResult(
                status=STATUS_METAL_DLPACK_ALIAS_DROPPED,
                handle_id=handle_id,
                active_aliases=active,
                pending_count=self.release_queue.pending_count,
                fence_completed=fence.completed,
            )

        self._active_aliases.pop(handle_id, None)
        handle = self._handles[handle_id]
        self.release_queue.schedule_free(handle, fence)
        return MetalDlpackReleaseResult(
            status=STATUS_METAL_DLPACK_RELEASE_DEFERRED,
            handle_id=handle_id,
            active_aliases=0,
            pending_count=self.release_queue.pending_count,
            fence_completed=fence.completed,
        )

    def reclaim_completed(self) -> MetalDlpackReclaimResult:
        reclaimed = tuple(self.release_queue.reclaim())
        for handle_id in reclaimed:
            self.allocation_set.release_handle(handle_id)
            self._released_handle_ids.add(handle_id)
        return MetalDlpackReclaimResult(
            status=(
                STATUS_METAL_DLPACK_NATIVE_RELEASED
                if reclaimed
                else STATUS_METAL_DLPACK_RECLAIM_PENDING
            ),
            reclaimed_handle_ids=reclaimed,
            pending_count=self.release_queue.pending_count,
            native_release_executed=bool(reclaimed),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_aliases": dict(sorted(self._active_aliases.items())),
            "pending_count": self.release_queue.pending_count,
            "released_handle_ids": sorted(self._released_handle_ids),
            "allocation_set_released": self.allocation_set.released,
            "runtime_launch_executed": False,
            "whole_program_gpu": False,
        }


def import_metal_dlpack_tensor(
    tensor: MetalDlpackManagedTensor,
    *,
    expected_dtype: str | None = None,
    expected_shape: tuple[int, ...] | None = None,
    expected_device: str | None = None,
) -> ImportedMetalDlpackTensor:
    """Consume a DLPack-style tensor and return pcc launcher metadata."""
    descriptor = tensor.consume()
    if descriptor.dl_device_type != "kDLMetal":
        raise MetalDlpackOwnershipError(
            f"expected kDLMetal DLPack device, got {descriptor.dl_device_type!r}"
        )
    if expected_dtype is not None and descriptor.dtype != expected_dtype:
        raise MetalDlpackOwnershipError(
            f"expected DLPack dtype {expected_dtype!r}, got {descriptor.dtype!r}"
        )
    if expected_shape is not None and descriptor.shape != expected_shape:
        raise MetalDlpackOwnershipError(
            f"expected DLPack shape {expected_shape!r}, got {descriptor.shape!r}"
        )
    if expected_device is not None and descriptor.device != expected_device:
        raise MetalDlpackOwnershipError(
            f"expected DLPack device {expected_device!r}, got {descriptor.device!r}"
        )
    handle = PccBufferHandle(
        nbytes=descriptor.nbytes,
        dtype=descriptor.dtype,
        device=descriptor.device,
        handle_id=descriptor.handle_id,
    )
    return ImportedMetalDlpackTensor(
        status=STATUS_METAL_DLPACK_TENSOR_IMPORTED,
        descriptor=descriptor,
        buffer_handle=handle,
        native_mtlbuffer_ptr=descriptor.native_mtlbuffer_ptr,
    )


@dataclass
class _DLManagedTensorStorage:
    tensor: MetalDlpackManagedTensor
    managed: DLManagedTensor
    shape: Any
    strides: Any
    release_fence: PccFenceToken


_DLMANAGED_TENSORS: dict[int, _DLManagedTensorStorage] = {}
_DLMANAGED_DELETER_ERRORS: dict[int, str] = {}
_CAPSULE_POINTERS: dict[int, int] = {}


def _pycapsule_api() -> tuple[Any, Any, Any, Any, Any]:
    try:
        new = ctypes.pythonapi.PyCapsule_New
        is_valid = ctypes.pythonapi.PyCapsule_IsValid
        get_pointer = ctypes.pythonapi.PyCapsule_GetPointer
        set_name = ctypes.pythonapi.PyCapsule_SetName
        get_name = ctypes.pythonapi.PyCapsule_GetName
    except AttributeError as exc:
        raise MetalDlpackOwnershipError("CPython PyCapsule API is unavailable") from exc

    new.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
    new.restype = ctypes.py_object
    is_valid.argtypes = [ctypes.py_object, ctypes.c_char_p]
    is_valid.restype = ctypes.c_int
    get_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
    get_pointer.restype = ctypes.c_void_p
    set_name.argtypes = [ctypes.py_object, ctypes.c_char_p]
    set_name.restype = ctypes.c_int
    get_name.argtypes = [ctypes.py_object]
    get_name.restype = ctypes.c_char_p
    return new, is_valid, get_pointer, set_name, get_name


def pycapsule_name(capsule: object) -> str | None:
    """Return a PyCapsule name for diagnostics."""
    _, _, _, _, get_name = _pycapsule_api()
    raw = get_name(capsule)
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace")


def _dtype_to_c(dtype: str) -> DLDataType:
    item = _dlpack_dtype(dtype)
    return DLDataType(
        _DLPACK_DTYPE_CODES[str(item["code"])],
        int(item["bits"]),
        int(item["lanes"]),
    )


def _dtype_from_c(dtype: DLDataType) -> str:
    for name, item in _DLPACK_DTYPES.items():
        code = _DLPACK_DTYPE_CODES[str(item["code"])]
        if (
            int(dtype.code) == code
            and int(dtype.bits) == int(item["bits"])
            and int(dtype.lanes) == int(item["lanes"])
        ):
            return name
    raise MetalDlpackOwnershipError(
        "unsupported external DLPack dtype "
        f"code={int(dtype.code)} bits={int(dtype.bits)} lanes={int(dtype.lanes)}"
    )


def _release_pcc_dlmanaged_pointer(
    pointer: ctypes.POINTER(DLManagedTensor),
    fence: PccFenceToken | None = None,
) -> MetalDlpackReleaseResult:
    address = ctypes.addressof(pointer.contents)
    storage = _DLMANAGED_TENSORS.get(address)
    if storage is None:
        raise MetalDlpackOwnershipError(
            f"DLManagedTensor pointer {address:#x} is not an active pcc export"
        )
    result = storage.tensor.deleter(
        fence if fence is not None else storage.release_fence
    )
    _DLMANAGED_TENSORS.pop(address, None)
    return result


@DLManagedTensorDeleter
def _dlmanaged_tensor_deleter(pointer: ctypes.POINTER(DLManagedTensor)) -> None:
    if not bool(pointer):
        return
    address = ctypes.addressof(pointer.contents)
    try:
        _release_pcc_dlmanaged_pointer(pointer)
    except BaseException as exc:
        # ctypes callbacks cannot propagate into an external C consumer. Keep
        # a deterministic diagnostic that tests/owners can inspect.
        _DLMANAGED_DELETER_ERRORS[address] = f"{type(exc).__name__}: {exc}"


_PyCapsuleDestructor = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
_RawPyCapsuleGetName = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p)
_raw_pycapsule_get_name = _RawPyCapsuleGetName(
    int(ctypes.cast(ctypes.pythonapi.PyCapsule_GetName, ctypes.c_void_p).value or 0)
)


@_PyCapsuleDestructor
def _dlpack_capsule_destructor(capsule_pointer: int) -> None:
    try:
        # Never wrap the PyObject currently being deallocated as py_object:
        # doing so mutates its refcount from inside tp_dealloc. Export/import
        # maintain this raw-address table, and successful consumption removes
        # the entry before the capsule is renamed to used_dltensor.
        capsule_table = globals().get("_CAPSULE_POINTERS")
        if not isinstance(capsule_table, dict):
            return
        capsule_address = int(capsule_pointer or 0)
        address = capsule_table.pop(capsule_address, 0)
        if not address:
            return
        # A conforming consumer renames the capsule to ``used_dltensor`` and
        # assumes ownership of the managed-tensor deleter. The capsule
        # destructor must do nothing in that case, otherwise an external
        # framework gets a premature/double release. Use a raw PyObject*
        # signature here so tp_dealloc does not receive a temporary py_object.
        capsule_name = _raw_pycapsule_get_name(ctypes.c_void_p(capsule_address))
        if capsule_name != _DLPACK_CAPSULE_NAME_BYTES:
            return
        pointer = ctypes.cast(
            ctypes.c_void_p(address), ctypes.POINTER(DLManagedTensor)
        )
        deleter = pointer.contents.deleter
        if bool(deleter):
            deleter(pointer)
    except BaseException as exc:
        _DLMANAGED_DELETER_ERRORS[-1] = f"{type(exc).__name__}: {exc}"


def _build_dlmanaged_storage(
    tensor: MetalDlpackManagedTensor,
    release_fence: PccFenceToken,
) -> tuple[int, _DLManagedTensorStorage]:
    descriptor = tensor.descriptor
    shape_type = ctypes.c_int64 * len(descriptor.shape)
    shape = shape_type(*descriptor.shape)
    strides = None
    strides_pointer = ctypes.POINTER(ctypes.c_int64)()
    if descriptor.strides is not None:
        strides_type = ctypes.c_int64 * len(descriptor.strides)
        strides = strides_type(*descriptor.strides)
        strides_pointer = ctypes.cast(strides, ctypes.POINTER(ctypes.c_int64))
    managed = DLManagedTensor(
        dl_tensor=DLTensor(
            data=ctypes.c_void_p(descriptor.native_mtlbuffer_ptr),
            device=DLDevice(_DLPACK_DEVICE_TYPE_METAL, descriptor.dl_device_id),
            ndim=len(descriptor.shape),
            dtype=_dtype_to_c(descriptor.dtype),
            shape=ctypes.cast(shape, ctypes.POINTER(ctypes.c_int64)),
            strides=strides_pointer,
            byte_offset=descriptor.byte_offset,
        ),
        manager_ctx=ctypes.c_void_p(),
        deleter=_dlmanaged_tensor_deleter,
    )
    address = ctypes.addressof(managed)
    managed.manager_ctx = ctypes.c_void_p(address)
    return address, _DLManagedTensorStorage(
        tensor, managed, shape, strides, release_fence
    )


def _descriptor_from_dlmanaged_pointer(
    pointer: ctypes.POINTER(DLManagedTensor),
    *,
    pcc_tensor: MetalDlpackManagedTensor | None,
) -> MetalDlpackTensorDescriptor:
    address = ctypes.addressof(pointer.contents)
    dl_tensor = pointer.contents.dl_tensor
    if int(dl_tensor.device.device_type) != _DLPACK_DEVICE_TYPE_METAL:
        raise MetalDlpackOwnershipError(
            "expected external DLPack kDLMetal device type 8, got "
            f"{int(dl_tensor.device.device_type)}"
        )
    ndim = int(dl_tensor.ndim)
    if not (1 <= ndim <= 64) or not bool(dl_tensor.shape):
        raise MetalDlpackOwnershipError(
            f"external DLManagedTensor has invalid ndim/shape ({ndim})"
        )
    shape = tuple(int(dl_tensor.shape[i]) for i in range(ndim))
    if any(dim <= 0 for dim in shape):
        raise MetalDlpackOwnershipError(
            f"external DLManagedTensor has invalid shape {shape!r}"
        )
    strides = None
    if bool(dl_tensor.strides):
        strides = tuple(int(dl_tensor.strides[i]) for i in range(ndim))
        if any(stride < 0 for stride in strides):
            raise MetalDlpackOwnershipError(
                f"external DLManagedTensor has unsupported negative strides {strides!r}"
            )
        expected: list[int] = []
        running = 1
        for dim in reversed(shape):
            expected.append(running)
            running *= dim
        if strides != tuple(reversed(expected)):
            raise MetalDlpackOwnershipError(
                "external DLManagedTensor requires contiguous row-major strides; "
                f"got {strides!r}"
            )
    if int(dl_tensor.byte_offset) != 0:
        raise MetalDlpackOwnershipError(
            "external DLManagedTensor byte_offset is not representable by the "
            "current PccBufferHandle ABI"
        )
    data = int(dl_tensor.data or 0)
    if data == 0:
        raise MetalDlpackOwnershipError("external DLManagedTensor data pointer is NULL")
    dtype = _dtype_from_c(dl_tensor.dtype)
    dtype_info = _dlpack_dtype(dtype)
    elements = 1
    for dim in shape:
        elements *= dim
    nbytes = (
        elements * int(dtype_info["bits"]) * int(dtype_info["lanes"]) + 7
    ) // 8
    if pcc_tensor is not None:
        expected = pcc_tensor.descriptor
        if (
            dtype != expected.dtype
            or shape != expected.shape
            or strides != expected.strides
            or data != expected.native_mtlbuffer_ptr
            or int(dl_tensor.device.device_id) != expected.dl_device_id
        ):
            raise MetalDlpackOwnershipError(
                "pcc-owned DLManagedTensor ABI fields were mutated before import"
            )
        name = pcc_tensor.descriptor.name
        handle_id = pcc_tensor.descriptor.handle_id
        nbytes = pcc_tensor.descriptor.nbytes
    else:
        name = "external_dlpack"
        handle_id = address
    return MetalDlpackTensorDescriptor(
        name=name,
        dtype=dtype,
        shape=shape,
        device=f"metal:{int(dl_tensor.device.device_id)}",
        handle_id=handle_id,
        native_mtlbuffer_ptr=data,
        nbytes=nbytes,
        dl_device_id=int(dl_tensor.device.device_id),
        byte_offset=int(dl_tensor.byte_offset),
        strides=strides,
    )


def _check_descriptor_expectations(
    descriptor: MetalDlpackTensorDescriptor,
    *,
    expected_dtype: str | None,
    expected_shape: tuple[int, ...] | None,
    expected_device: str | None,
) -> None:
    if expected_dtype is not None and descriptor.dtype != expected_dtype:
        raise MetalDlpackOwnershipError(
            f"expected DLPack dtype {expected_dtype!r}, got {descriptor.dtype!r}"
        )
    if expected_shape is not None and descriptor.shape != expected_shape:
        raise MetalDlpackOwnershipError(
            f"expected DLPack shape {expected_shape!r}, got {descriptor.shape!r}"
        )
    if expected_device is not None and descriptor.device != expected_device:
        raise MetalDlpackOwnershipError(
            f"expected DLPack device {expected_device!r}, got {descriptor.device!r}"
        )


def export_metal_dlpack_py_capsule(
    tensor: MetalDlpackManagedTensor,
    *,
    stream: int | None = None,
    release_fence: PccFenceToken | None = None,
) -> MetalDlpackCapsuleExport:
    """Export a managed tensor as a real CPython ``dltensor`` PyCapsule."""
    if stream not in (None, 0):
        raise MetalDlpackOwnershipError(
            "non-default DLPack stream synchronization is not implemented yet"
        )
    if not isinstance(tensor, MetalDlpackManagedTensor):
        raise MetalDlpackOwnershipError("DLPack capsule export requires a managed tensor")
    if release_fence is None:
        release_fence = PccFenceToken()
        release_fence.complete()
    if not isinstance(release_fence, PccFenceToken):
        raise MetalDlpackOwnershipError(
            "DLPack capsule release requires a PccFenceToken"
        )
    pointer_id, storage = _build_dlmanaged_storage(tensor, release_fence)
    new, _, _, _, _ = _pycapsule_api()
    _DLMANAGED_TENSORS[pointer_id] = storage
    try:
        capsule = new(
            ctypes.c_void_p(pointer_id),
            _DLPACK_CAPSULE_NAME_BYTES,
            ctypes.cast(_dlpack_capsule_destructor, ctypes.c_void_p),
        )
        _CAPSULE_POINTERS[id(capsule)] = pointer_id
    except BaseException:
        _DLMANAGED_TENSORS.pop(pointer_id, None)
        raise
    return MetalDlpackCapsuleExport(
        status=STATUS_METAL_DLPACK_CAPSULE_EXPORTED,
        capsule=capsule,
        descriptor=tensor.descriptor,
        pointer_id=pointer_id,
        stream=stream,
    )


def export_metal_dlpack_protocol(
    tensor: MetalDlpackManagedTensor,
    *,
    release_fence: PccFenceToken,
) -> MetalDlpackProtocolExport:
    """Return a one-shot object accepted by ``framework.from_dlpack`` APIs."""
    if not isinstance(tensor, MetalDlpackManagedTensor):
        raise MetalDlpackOwnershipError(
            "DLPack protocol export requires a managed tensor"
        )
    if not isinstance(release_fence, PccFenceToken):
        raise MetalDlpackOwnershipError(
            "DLPack protocol export requires a PccFenceToken"
        )
    return MetalDlpackProtocolExport(tensor=tensor, release_fence=release_fence)


def import_metal_dlpack_py_capsule(
    capsule: object,
    *,
    expected_dtype: str | None = None,
    expected_shape: tuple[int, ...] | None = None,
    expected_device: str | None = None,
    stream: int | None = None,
) -> ImportedMetalDlpackCapsule:
    """Consume any classic ``DLManagedTensor`` capsule into pcc metadata."""
    if stream not in (None, 0):
        raise MetalDlpackOwnershipError(
            "non-default DLPack stream synchronization is not implemented yet"
        )
    _, is_valid, get_pointer, set_name, _ = _pycapsule_api()
    if not bool(is_valid(capsule, _DLPACK_CAPSULE_NAME_BYTES)):
        raise MetalDlpackOwnershipError(
            "DLPack capsule must be valid with name 'dltensor'"
        )
    pointer = int(get_pointer(capsule, _DLPACK_CAPSULE_NAME_BYTES) or 0)
    if pointer == 0:
        raise MetalDlpackOwnershipError("DLPack capsule pointer is NULL")
    dlmanaged_pointer = ctypes.cast(
        ctypes.c_void_p(pointer), ctypes.POINTER(DLManagedTensor)
    )
    storage = _DLMANAGED_TENSORS.get(pointer)
    tensor = storage.tensor if storage is not None else None
    descriptor = _descriptor_from_dlmanaged_pointer(
        dlmanaged_pointer, pcc_tensor=tensor
    )
    _check_descriptor_expectations(
        descriptor,
        expected_dtype=expected_dtype,
        expected_shape=expected_shape,
        expected_device=expected_device,
    )
    if tensor is None and not bool(dlmanaged_pointer.contents.deleter):
        raise MetalDlpackOwnershipError("external DLManagedTensor has no deleter")
    if tensor is not None:
        tensor.consume()
    if set_name(capsule, _USED_DLPACK_CAPSULE_NAME_BYTES) != 0:
        raise MetalDlpackOwnershipError("failed to mark DLPack capsule as used")
    _CAPSULE_POINTERS.pop(id(capsule), None)
    handle = PccBufferHandle(
        nbytes=descriptor.nbytes,
        dtype=descriptor.dtype,
        device=descriptor.device,
        handle_id=descriptor.handle_id,
    )
    imported = ImportedMetalDlpackTensor(
        status=STATUS_METAL_DLPACK_TENSOR_IMPORTED,
        descriptor=descriptor,
        buffer_handle=handle,
        native_mtlbuffer_ptr=descriptor.native_mtlbuffer_ptr,
    )
    return ImportedMetalDlpackCapsule(
        status=STATUS_METAL_DLPACK_CAPSULE_IMPORTED,
        imported=imported,
        _dlmanaged_pointer=dlmanaged_pointer,
        _pcc_managed_tensor=tensor,
    )


def _dlpack_dtype(dtype: str) -> dict[str, Any]:
    out = _DLPACK_DTYPES.get(dtype)
    if out is None:
        raise MetalDlpackOwnershipError(f"unsupported DLPack dtype {dtype!r}")
    return out


def _buffer_arg_by_name(launch_plan: MetalLaunchPlan, name: str) -> MetalRuntimeArg:
    matches = [arg for arg in launch_plan.args if arg.kind == "buffer" and arg.name == name]
    if len(matches) != 1:
        raise MetalDlpackOwnershipError(f"{name!r} is not a unique launch buffer arg")
    return matches[0]


def _require_handle_id(arg: MetalRuntimeArg) -> int:
    if arg.handle_id is None:
        raise MetalDlpackOwnershipError(f"{arg.name!r} has no PccBufferHandle id")
    return arg.handle_id


def _allocation_for_handle(
    allocation_set: MetalNativeBufferAllocationSet,
    handle_id: int,
) -> MetalNativeBufferAllocation:
    for allocation in allocation_set.allocations:
        if allocation.handle_id == handle_id:
            return allocation
    raise MetalDlpackOwnershipError(f"no native allocation for DLPack handle {handle_id}")


def _descriptor_for_arg(
    arg: MetalRuntimeArg,
    allocation: MetalNativeBufferAllocation,
    *,
    device: str,
) -> MetalDlpackTensorDescriptor:
    if arg.shape is None:
        raise MetalDlpackOwnershipError(f"{arg.name!r} DLPack export requires static shape")
    if arg.dtype not in _DLPACK_DTYPES:
        raise MetalDlpackOwnershipError(f"{arg.name!r} has unsupported DLPack dtype {arg.dtype!r}")
    nbytes = arg.required_nbytes if arg.required_nbytes is not None else allocation.reported_nbytes
    if nbytes > allocation.reported_nbytes:
        raise MetalDlpackOwnershipError(
            f"{arg.name!r} requires {nbytes} bytes but native allocation has "
            f"{allocation.reported_nbytes}"
        )
    return MetalDlpackTensorDescriptor(
        name=arg.name,
        dtype=arg.dtype,
        shape=arg.shape,
        device=device,
        handle_id=allocation.handle_id,
        native_mtlbuffer_ptr=allocation.native_mtlbuffer_ptr,
        nbytes=nbytes,
    )


__all__ = [
    "DLDataType",
    "DLDevice",
    "DLManagedTensor",
    "DLManagedTensorDeleter",
    "DLTensor",
    "ImportedMetalDlpackTensor",
    "ImportedMetalDlpackCapsule",
    "DLPACK_CAPSULE_NAME",
    "USED_DLPACK_CAPSULE_NAME",
    "MetalDlpackCapsuleExport",
    "MetalDlpackProtocolExport",
    "MetalDlpackManagedTensor",
    "MetalDlpackOwnershipError",
    "MetalDlpackReclaimResult",
    "MetalDlpackReleaseResult",
    "MetalDlpackTensorDescriptor",
    "MetalDlpackTensorOwner",
    "STATUS_METAL_DLPACK_ALIAS_DROPPED",
    "STATUS_METAL_DLPACK_CAPSULE_EXPORTED",
    "STATUS_METAL_DLPACK_CAPSULE_IMPORTED",
    "STATUS_METAL_DLPACK_NATIVE_RELEASED",
    "STATUS_METAL_DLPACK_RECLAIM_PENDING",
    "STATUS_METAL_DLPACK_RELEASE_DEFERRED",
    "STATUS_METAL_DLPACK_TENSOR_EXPORTED",
    "STATUS_METAL_DLPACK_TENSOR_IMPORTED",
    "export_metal_dlpack_py_capsule",
    "export_metal_dlpack_protocol",
    "import_metal_dlpack_tensor",
    "import_metal_dlpack_py_capsule",
    "pycapsule_name",
]
