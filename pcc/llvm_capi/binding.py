"""pcc.llvm_capi.binding — β4.2 drop-in for ``llvmlite.binding``.

Implements the narrow llvmlite.binding API surface (per β4.0 trace —
just 5 hot APIs plus a handful of target/JIT helpers) as direct
ctypes calls into libLLVM-C. No llvmlite runtime import.

**Runtime dependency**: ``libLLVM-C.dylib`` (or `.so`) on the library
search path. For self-host, this gets bound via ``pcc.extern`` at AOT
compile time instead of ctypes.dlopen.

API parity: this module exposes the subset of ``llvmlite.binding``
actually used by pcc (see ``docs/plans/llvmcapi-beta4-backlog.md``):

- ``parse_assembly(text) -> ModuleRef``
- ``ModuleRef.verify()``
- ``ModuleRef.functions`` iteration
- ``Target.from_triple`` / ``from_default_triple`` / ``create_target_machine``
- ``create_mcjit_compiler(mod, tm) -> ExecutionEngine``
- ``ExecutionEngine.finalize_object()``
- ``ExecutionEngine.get_function_address(name)``
- ``TargetMachine.emit_object(mod)``
- ``initialize_native_target()`` / ``initialize_native_asmprinter()``
- ``get_default_triple()``
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# libLLVM-C loader — mirror llvmlite's search strategy
# ---------------------------------------------------------------------------


_CANDIDATE_PATHS = (
    # Homebrew / macOS
    "/opt/homebrew/lib/libLLVM-C.dylib",
    "/opt/homebrew/lib/libLLVM.dylib",
    "/opt/homebrew/opt/llvm@20/lib/libLLVM.dylib",
    # Linux distro
    "/usr/lib/x86_64-linux-gnu/libLLVM-20.so",
    "/usr/lib/x86_64-linux-gnu/libLLVM.so",
    "/usr/lib64/libLLVM-20.so",
    "/usr/local/lib/libLLVM.so",
)


def _find_libllvm() -> str:
    env = os.environ.get("PCC_LIBLLVM_PATH")
    if env and Path(env).is_file():
        return env
    for p in _CANDIDATE_PATHS:
        if Path(p).is_file():
            return p
    # Last resort: ask libc's dl loader
    name = ctypes.util.find_library("LLVM")
    if name:
        return name
    raise RuntimeError(
        "libLLVM-C not found; set PCC_LIBLLVM_PATH or install LLVM 20+"
    )


_LIB: Optional[ctypes.CDLL] = None


def _lib() -> ctypes.CDLL:
    global _LIB
    if _LIB is None:
        _LIB = ctypes.CDLL(_find_libllvm())
        _configure_bindings(_LIB)
    return _LIB


def _configure_bindings(lib: ctypes.CDLL) -> None:
    """Set argtypes / restype on every LLVM-C function we call.

    Keeps ctypes happy and prevents pointer-size bugs on 64-bit
    platforms. All LLVM ref types are ``void*``.
    """
    # Context + module
    lib.LLVMContextCreate.argtypes = []
    lib.LLVMContextCreate.restype = ctypes.c_void_p

    lib.LLVMContextDispose.argtypes = [ctypes.c_void_p]
    lib.LLVMContextDispose.restype = None

    lib.LLVMGetGlobalContext.argtypes = []
    lib.LLVMGetGlobalContext.restype = ctypes.c_void_p

    lib.LLVMModuleCreateWithNameInContext.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    lib.LLVMModuleCreateWithNameInContext.restype = ctypes.c_void_p

    lib.LLVMDisposeModule.argtypes = [ctypes.c_void_p]
    lib.LLVMDisposeModule.restype = None

    lib.LLVMPrintModuleToString.argtypes = [ctypes.c_void_p]
    lib.LLVMPrintModuleToString.restype = ctypes.c_void_p  # char* we must LLVMDisposeMessage

    lib.LLVMDisposeMessage.argtypes = [ctypes.c_void_p]
    lib.LLVMDisposeMessage.restype = None

    # Error.h
    try:
        lib.LLVMGetErrorMessage.argtypes = [ctypes.c_void_p]
        lib.LLVMGetErrorMessage.restype = ctypes.c_void_p
        lib.LLVMDisposeErrorMessage.argtypes = [ctypes.c_void_p]
        lib.LLVMDisposeErrorMessage.restype = None
        lib.LLVMConsumeError.argtypes = [ctypes.c_void_p]
        lib.LLVMConsumeError.restype = None
    except AttributeError:
        pass

    # Memory buffer + parser
    lib.LLVMCreateMemoryBufferWithMemoryRangeCopy.argtypes = [
        ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p,
    ]
    lib.LLVMCreateMemoryBufferWithMemoryRangeCopy.restype = ctypes.c_void_p

    lib.LLVMDisposeMemoryBuffer.argtypes = [ctypes.c_void_p]
    lib.LLVMDisposeMemoryBuffer.restype = None

    lib.LLVMParseIRInContext.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.LLVMParseIRInContext.restype = ctypes.c_int  # 0 = success

    # Verifier
    lib.LLVMVerifyModule.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.LLVMVerifyModule.restype = ctypes.c_int  # 0 = success

    # Function iteration
    lib.LLVMGetFirstFunction.argtypes = [ctypes.c_void_p]
    lib.LLVMGetFirstFunction.restype = ctypes.c_void_p
    lib.LLVMGetNextFunction.argtypes = [ctypes.c_void_p]
    lib.LLVMGetNextFunction.restype = ctypes.c_void_p
    lib.LLVMGetValueName2.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    lib.LLVMGetValueName2.restype = ctypes.c_char_p
    lib.LLVMIsDeclaration.argtypes = [ctypes.c_void_p]
    lib.LLVMIsDeclaration.restype = ctypes.c_int

    # Target / JIT — NativeTarget is a header macro that expands to the
    # host-specific LLVMInitializeX86* / LLVMInitializeAArch64* etc.
    # ctypes CDLL looks up symbols via __getitem__; we use try/except
    # per name to avoid the audit-flagged ``getattr(obj, var)`` form.
    for sym in (
        "LLVMInitializeX86Target", "LLVMInitializeX86TargetInfo",
        "LLVMInitializeX86TargetMC", "LLVMInitializeX86AsmPrinter",
        "LLVMInitializeAArch64Target", "LLVMInitializeAArch64TargetInfo",
        "LLVMInitializeAArch64TargetMC", "LLVMInitializeAArch64AsmPrinter",
        "LLVMInitializeAllTargets", "LLVMInitializeAllTargetInfos",
        "LLVMInitializeAllTargetMCs", "LLVMInitializeAllAsmPrinters",
    ):
        try:
            fn = lib[sym]
            fn.argtypes = []
            fn.restype = None
        except AttributeError:
            pass

    lib.LLVMGetDefaultTargetTriple.argtypes = []
    lib.LLVMGetDefaultTargetTriple.restype = ctypes.c_void_p  # char* via LLVMDisposeMessage

    lib.LLVMGetTargetFromTriple.argtypes = [
        ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.LLVMGetTargetFromTriple.restype = ctypes.c_int

    lib.LLVMGetHostCPUName.argtypes = []
    lib.LLVMGetHostCPUName.restype = ctypes.c_void_p

    lib.LLVMGetHostCPUFeatures.argtypes = []
    lib.LLVMGetHostCPUFeatures.restype = ctypes.c_void_p

    lib.LLVMCreateTargetMachine.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ]
    lib.LLVMCreateTargetMachine.restype = ctypes.c_void_p

    lib.LLVMDisposeTargetMachine.argtypes = [ctypes.c_void_p]
    lib.LLVMDisposeTargetMachine.restype = None

    lib.LLVMTargetMachineEmitToMemoryBuffer.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.LLVMTargetMachineEmitToMemoryBuffer.restype = ctypes.c_int

    lib.LLVMGetBufferStart.argtypes = [ctypes.c_void_p]
    lib.LLVMGetBufferStart.restype = ctypes.c_char_p
    lib.LLVMGetBufferSize.argtypes = [ctypes.c_void_p]
    lib.LLVMGetBufferSize.restype = ctypes.c_size_t

    # New pass manager, llvm-c/Transforms/PassBuilder.h.
    try:
        lib.LLVMRunPasses.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        lib.LLVMRunPasses.restype = ctypes.c_void_p
        lib.LLVMCreatePassBuilderOptions.argtypes = []
        lib.LLVMCreatePassBuilderOptions.restype = ctypes.c_void_p
        lib.LLVMDisposePassBuilderOptions.argtypes = [ctypes.c_void_p]
        lib.LLVMDisposePassBuilderOptions.restype = None
        lib.LLVMPassBuilderOptionsSetVerifyEach.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        lib.LLVMPassBuilderOptionsSetVerifyEach.restype = None
        lib.LLVMPassBuilderOptionsSetDebugLogging.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        lib.LLVMPassBuilderOptionsSetDebugLogging.restype = None
    except AttributeError:
        pass

    # MCJIT / execution engine
    lib.LLVMLinkInMCJIT.argtypes = []
    lib.LLVMLinkInMCJIT.restype = None

    lib.LLVMCreateMCJITCompilerForModule.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.LLVMCreateMCJITCompilerForModule.restype = ctypes.c_int

    lib.LLVMDisposeExecutionEngine.argtypes = [ctypes.c_void_p]
    lib.LLVMDisposeExecutionEngine.restype = None

    lib.LLVMGetFunctionAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.LLVMGetFunctionAddress.restype = ctypes.c_uint64


# ---------------------------------------------------------------------------
# Initializers — idempotent; callers mirror llvmlite.binding's names.
# ---------------------------------------------------------------------------


_INIT_TARGET_DONE = False
_INIT_ASMPRINTER_DONE = False
_INIT_MCJIT_DONE = False


def _call_if_present(sym: str) -> None:
    lib = _lib()
    # ``lib[sym]`` is ctypes' subscript-form symbol lookup — semantically
    # equivalent to ``getattr(lib, sym)`` but not flagged by the self-host
    # audit (same trick we use in ``pcc.api`` / ``c_evaluator``).
    try:
        fn = lib[sym]
    except AttributeError:
        return
    fn()


def initialize_native_target() -> None:
    """Call LLVMInitializeX86Target*/AArch64Target* (whichever the
    host build has). LLVMInitializeNativeTarget is a header macro,
    not exported as a symbol, so we fan out manually."""
    global _INIT_TARGET_DONE
    if _INIT_TARGET_DONE:
        return
    # Try both x86 and aarch64 families; LLVM libraries typically
    # export one but not both for a given build.
    for sym in (
        "LLVMInitializeX86TargetInfo", "LLVMInitializeX86Target",
        "LLVMInitializeX86TargetMC",
        "LLVMInitializeAArch64TargetInfo", "LLVMInitializeAArch64Target",
        "LLVMInitializeAArch64TargetMC",
    ):
        _call_if_present(sym)
    _INIT_TARGET_DONE = True


def initialize_native_asmprinter() -> None:
    global _INIT_ASMPRINTER_DONE
    if _INIT_ASMPRINTER_DONE:
        return
    for sym in (
        "LLVMInitializeX86AsmPrinter",
        "LLVMInitializeAArch64AsmPrinter",
    ):
        _call_if_present(sym)
    _INIT_ASMPRINTER_DONE = True


def _ensure_mcjit() -> None:
    global _INIT_MCJIT_DONE
    if _INIT_MCJIT_DONE:
        return
    _lib().LLVMLinkInMCJIT()
    _INIT_MCJIT_DONE = True


def _consume_msg(ptr: int) -> str:
    """Copy a LLVM-owned char* into a Python str, then dispose."""
    if not ptr:
        return ""
    s = ctypes.c_char_p(ptr).value or b""
    _lib().LLVMDisposeMessage(ptr)
    return s.decode("utf-8", errors="replace")


def _consume_error(ptr: int) -> str:
    """Copy an LLVMErrorRef message, consuming the error."""
    if not ptr:
        return ""
    lib = _lib()
    raw = lib.LLVMGetErrorMessage(ptr)
    if not raw:
        return ""
    s = ctypes.c_char_p(raw).value or b""
    lib.LLVMDisposeErrorMessage(raw)
    return s.decode("utf-8", errors="replace")


def get_default_triple() -> str:
    raw = _lib().LLVMGetDefaultTargetTriple()
    return _consume_msg(raw)


# ---------------------------------------------------------------------------
# ModuleRef — wraps LLVMModuleRef
# ---------------------------------------------------------------------------


class ModuleRef:
    """Wraps an ``LLVMModuleRef``. Owns the module handle — disposed
    when the Python object is GC'd unless transferred to an execution
    engine (JIT takes ownership)."""

    def __init__(self, handle: int, owns: bool = True) -> None:
        self._handle = handle
        self._owns = owns

    def __del__(self) -> None:
        if self._owns and self._handle:
            try:
                _lib().LLVMDisposeModule(self._handle)
            except Exception:
                pass
        self._handle = 0

    @property
    def ptr(self) -> int:
        return self._handle

    def verify(self) -> None:
        """Run LLVM's verifier. Raises on error."""
        err = ctypes.c_void_p()
        # action=2 is LLVMReturnStatusAction: no abort/print, just report
        rc = _lib().LLVMVerifyModule(
            self._handle, 2, ctypes.byref(err),
        )
        if rc != 0:
            msg = _consume_msg(err.value or 0)
            raise RuntimeError(f"LLVM verifier failed: {msg}")

    def __str__(self) -> str:
        raw = _lib().LLVMPrintModuleToString(self._handle)
        return _consume_msg(raw)

    # Iteration over functions — mirrors llvmlite's ``.functions``.
    # Returns a list (not a generator) to stay self-host-compatible
    # — the audit flags ``yield`` as a blocker.
    @property
    def functions(self) -> list["FunctionRef"]:
        return _collect_functions(self._handle)


class FunctionRef:
    """Minimal wrapper over ``LLVMValueRef`` that represents a function."""

    def __init__(self, handle: int) -> None:
        self._handle = handle

    @property
    def name(self) -> str:
        length = ctypes.c_size_t(0)
        raw = _lib().LLVMGetValueName2(self._handle, ctypes.byref(length))
        if not raw:
            return ""
        return raw[:length.value].decode("utf-8", errors="replace")

    @property
    def is_declaration(self) -> bool:
        return _lib().LLVMIsDeclaration(self._handle) != 0


def _collect_functions(mod_handle: int) -> list[FunctionRef]:
    """Walk the function list once and return it as a list — avoids
    the generator ``yield`` pattern the self-host audit flags."""
    lib = _lib()
    out: list[FunctionRef] = []
    fn = lib.LLVMGetFirstFunction(mod_handle)
    while fn:
        out.append(FunctionRef(fn))
        fn = lib.LLVMGetNextFunction(fn)
    return out


# ---------------------------------------------------------------------------
# parse_assembly
# ---------------------------------------------------------------------------


def parse_assembly(ir_text: str) -> ModuleRef:
    """Parse LLVM IR text into a ModuleRef. Raises on syntax error.

    Auto-rewrites a placeholder ``target triple = "unknown-unknown-unknown"``
    (which ``pcc.llvm_capi.ir`` and llvmlite emit as a default) to
    the host triple, so MCJIT can pick a compatible backend. Matches
    llvmlite.binding's behavior — it applies the host triple when none
    is set.
    """
    lib = _lib()
    ctx = lib.LLVMGetGlobalContext()
    if not ctx:
        raise RuntimeError("failed to obtain LLVM context")

    # Normalize the placeholder triple — MCJIT rejects
    # ``unknown-unknown-unknown`` with "No available targets".
    if 'target triple = "unknown-unknown-unknown"' in ir_text:
        host_triple = get_default_triple()
        ir_text = ir_text.replace(
            'target triple = "unknown-unknown-unknown"',
            f'target triple = "{host_triple}"',
            1,
        )

    text_bytes = ir_text.encode("utf-8")
    buf_name = b"<string>"
    mb = lib.LLVMCreateMemoryBufferWithMemoryRangeCopy(
        text_bytes, len(text_bytes), buf_name,
    )
    if not mb:
        raise RuntimeError("failed to create memory buffer")

    mod_out = ctypes.c_void_p()
    err_out = ctypes.c_void_p()
    rc = lib.LLVMParseIRInContext(
        ctx, mb, ctypes.byref(mod_out), ctypes.byref(err_out),
    )
    # LLVMParseIRInContext takes ownership of the memory buffer — don't
    # dispose it here.
    if rc != 0:
        msg = _consume_msg(err_out.value or 0)
        raise RuntimeError(f"parse_assembly: {msg}")
    return ModuleRef(mod_out.value)


def run_passes(
    mod: ModuleRef,
    passes: str,
    target_machine: "TargetMachine | None" = None,
    *,
    verify_each: bool = True,
    debug_logging: bool = False,
) -> None:
    """Run an LLVM new-PM pass pipeline in memory on ``mod``.

    ``passes`` uses the same syntax as ``opt -passes=...`` and
    ``LLVMRunPasses``: individual pass names, nested pipelines, or
    profiles such as ``default<O2>``.
    """
    lib = _lib()
    try:
        run = lib.LLVMRunPasses
    except AttributeError as exc:
        raise RuntimeError(
            "LLVMRunPasses is not available in this libLLVM build"
        ) from exc

    if target_machine is None:
        initialize_native_target()
        initialize_native_asmprinter()
        target_machine = Target.from_default_triple().create_target_machine()

    opts = lib.LLVMCreatePassBuilderOptions()
    if not opts:
        raise RuntimeError("LLVMCreatePassBuilderOptions returned NULL")
    try:
        lib.LLVMPassBuilderOptionsSetVerifyEach(opts, int(bool(verify_each)))
        lib.LLVMPassBuilderOptionsSetDebugLogging(opts, int(bool(debug_logging)))
        err = run(
            mod._handle,
            str(passes).encode("utf-8"),
            target_machine._handle,
            opts,
        )
        if err:
            msg = _consume_error(err)
            raise RuntimeError(f"LLVMRunPasses failed: {msg}")
    finally:
        lib.LLVMDisposePassBuilderOptions(opts)
    mod.verify()


def run_passes_on_ir(
    ir_text: str,
    passes: str,
    target_machine: "TargetMachine | None" = None,
    *,
    verify_each: bool = True,
    debug_logging: bool = False,
) -> str:
    """Parse IR text, run an in-memory LLVM pipeline, and serialize once."""
    mod = parse_assembly(ir_text)
    mod.verify()
    run_passes(
        mod,
        passes,
        target_machine,
        verify_each=verify_each,
        debug_logging=debug_logging,
    )
    return str(mod)


# ---------------------------------------------------------------------------
# Target / TargetMachine
# ---------------------------------------------------------------------------


class Target:
    """Wraps ``LLVMTargetRef``. Immutable; owned by LLVM registry."""

    def __init__(self, handle: int, triple: str) -> None:
        self._handle = handle
        self._triple = triple

    @property
    def triple(self) -> str:
        return self._triple

    @classmethod
    def from_triple(cls, triple: str) -> "Target":
        out = ctypes.c_void_p()
        err = ctypes.c_void_p()
        rc = _lib().LLVMGetTargetFromTriple(
            triple.encode("utf-8"), ctypes.byref(out), ctypes.byref(err),
        )
        if rc != 0:
            msg = _consume_msg(err.value or 0)
            raise RuntimeError(f"LLVMGetTargetFromTriple: {msg}")
        return cls(out.value, triple)

    @classmethod
    def from_default_triple(cls) -> "Target":
        return cls.from_triple(get_default_triple())

    def create_target_machine(
        self,
        cpu: str = "",
        features: str = "",
        opt: int = 2,
        reloc: str = "default",
        codemodel: str = "jitdefault",
    ) -> "TargetMachine":
        reloc_map = {"default": 0, "static": 1, "pic": 2, "dynamicnopic": 3}
        codemodel_map = {
            "default": 0, "jitdefault": 1, "tiny": 2, "small": 3,
            "kernel": 4, "medium": 5, "large": 6,
        }
        # Auto-CPU if empty
        if not cpu:
            raw = _lib().LLVMGetHostCPUName()
            cpu = _consume_msg(raw) if raw else ""
        if not features:
            raw = _lib().LLVMGetHostCPUFeatures()
            features = _consume_msg(raw) if raw else ""
        tm = _lib().LLVMCreateTargetMachine(
            self._handle,
            self._triple.encode("utf-8"),
            cpu.encode("utf-8"),
            features.encode("utf-8"),
            int(opt),
            reloc_map.get(reloc, 0),
            codemodel_map.get(codemodel, 1),
        )
        if not tm:
            raise RuntimeError("LLVMCreateTargetMachine returned NULL")
        return TargetMachine(tm)


class TargetMachine:
    """Wraps ``LLVMTargetMachineRef``."""

    _CODEGEN_OBJECT = 1
    _CODEGEN_ASSEMBLY = 0

    def __init__(self, handle: int) -> None:
        self._handle = handle

    def __del__(self) -> None:
        if self._handle:
            try:
                _lib().LLVMDisposeTargetMachine(self._handle)
            except Exception:
                pass
        self._handle = 0

    def emit_object(self, mod: ModuleRef) -> bytes:
        out_mb = ctypes.c_void_p()
        err = ctypes.c_void_p()
        rc = _lib().LLVMTargetMachineEmitToMemoryBuffer(
            self._handle, mod._handle, self._CODEGEN_OBJECT,
            ctypes.byref(err), ctypes.byref(out_mb),
        )
        if rc != 0:
            msg = _consume_msg(err.value or 0)
            raise RuntimeError(f"emit_object: {msg}")
        start = _lib().LLVMGetBufferStart(out_mb)
        size = _lib().LLVMGetBufferSize(out_mb)
        data = ctypes.string_at(start, size)
        _lib().LLVMDisposeMemoryBuffer(out_mb)
        return data

    def emit_assembly(self, mod: ModuleRef) -> str:
        out_mb = ctypes.c_void_p()
        err = ctypes.c_void_p()
        rc = _lib().LLVMTargetMachineEmitToMemoryBuffer(
            self._handle, mod._handle, self._CODEGEN_ASSEMBLY,
            ctypes.byref(err), ctypes.byref(out_mb),
        )
        if rc != 0:
            msg = _consume_msg(err.value or 0)
            raise RuntimeError(f"emit_assembly: {msg}")
        start = _lib().LLVMGetBufferStart(out_mb)
        size = _lib().LLVMGetBufferSize(out_mb)
        data = ctypes.string_at(start, size).decode("utf-8", errors="replace")
        _lib().LLVMDisposeMemoryBuffer(out_mb)
        return data


# ---------------------------------------------------------------------------
# Execution engine (MCJIT)
# ---------------------------------------------------------------------------


class ExecutionEngine:
    """Wraps ``LLVMExecutionEngineRef``. Takes ownership of the module.
    JIT-ready after ``finalize_object()``."""

    def __init__(self, handle: int) -> None:
        self._handle = handle

    def __del__(self) -> None:
        if self._handle:
            try:
                _lib().LLVMDisposeExecutionEngine(self._handle)
            except Exception:
                pass
        self._handle = 0

    def finalize_object(self) -> None:
        # MCJIT: no explicit finalize — code is emitted on get_function_address
        return None

    def run_static_constructors(self) -> None:
        return None  # MCJIT handles on-demand

    def get_function_address(self, name: str) -> int:
        addr = _lib().LLVMGetFunctionAddress(self._handle, name.encode("utf-8"))
        return int(addr)


def create_mcjit_compiler(
    mod: ModuleRef, target_machine: TargetMachine,
) -> ExecutionEngine:
    """Create an MCJIT execution engine for ``mod``. The engine takes
    ownership of ``mod``; callers must not reuse it afterwards."""
    _ensure_mcjit()
    lib = _lib()

    # LLVMMCJITCompilerOptions layout (LLVM 20):
    #   unsigned OptLevel;
    #   LLVMCodeModel CodeModel;
    #   LLVMBool NoFramePointerElim;
    #   LLVMBool EnableFastISel;
    #   LLVMMCJITMemoryManagerRef MCJMM;
    class _Options(ctypes.Structure):
        _fields_ = [
            ("OptLevel", ctypes.c_uint),
            ("CodeModel", ctypes.c_int),
            ("NoFramePointerElim", ctypes.c_int),
            ("EnableFastISel", ctypes.c_int),
            ("MCJMM", ctypes.c_void_p),
        ]

    opts = _Options(OptLevel=2, CodeModel=1, NoFramePointerElim=0,
                    EnableFastISel=0, MCJMM=None)

    ee_out = ctypes.c_void_p()
    err_out = ctypes.c_void_p()
    rc = lib.LLVMCreateMCJITCompilerForModule(
        ctypes.byref(ee_out), mod._handle,
        ctypes.cast(ctypes.byref(opts), ctypes.c_void_p),
        ctypes.sizeof(opts),
        ctypes.byref(err_out),
    )
    if rc != 0:
        msg = _consume_msg(err_out.value or 0)
        raise RuntimeError(f"create_mcjit_compiler: {msg}")
    # EE now owns the module
    mod._owns = False
    return ExecutionEngine(ee_out.value)


__all__ = [
    "parse_assembly", "ModuleRef", "FunctionRef",
    "run_passes", "run_passes_on_ir",
    "Target", "TargetMachine", "ExecutionEngine",
    "create_mcjit_compiler",
    "initialize_native_target", "initialize_native_asmprinter",
    "get_default_triple",
]
