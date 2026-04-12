"""Layer-1/2 (typed, mostly-native) LLVM IR codegen for pcc_py.

Covers the Phase 1 MVP scope plus Phase 2 (L2) container / string /
None support from ``docs/plans/python-frontend-plan.md``.

Phase 1 (native-only) constructs:

* ``def`` with typed parameters and return.
* Scalar arithmetic on ``int`` (i64), ``float`` (double), ``bool`` (i1),
  including Python-correct ``//`` (floor) and ``%`` (sign follows
  divisor) on signed integers, and Python ``/`` (int / int → float).
* ``if / elif / else``, ``while``, ``for i in range(...)`` (the range
  literal is typed and lowered to an i64 while-counter).
* Local variables via entry-block ``alloca`` + ``store`` + ``load``.
* Function calls (including recursion).
* ``return``.

Phase 2 (typed objects via runtime lib) constructs:

* String literals (stored as UTF-8 globals, wrapped via ``py_str_new``).
* ``None`` literal (reference to runtime global ``py_None``).
* List / dict / tuple literals with native→PyObject* marshalling per
  element.
* Subscript read/write on list / dict / tuple / str (runtime dispatch).
* ``in`` / ``not in`` on str / list / dict.
* ``x is None`` / ``x is not None`` (pointer comparison against
  ``py_None``).
* String concatenation and repetition via ``py_str_concat`` /
  ``py_str_repeat``.
* ``len(x)`` dispatched to ``py_list_len`` / ``py_str_len`` /
  ``py_dict_len`` / ``py_tuple_len`` as appropriate.
* ``print(...)`` on str / list / dict / tuple via ``py_print``, and
  multi-arg print via ``py_print_many`` (arguments marshalled into a
  tuple PyObject first).

Anything outside the above raises :class:`NotImplementedError` with a
message naming the offending AST node — that's the signal for Layer 3
to pick it up.

Layer discipline per-expression: an expression is "L1" iff its type and
every operand's type is a native-mapping scalar (IntType / FloatType /
BoolType). Otherwise it becomes "L2" and goes through the runtime lib
with marshalling at boundaries.
"""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Assign,
    Attr,
    AugAssign,
    BinOp,
    BoolExpr,
    BoolLit,
    BoolType,
    Break,
    Call,
    Compare,
    Continue,
    DictExpr,
    DictType,
    DynType,
    Expr,
    ExprStmt,
    FloatLit,
    FloatType,
    For,
    FuncDef,
    If,
    IfExpr,
    IntLit,
    IntType,
    ListExpr,
    ListType,
    Module,
    Name,
    NoneLit,
    NoneType,
    Delete,
    Pass,
    Return,
    Slice,
    Import,
    ImportFrom,
    Raise,
    Stmt,
    StrLit,
    StrType,
    Subscript,
    Try,
    With,
    TupleExpr,
    TupleType,
    Type,
    UnaryOp,
    While,
)
from . import marshal
from .runtime_abi import declare_runtime, declare_runtime_global


# -- Canonical IR types ------------------------------------------------------

_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()
_VOID = ir.VoidType()
_CSTR = _I8.as_pointer()


def _zero_initializer_for(ir_ty):
    if isinstance(ir_ty, ir.IntType):
        return 0
    if isinstance(ir_ty, (ir.FloatType, ir.DoubleType)):
        return 0.0
    if isinstance(ir_ty, ir.PointerType):
        return None
    return 0


class L1CodegenError(Exception):
    """Raised when L1 cannot handle an AST shape it should have.

    Distinct from :class:`NotImplementedError` — which means "this is a
    later-phase feature that belongs in L2/L3" — in that L1CodegenError
    indicates a malformed AST or an internal invariant violation.
    """


class L1CodeGen:
    """Layer-1 code generator: typed pcc_py AST → native LLVM IR.

    Construct with a parsed and type-inferred :class:`ast.Module`, then
    call :meth:`generate` to get the module text. Re-invocations on the
    same instance are not supported; build a fresh generator per call
    for clarity.
    """

    # ---------------------------------------------------------------- init

    def __init__(self, module: Module):
        self.ast_module = module
        self.module = ir.Module(name=module.name or "pcc_py_module")
        # target triple/layout are intentionally left empty so the
        # caller (pcc.py CLI) can set them based on the active cross-
        # compile target.
        self.runtime: dict[str, ir.Function] = declare_runtime(self.module)

        # Printf declaration for L1 print().
        self._printf = self._declare_printf()

        # Map from user function name -> ir.Function (filled during
        # the declaration pass before we emit bodies).
        self.functions: dict[str, ir.Function] = {}

        # Per-function state, reset when entering a new FuncDef:
        self.builder: Optional[ir.IRBuilder] = None
        self.current_function: Optional[ir.Function] = None
        self.current_func_def: Optional[FuncDef] = None
        # ident -> (alloca ptr, ir.Type, pcc_py Type)
        self.env: dict[str, tuple[ir.AllocaInstr, ir.Type, Type]] = {}
        # ident -> class name (when a local was last assigned a value
        # that we could statically identify as a known class instance).
        # Used by :meth:`_emit_method_call` to pick the right method
        # when type inference collapsed the variable to ``DynType``.
        self.env_class_hint: dict[str, str] = {}
        # Loop stack of (continue_block, break_block) for break/continue.
        self.loop_stack: list[tuple[ir.Block, ir.Block]] = []

        # Cached format-string globals for printf.
        self._fmt_int: Optional[ir.GlobalVariable] = None
        self._fmt_float: Optional[ir.GlobalVariable] = None
        self._fmt_bool_true: Optional[ir.GlobalVariable] = None
        self._fmt_bool_false: Optional[ir.GlobalVariable] = None

        # De-duplicated pool of ``.pystr.<N>`` byte arrays and attribute
        # name C-strings for ``py_obj_getattr``.
        self._str_pool: dict[str, ir.GlobalVariable] = {}
        self._attr_pool: dict[str, ir.GlobalVariable] = {}
        self._str_counter = 0

        self._tmp_counter = 0

    # ---------------------------------------------------------------- API

    def generate(self, module: Optional[Module] = None) -> str:
        """Lower the AST module to an LLVM IR text blob.

        ``module`` may be supplied to override the one given to the
        constructor, matching the task contract.
        """
        if module is not None:
            self.ast_module = module
            self.module = ir.Module(name=module.name or "pcc_py_module")
            self.runtime = declare_runtime(self.module)
            self._printf = self._declare_printf()
            self.functions = {}
            self._fmt_int = None
            self._fmt_float = None
            self._fmt_bool_true = None
            self._fmt_bool_false = None
            self._str_pool = {}
            self._attr_pool = {}
            self._str_counter = 0

        # Lazy-import the class lowering helper — it lives in a sibling
        # module that imports :mod:`.layer1` transitively through
        # ``runtime_abi`` (no hard cycle but let's keep startup cheap).
        from .class_gen import ClassLowering
        from ..py_ast import (
            ClassDef as _ClassDef,
            ExprStmt as _ExprStmt,
            Import as _TopImport,
            ImportFrom as _TopImportFrom,
        )

        self.class_lowering = ClassLowering(self)

        # Partition module-level statements into (def-shaped,
        # statement-body). Anything that isn't a FuncDef/ClassDef is
        # queued into the synthesized module-main body so that
        # ``main()`` at file scope still runs at program start.
        main_body: list[Stmt] = []

        for stmt in self.ast_module.body:
            if isinstance(stmt, FuncDef):
                self._declare_user_function(stmt)
            elif isinstance(stmt, _ClassDef):
                self.class_lowering.declare_class(stmt)
            elif isinstance(stmt, _TopImport):
                for mod_name, as_name in stmt.names:
                    # Match _emit_import's binding convention: bind
                    # the top-level for ``import a.b`` (no alias) so
                    # ``a.b.c`` lookups via getattr succeed.
                    if as_name is None and "." in mod_name:
                        local_name = mod_name.split(".")[0]
                    else:
                        local_name = as_name or mod_name
                    self._cpy_module_global(local_name)
                    self._cpy_modules()[local_name] = (
                        self._cpy_module_global(local_name)
                    )
                main_body.append(stmt)
            elif isinstance(stmt, _TopImportFrom):
                # Compile-time scaffold imports (pcc.extern / pcc.llvm_capi)
                # carry no runtime CPython globals — their names are
                # consumed by codegen during the emit pass. Seed the
                # binding set now so extern decls that follow (and
                # extern calls in user functions) see them.
                if stmt.module in self._EXTERN_SCAFFOLD_MODULES:
                    self._register_extern_scaffold_imports(stmt)
                else:
                    # Multi-file compile: pre-register native sibling
                    # imports in the first pass so user function bodies
                    # emitted immediately after see the extern binding.
                    # The regular CPython-backed side-effect (allocating
                    # a module global) is skipped for native siblings.
                    native_table = getattr(
                        self, "_native_module_exports", None,
                    )
                    resolved = (
                        self._resolve_relative_import(stmt)
                        if native_table is not None else None
                    )
                    if (
                        native_table is not None
                        and resolved in native_table
                    ):
                        self._predeclare_native_cross_module(
                            stmt, resolved, native_table[resolved],
                        )
                    else:
                        for attr_name, as_name in stmt.names:
                            local_name = as_name or attr_name
                            self._cpy_module_global(local_name)
                            self._cpy_modules()[local_name] = (
                                self._cpy_module_global(local_name)
                            )
                main_body.append(stmt)
            elif (
                isinstance(stmt, Assign)
                and self._maybe_register_extern_assign(stmt)
            ):
                # Pre-register extern("symbol", ...) decls during the
                # declare pass so user-function bodies emitted next can
                # resolve the extern callable. Do NOT append to
                # main_body — nothing runtime to emit.
                pass
            elif isinstance(stmt, (_ExprStmt, Assign, AugAssign, If, While, For)):
                # Top-level statements that belong in the synthetic
                # module-main function so they execute at program
                # start. Top-level ``Name = <expr>`` also declares a
                # module-level global so other functions can read it.
                if isinstance(stmt, Assign):
                    self._declare_module_globals_for(stmt)
                main_body.append(stmt)
            else:
                raise NotImplementedError(
                    "Layer 1 only supports top-level FuncDef / ClassDef / "
                    f"Import / Assign / AugAssign / ExprStmt / If / While / "
                    f"For at module scope; got {type(stmt).__name__}"
                )

        for stmt in self.ast_module.body:
            if isinstance(stmt, FuncDef):
                self._emit_user_function(stmt)
            elif isinstance(stmt, _ClassDef):
                self.class_lowering.emit_methods(stmt)

        self.class_lowering.emit_module_init()
        # Multi-file compile mode: non-entry modules emit a
        # ``_pcc_py_module_top_<mod>()`` initialiser instead of the
        # program entry ``@main``. The entry module's @main is
        # responsible for calling each other module's top-level init
        # before its own body runs.
        if getattr(self, "_skip_program_main", False):
            self._emit_module_top_init(main_body)
        else:
            self._emit_program_main(main_body)

        return str(self.module)

    def _declare_module_globals_for(self, stmt: Assign) -> None:
        """Allocate a module-level global for each simple Name target of
        a module-scope assignment so user functions can later load
        the same binding."""
        if not hasattr(self, "_module_globals"):
            self._module_globals: dict[str, tuple[ir.GlobalVariable, Type]] = {}
        target_ty = stmt.annotation if stmt.annotation is not None \
            else stmt.value.ty
        for t in stmt.targets:
            if not isinstance(t, Name):
                continue
            if t.ident in self._module_globals:
                continue
            if not (
                self._is_scalar(target_ty) or self._is_object(target_ty)
            ):
                continue
            ir_ty = self._map_type(target_ty)
            gv = ir.GlobalVariable(
                self.module, ir_ty, name=f".modvar.{t.ident}",
            )
            gv.linkage = "internal"
            gv.initializer = ir.Constant(ir_ty, _zero_initializer_for(ir_ty))
            self._module_globals[t.ident] = (gv, target_ty)

    def _emit_module_top_init(self, body: list["Stmt"]) -> None:
        """Emit ``void _pcc_py_module_top_<mod>()`` holding the
        module-level statements. Used when this compilation unit is a
        secondary module in a multi-file compile — the entry module's
        ``@main`` must call this before its own top-level body."""
        mod_name = self.ast_module.name or "mod"
        sanitised = mod_name.replace(".", "_").replace("-", "_")
        fnty = ir.FunctionType(_VOID, [])
        fn = ir.Function(
            self.module, fnty, name=f"_pcc_py_module_top_{sanitised}",
        )
        fn.linkage = "external"
        entry = fn.append_basic_block("entry")
        saved_builder = self.builder
        saved_fn = self.current_function
        saved_fd = self.current_func_def
        saved_env = self.env
        saved_loops = self.loop_stack
        self.builder = ir.IRBuilder(entry)
        self.current_function = fn
        self.current_func_def = None
        self.env = {}
        self.loop_stack = []

        if self.class_lowering.classes:
            init_name = f"_pcc_py_module_init_{sanitised}"
            init_fn = self.module.globals.get(init_name)
            if isinstance(init_fn, ir.Function):
                self.builder.call(init_fn, [])

        self._emit_stmts(tuple(body))

        if not self.builder.block.is_terminated:
            self.builder.ret_void()

        self.builder = saved_builder
        self.current_function = saved_fn
        self.current_func_def = saved_fd
        self.env = saved_env
        self.loop_stack = saved_loops

    def _emit_program_main(self, body: list["Stmt"]) -> None:
        """Synthesize ``i32 @main()`` holding module-level statements.

        Runs the ``_pcc_py_module_init_<mod>`` ctor first (populates
        class globals) and then emits each queued module-level
        statement. Returns 0.
        """
        if self.module.globals.get("main") is not None:
            # User provided a C-style ``main`` function already; leave
            # it alone. This is a pcc-py convention for hand-written
            # entry points.
            return

        fnty = ir.FunctionType(_I32, [])
        fn = ir.Function(self.module, fnty, name="main")
        entry = fn.append_basic_block("entry")
        saved_builder = self.builder
        saved_fn = self.current_function
        saved_fd = self.current_func_def
        saved_env = self.env
        saved_loops = self.loop_stack
        self.builder = ir.IRBuilder(entry)
        self.current_function = fn
        self.current_func_def = None
        self.env = {}
        self.loop_stack = []

        # Call other-module top-inits first (multi-file compile).
        # Each declared-external void function executes the sibling
        # module's class init + top-level statements.
        for sibling_mod in getattr(self, "_sibling_module_inits", ()):
            sanitised_sib = sibling_mod.replace(".", "_").replace("-", "_")
            sib_top = f"_pcc_py_module_top_{sanitised_sib}"
            existing = self.module.globals.get(sib_top)
            if existing is None:
                sib_fn = ir.Function(
                    self.module, ir.FunctionType(_VOID, []), name=sib_top,
                )
                sib_fn.linkage = "external"
            else:
                sib_fn = existing
            self.builder.call(sib_fn, [])

        # Call module init (populates class globals) if any classes
        # were lowered.
        if self.class_lowering.classes:
            mod_name = self.ast_module.name or "mod"
            sanitised_mod = mod_name.replace(".", "_").replace("-", "_")
            init_name = f"_pcc_py_module_init_{sanitised_mod}"
            init_fn = self.module.globals.get(init_name)
            if isinstance(init_fn, ir.Function):
                self.builder.call(init_fn, [])

        self._emit_stmts(tuple(body))

        if not self.builder.block.is_terminated:
            self.builder.ret(ir.Constant(_I32, 0))

        self.builder = saved_builder
        self.current_function = saved_fn
        self.current_func_def = saved_fd
        self.env = saved_env
        self.loop_stack = saved_loops

    # ---------------------------------------------------------------- helpers

    def _fresh(self, hint: str = "t") -> str:
        self._tmp_counter += 1
        return f"{hint}.{self._tmp_counter}"

    def _declare_printf(self) -> ir.Function:
        existing = self.module.globals.get("printf")
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_I32, [_CSTR], var_arg=True)
        fn = ir.Function(self.module, fnty, name="printf")
        fn.linkage = "external"
        return fn

    # -- type mapping --------------------------------------------------

    def _map_type(self, ty: Type) -> ir.Type:
        """Map a pcc_py :class:`Type` to its LLVM IR representation.

        Phase 1 scalars lower to native types; Phase 2 object types
        (str / list / dict / tuple / None) lower to ``PyObject*`` (an
        opaque pointer).
        """
        if isinstance(ty, IntType):
            # We always lower to i64 in L1 regardless of the declared
            # width; the type-infer layer is expected to have
            # range-checked narrower widths already. The ``width`` field
            # will matter once tagged-int codegen lands in Phase 2.
            return _I64
        if isinstance(ty, FloatType):
            return _DOUBLE
        if isinstance(ty, BoolType):
            return _I1
        if isinstance(ty, (StrType, ListType, DictType, TupleType)):
            return _CSTR  # alias for i8* == PyObject*
        if isinstance(ty, NoneType):
            # None is a PyObject* (points to the global ``py_None``).
            # Using a pointer (not void) lets us store and load None in
            # locals uniformly with other object types.
            return _CSTR
        if isinstance(ty, DynType):
            # A generic PyObject* slot: covers class instances, results
            # of ``MyClass(args)`` construction, attribute fetches, and
            # anything else the type inferer did not narrow.
            return _CSTR
        raise NotImplementedError(
            f"Layer 1 does not handle type {type(ty).__name__} "
            f"(name={getattr(ty, 'name', '?')!r})"
        )

    def _is_scalar(self, ty: Type) -> bool:
        return isinstance(ty, (IntType, FloatType, BoolType))

    def _is_object(self, ty: Type) -> bool:
        return isinstance(
            ty,
            (StrType, ListType, DictType, TupleType, NoneType, DynType),
        )

    # -- string / attribute name globals -------------------------------

    def _cstr_literal(self, payload: str) -> tuple[ir.GlobalVariable, int]:
        """Intern a UTF-8 byte array as an internal global.

        Returns ``(gv, byte_len)`` where ``byte_len`` excludes the
        trailing NUL. Emitted globals are named ``.pystr.<N>`` per the
        L2 convention in the task brief.
        """
        data = payload.encode("utf-8")
        existing = self._str_pool.get(payload)
        if existing is not None:
            # Array length minus the NUL terminator.
            arr_ty = existing.type.pointee
            return existing, arr_ty.count - 1
        self._str_counter += 1
        name = f".pystr.{self._str_counter}"
        body = bytearray(data + b"\x00")
        arr_ty = ir.ArrayType(_I8, len(body))
        gv = ir.GlobalVariable(self.module, arr_ty, name=name)
        gv.linkage = "internal"
        gv.global_constant = True
        gv.initializer = ir.Constant(arr_ty, body)
        self._str_pool[payload] = gv
        return gv, len(data)

    def _attr_name_ptr(self, name: str) -> ir.Value:
        """Return an i8* pointing at a NUL-terminated attribute name.

        These globals are short-lived (attribute-access use only) and
        intentionally distinct from :meth:`_cstr_literal` so a later
        optimiser can fold them if it wishes.
        """
        existing = self._attr_pool.get(name)
        if existing is None:
            data = bytearray(name.encode("utf-8") + b"\x00")
            arr_ty = ir.ArrayType(_I8, len(data))
            sym = f".pyattr.{name}"
            # Multiple distinct attrs may share a name; disambiguate.
            if sym in self.module.globals:
                sym = f".pyattr.{name}.{len(self._attr_pool)}"
            gv = ir.GlobalVariable(self.module, arr_ty, name=sym)
            gv.linkage = "internal"
            gv.global_constant = True
            gv.initializer = ir.Constant(arr_ty, data)
            self._attr_pool[name] = gv
            existing = gv
        zero = ir.Constant(_I32, 0)
        return self.builder.gep(existing, [zero, zero], inbounds=True)

    def _emit_str_literal(self, value: str) -> ir.Value:
        """Emit ``py_str_new(ptr, byte_len)`` for a string literal."""
        gv, byte_len = self._cstr_literal(value)
        zero = ir.Constant(_I32, 0)
        ptr = self.builder.gep(gv, [zero, zero], inbounds=True,
                                 name=self._fresh("pystr.ptr"))
        length = ir.Constant(_I64, byte_len)
        return self.builder.call(
            self.runtime["py_str_new"], [ptr, length],
            name=self._fresh("str.new"),
        )

    def _emit_none_literal(self) -> ir.Value:
        """Load the runtime ``py_None`` const pointer."""
        gv = declare_runtime_global(self.module, "py_None")
        return self.builder.load(gv, name=self._fresh("none"))

    # -- format-string globals ----------------------------------------

    def _cstr_global(self, payload: str, name: str) -> ir.GlobalVariable:
        data = bytearray(payload.encode("utf-8") + b"\x00")
        arr_ty = ir.ArrayType(_I8, len(data))
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.GlobalVariable):
            return existing
        gv = ir.GlobalVariable(self.module, arr_ty, name=name)
        gv.linkage = "internal"
        gv.global_constant = True
        gv.initializer = ir.Constant(arr_ty, data)
        return gv

    def _ptr_to_cstr(self, gv: ir.GlobalVariable) -> ir.Value:
        zero = ir.Constant(_I32, 0)
        return self.builder.gep(gv, [zero, zero], inbounds=True)

    def _get_fmt_int(self) -> ir.GlobalVariable:
        if self._fmt_int is None:
            self._fmt_int = self._cstr_global("%ld\n", ".fmt_int")
        return self._fmt_int

    def _get_fmt_float(self) -> ir.GlobalVariable:
        if self._fmt_float is None:
            # Use %g for a Python-ish default; this is NOT bit-for-bit
            # Python's repr and will be upgraded in Phase 2 when the
            # runtime lib is wired in for repr.
            self._fmt_float = self._cstr_global("%g\n", ".fmt_float")
        return self._fmt_float

    def _get_fmt_bool_true(self) -> ir.GlobalVariable:
        if self._fmt_bool_true is None:
            self._fmt_bool_true = self._cstr_global("True\n", ".fmt_true")
        return self._fmt_bool_true

    def _get_fmt_bool_false(self) -> ir.GlobalVariable:
        if self._fmt_bool_false is None:
            self._fmt_bool_false = self._cstr_global("False\n", ".fmt_false")
        return self._fmt_bool_false

    # -- user-function declaration / definition -----------------------

    def _user_symbol(self, name: str) -> str:
        """Mangled LLVM symbol for a user function.

        Uses the ``user_<module>_<name>`` convention from
        Section 4 of the interface contract.
        """
        mod_name = self.ast_module.name or "mod"
        # Normalise dotted module names so the mangled symbol is a
        # valid LLVM identifier (dots in LLVM identifiers work when
        # quoted but read oddly).
        sanitized = mod_name.replace(".", "_").replace("-", "_")
        return f"user_{sanitized}_{name}"

    def _declare_user_function(self, fd: FuncDef) -> None:
        if fd.is_async:
            raise NotImplementedError(
                "Layer 1 does not handle async def; received "
                f"{fd.name!r}"
            )
        if fd.decorators:
            unrecognised = [
                d for d in fd.decorators
                if not self._decorator_is_noop_whitelist(d)
            ]
            if unrecognised:
                raise NotImplementedError(
                    "Layer 1 does not handle decorators; received "
                    f"{len(fd.decorators)} on {fd.name!r} "
                    f"(first unrecognised: "
                    f"{self._decorator_repr(unrecognised[0])})"
                )

        param_types: list[ir.Type] = []
        for arg in fd.args:
            if arg.kind not in ("pos", "pos_only", "kw_only"):
                raise NotImplementedError(
                    f"Layer 1 parameter kind {arg.kind!r} "
                    f"(in function {fd.name!r}) not supported "
                    "(*args/**kwargs need L3)"
                )
            if arg.annotation is None:
                raise L1CodegenError(
                    f"Layer 1 requires an annotation on parameter "
                    f"{arg.name!r} of function {fd.name!r}"
                )
            param_types.append(self._map_type(arg.annotation))

        if fd.return_ty is None or isinstance(fd.return_ty, NoneType):
            # ``-> None`` maps to ``ret void`` — bare ``return`` works
            # without materialising the py_None global.
            ret_ty = _VOID
        else:
            ret_ty = self._map_type(fd.return_ty)

        fnty = ir.FunctionType(ret_ty, param_types, var_arg=False)
        sym = self._user_symbol(fd.name)
        existing = self.module.globals.get(sym)
        if isinstance(existing, ir.Function):
            fn = existing
        else:
            fn = ir.Function(self.module, fnty, name=sym)
            fn.linkage = "external"
        for ir_arg, ast_arg in zip(fn.args, fd.args):
            ir_arg.name = ast_arg.name
        self.functions[fd.name] = fn

    def _emit_user_function(self, fd: FuncDef) -> None:
        fn = self.functions[fd.name]
        self.current_function = fn
        self.current_func_def = fd

        entry = fn.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(entry)
        self.env = {}
        self.env_class_hint = {}
        self.loop_stack = []

        # Promote each incoming argument to an entry-block alloca so
        # assignments within the function body are uniform.
        for ir_arg, ast_arg in zip(fn.args, fd.args):
            ir_ty = ir_arg.type
            slot = self.builder.alloca(ir_ty, name=f"{ast_arg.name}.addr")
            self.builder.store(ir_arg, slot)
            self.env[ast_arg.name] = (slot, ir_ty, ast_arg.annotation)

        # Emit body.
        self._emit_stmts(fd.body)

        # If the terminator is missing (body fell through), insert a
        # default return. For void, ``ret void``. For typed returns
        # this is a bug in the user program, but we emit a zero-value
        # return to keep the IR well-formed — the type checker is
        # supposed to have rejected it already.
        if not self.builder.block.is_terminated:
            if isinstance(fn.function_type.return_type, ir.VoidType):
                self.builder.ret_void()
            else:
                self.builder.ret(self._zero_of(fn.function_type.return_type))

        self.builder = None
        self.current_function = None
        self.current_func_def = None
        self.env = {}
        self.loop_stack = []

    def _zero_of(self, ir_ty: ir.Type) -> ir.Value:
        if isinstance(ir_ty, ir.IntType):
            return ir.Constant(ir_ty, 0)
        if isinstance(ir_ty, (ir.FloatType, ir.DoubleType)):
            return ir.Constant(ir_ty, 0.0)
        if isinstance(ir_ty, ir.PointerType):
            # NULL pointer — used as a safe fall-through return for
            # object-typed functions.
            return ir.Constant(ir_ty, None)
        raise L1CodegenError(f"no zero value for type {ir_ty}")

    # ------------------------------------------------------- statements

    def _emit_stmts(self, stmts: tuple[Stmt, ...]) -> None:
        for stmt in stmts:
            if self.builder.block.is_terminated:
                # Dead code after a return/raise — silently drop.
                return
            self._emit_stmt(stmt)

    def _emit_stmt(self, stmt: Stmt) -> None:
        if isinstance(stmt, Pass):
            return
        if isinstance(stmt, Return):
            self._emit_return(stmt)
            return
        if isinstance(stmt, Assign):
            self._emit_assign(stmt)
            return
        if isinstance(stmt, AugAssign):
            self._emit_augassign(stmt)
            return
        if isinstance(stmt, ExprStmt):
            self._emit_expr_stmt(stmt)
            return
        if isinstance(stmt, Raise):
            self._emit_raise(stmt)
            return
        if isinstance(stmt, Try):
            self._emit_try(stmt)
            return
        if isinstance(stmt, With):
            self._emit_with(stmt)
            return
        if isinstance(stmt, Import):
            self._emit_import(stmt)
            return
        if isinstance(stmt, ImportFrom):
            self._emit_import_from(stmt)
            return
        if isinstance(stmt, If):
            self._emit_if(stmt)
            return
        if isinstance(stmt, While):
            self._emit_while(stmt)
            return
        if isinstance(stmt, For):
            self._emit_for(stmt)
            return
        if isinstance(stmt, Break):
            if not self.loop_stack:
                raise L1CodegenError("break outside loop")
            _, break_bb = self.loop_stack[-1]
            self.builder.branch(break_bb)
            return
        if isinstance(stmt, Continue):
            if not self.loop_stack:
                raise L1CodegenError("continue outside loop")
            cont_bb, _ = self.loop_stack[-1]
            self.builder.branch(cont_bb)
            return
        if isinstance(stmt, Delete):
            self._emit_delete(stmt)
            return
        raise NotImplementedError(
            f"Layer 1 does not handle statement {type(stmt).__name__}"
        )

    def _emit_delete(self, stmt: Delete) -> None:
        """Lower ``del x`` / ``del d[k]`` / ``del xs[i]`` — the
        surface covers only what pcc itself uses. Name targets
        become a compile-time binding drop (no runtime IR) since
        pcc doesn't reuse the slot for a different type post-del.
        Subscript targets dispatch on container type."""
        for target in stmt.targets:
            if isinstance(target, Name):
                # Drop the env / cpy-flag entry so future reads
                # surface an unbound-name error. The alloca stays
                # (LLVM drops it through SSA). This is coarser than
                # Python but matches the bootstrap's usage pattern:
                # ``del tmp`` to release a large intermediate value.
                self.env.pop(target.ident, None)
                if hasattr(self, "_cpy_env_flags"):
                    self._cpy_env_flags.pop(target.ident, None)
                if hasattr(self, "env_class_hint"):
                    self.env_class_hint.pop(target.ident, None)
                continue
            if isinstance(target, Subscript):
                obj = self._emit_expr(target.obj)
                obj_ty = target.obj.ty
                idx_val = self._emit_expr(target.idx)
                idx_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    idx_val, target.idx.ty,
                )
                if isinstance(obj_ty, DictType):
                    self.builder.call(
                        self.runtime["py_dict_del"], [obj, idx_obj],
                    )
                    continue
                raise NotImplementedError(
                    f"Layer 1 'del' on subscript with container type "
                    f"{type(obj_ty).__name__} not yet wired"
                )
            raise NotImplementedError(
                f"Layer 1 'del' on {type(target).__name__} target "
                "not supported"
            )

    # -- Return --------------------------------------------------------

    def _emit_return(self, stmt: Return) -> None:
        fn = self.current_function
        ret_ty = fn.function_type.return_type
        if stmt.value is None:
            if isinstance(ret_ty, ir.VoidType):
                self.builder.ret_void()
                return
            # Python lets you ``return`` (bare) from a function
            # annotated with a non-None return type — it returns None
            # at runtime, which pcc can satisfy with a zero / NULL
            # value matching the declared IR type. Match that
            # behaviour so pcc sources that have early bare returns
            # don't hard-fail.
            if isinstance(ret_ty, ir.PointerType):
                self.builder.ret(ir.Constant(ret_ty, None))
            elif isinstance(ret_ty, ir.IntType):
                self.builder.ret(ir.Constant(ret_ty, 0))
            elif isinstance(ret_ty, (ir.FloatType, ir.DoubleType)):
                self.builder.ret(ir.Constant(ret_ty, 0.0))
            else:
                raise L1CodegenError(
                    f"bare 'return' fallback can't zero-init {ret_ty}"
                )
            return
        # ``return None`` where the function is declared to return None —
        # evaluate the expression for side-effects but emit ``ret void``.
        if isinstance(ret_ty, ir.VoidType):
            # Evaluate the expression for any side-effects even though
            # the value is discarded. NoneLit has none, so this is cheap.
            self._emit_expr(stmt.value)
            self.builder.ret_void()
            return
        value = self._emit_expr(stmt.value)
        value = self._coerce(value, stmt.value.ty, self.current_func_def.return_ty)
        self.builder.ret(value)

    # -- Assignment ----------------------------------------------------

    def _emit_assign(self, stmt: Assign) -> None:
        if len(stmt.targets) != 1:
            raise NotImplementedError(
                "Layer 1 does not handle tuple-unpacking assignment"
            )
        target = stmt.targets[0]

        # Tuple-unpacking assignment: ``a, b = x, y`` where the RHS is a
        # matching TupleExpr literal. Lower to a sequence of plain
        # assignments; Python semantics require that the whole RHS be
        # evaluated before any LHS is bound, which we mimic by emitting
        # every RHS into an SSA value first and only then storing.
        if isinstance(target, TupleExpr):
            return self._emit_tuple_unpack_assign(stmt, target)

        # Subscript target: ``lst[i] = v`` / ``d[k] = v``.
        if isinstance(target, Subscript):
            self._emit_subscript_store(target, stmt.value)
            return

        # Attribute target: currently only ``self.<attr> = value`` inside
        # a method body. Delegates to the class lowering helper which
        # uses the per-class field layout when known and falls back to
        # ``py_obj_setattr`` otherwise.
        if isinstance(target, Attr):
            self._emit_attr_store(target, stmt.value)
            return

        if not isinstance(target, Name):
            raise NotImplementedError(
                f"Layer 1/2 assignment target must be Name or Subscript; got "
                f"{type(target).__name__}"
            )

        # ``my_fn = extern("symbol", ...)`` — pcc.extern scaffold
        # declaration. No runtime IR emitted; just record the decl.
        if self._maybe_register_extern_assign(stmt):
            return

        # Track class hint for ``p = MyClass(args)`` so that ``p.method()``
        # can dispatch to ``MyClass``'s method even when type inference
        # labels ``p`` as ``DynType``.
        if isinstance(stmt.value, Call) and isinstance(stmt.value.func, Name):
            callee = stmt.value.func.ident
            if (
                hasattr(self, "class_lowering")
                and callee in self.class_lowering.classes
            ):
                self.env_class_hint[target.ident] = callee
            else:
                self.env_class_hint.pop(target.ident, None)
        else:
            # Any other RHS invalidates the class hint.
            self.env_class_hint.pop(target.ident, None)

        value = self._emit_expr(stmt.value)

        # Track "this local holds a CPython PyObject*" so subsequent
        # loads of the variable keep the tag, letting _to_int64 /
        # print / compare dispatch via the libpython helpers.
        if not hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags = {}
        if value in getattr(self, "_cpy_values", ()):
            self._cpy_env_flags[target.ident] = True
        else:
            self._cpy_env_flags.pop(target.ident, None)

        # If this is a module-level global (seeded in the first pass),
        # write into the module variable and skip the local alloca
        # path. Guard on being inside the synthetic ``main`` body —
        # user-defined functions may still shadow with a local of the
        # same name, which is what the env fallback below handles.
        module_globals = getattr(self, "_module_globals", None)
        if (
            module_globals is not None
            and target.ident in module_globals
            and self.current_func_def is None
        ):
            gv, declared_ty = module_globals[target.ident]
            value = self._coerce(value, stmt.value.ty, declared_ty)
            self.builder.store(value, gv)
            return

        slot = self.env.get(target.ident)
        if slot is None:
            # First assignment — allocate.
            target_ty = stmt.annotation if stmt.annotation is not None else target.ty
            if not (self._is_scalar(target_ty) or self._is_object(target_ty)):
                raise NotImplementedError(
                    f"Layer 1/2 cannot allocate variable "
                    f"{target.ident!r} of type {type(target_ty).__name__}"
                )
            ir_ty = self._map_type(target_ty)
            alloca = self._alloca_in_entry(ir_ty, name=f"{target.ident}.addr")
            self.env[target.ident] = (alloca, ir_ty, target_ty)
            slot = self.env[target.ident]

        alloca, ir_ty, declared_ty = slot
        value = self._coerce(value, stmt.value.ty, declared_ty)
        self.builder.store(value, alloca)

    def _emit_tuple_unpack_assign(
        self, stmt: Assign, target: TupleExpr,
    ) -> None:
        """Lower ``a, b = <rhs>`` into pair-wise name/subscript/attr
        assigns.

        Two RHS shapes are handled:

        * ``TupleExpr`` literal — each elem evaluated in source order,
          then bound into the corresponding target.
        * Any expression whose inferred type is ``TupleType`` with the
          correct arity — value is evaluated once, then each target is
          assigned from ``py_tuple_get(result, i)`` marshaled back to
          the declared element type.

        Anything else (e.g. list RHS, unknown iterable) remains
        unsupported.
        """
        rhs = stmt.value
        if isinstance(rhs, TupleExpr):
            if len(rhs.elems) != len(target.elems):
                raise L1CodegenError(
                    f"tuple unpack arity mismatch: {len(target.elems)} "
                    f"targets, {len(rhs.elems)} values"
                )
            rhs_vals: list = []
            for e in rhs.elems:
                rhs_vals.append((self._emit_expr(e), e.ty))
            for lhs, (val, val_ty) in zip(target.elems, rhs_vals):
                self._store_unpack_target(lhs, val, val_ty)
            return

        rhs_ty = rhs.ty
        if isinstance(rhs_ty, TupleType) and len(rhs_ty.elems) == len(target.elems):
            tup_val = self._emit_expr(rhs)
            tup_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, tup_val, rhs_ty,
            )
            for i, (lhs, elem_ty) in enumerate(zip(target.elems, rhs_ty.elems)):
                idx_val = ir.Constant(_I64, i)
                elem_obj = self.builder.call(
                    self.runtime["py_tuple_get"], [tup_obj, idx_val],
                    name=self._fresh(f"tup.{i}"),
                )
                # Marshal the PyObject* back to the declared element
                # type so downstream stores see a native value when
                # possible.
                native_val = elem_obj
                if not isinstance(elem_ty, DynType):
                    native_val = marshal.marshal_from_object(
                        self.builder, self.module, self.runtime,
                        elem_obj, elem_ty,
                    )
                self._store_unpack_target(lhs, native_val, elem_ty)
            return

        # DynType RHS: assume runtime tuple (or any ``py_obj_getitem``
        # -friendly container). Index each slot via
        # ``py_obj_getitem(tup, int_box)``. Element types are unknown
        # so each target is bound as DynType.
        if isinstance(rhs_ty, DynType):
            tup_val = self._emit_expr(rhs)
            for i, lhs in enumerate(target.elems):
                idx_box = self.builder.call(
                    self.runtime["py_int_from_i64"],
                    [ir.Constant(_I64, i)],
                    name=self._fresh("unpack.idx.box"),
                )
                elem_obj = self.builder.call(
                    self.runtime["py_obj_getitem"], [tup_val, idx_box],
                    name=self._fresh(f"unpack.{i}"),
                )
                self._store_unpack_target(
                    lhs, elem_obj, DynType(name="dyn"),
                )
            return

        raise NotImplementedError(
            "Layer 1 tuple-unpacking supports a TupleExpr RHS or an "
            "expression whose inferred type is a concrete tuple; "
            f"got {type(rhs).__name__} of type {rhs_ty}"
        )

    def _store_unpack_target(
        self, lhs: Expr, value: ir.Value, value_ty: Type,
    ) -> None:
        if isinstance(lhs, Subscript):
            self._store_value_at_subscript(lhs, value, value_ty)
            return
        if isinstance(lhs, Attr):
            self._store_value_at_attr(lhs, value, value_ty)
            return
        if isinstance(lhs, Name):
            self._store_value_at_name(lhs, value, value_ty)
            return
        if isinstance(lhs, TupleExpr):
            # Nested unpack: ``(b, c) = value`` where ``value`` is
            # a PyObject* tuple. Each inner slot fetched via
            # py_obj_getitem so the same code works for list /
            # tuple / dyn.
            for i, sub in enumerate(lhs.elems):
                idx_box = self.builder.call(
                    self.runtime["py_int_from_i64"],
                    [ir.Constant(_I64, i)],
                    name=self._fresh("unpack.nested.idx.box"),
                )
                elem = self.builder.call(
                    self.runtime["py_obj_getitem"], [value, idx_box],
                    name=self._fresh(f"unpack.nested.{i}"),
                )
                # Slot type unknown at this layer — pass Dyn.
                self._store_unpack_target(sub, elem, DynType(name="dyn"))
            return
        raise NotImplementedError(
            f"Layer 1 tuple-unpack target kind "
            f"{type(lhs).__name__} not supported"
        )

    def _store_value_at_name(
        self, target: Name, value: ir.Value, value_ty: Type,
    ) -> None:
        """Store a pre-computed SSA value to a local / module global."""
        self.env_class_hint.pop(target.ident, None)
        if not hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags = {}
        if value in getattr(self, "_cpy_values", ()):
            self._cpy_env_flags[target.ident] = True
        else:
            self._cpy_env_flags.pop(target.ident, None)

        module_globals = getattr(self, "_module_globals", None)
        if (
            module_globals is not None
            and target.ident in module_globals
            and self.current_func_def is None
        ):
            gv, declared_ty = module_globals[target.ident]
            value = self._coerce(value, value_ty, declared_ty)
            self.builder.store(value, gv)
            return

        slot = self.env.get(target.ident)
        if slot is None:
            target_ty = target.ty
            if not (self._is_scalar(target_ty) or self._is_object(target_ty)):
                raise NotImplementedError(
                    f"Layer 1 tuple-unpack target {target.ident!r} has "
                    f"unsupported type {type(target_ty).__name__}"
                )
            ir_ty = self._map_type(target_ty)
            alloca = self._alloca_in_entry(ir_ty, name=f"{target.ident}.addr")
            self.env[target.ident] = (alloca, ir_ty, target_ty)
            slot = self.env[target.ident]

        alloca, ir_ty, declared_ty = slot
        value = self._coerce(value, value_ty, declared_ty)
        self.builder.store(value, alloca)

    def _store_value_at_subscript(
        self, target: Subscript, value: ir.Value, value_ty: Type,
    ) -> None:
        """Runtime subscript store given a pre-computed value."""
        obj = self._emit_expr(target.obj)
        idx_val = self._emit_expr(target.idx)
        v_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, value, value_ty,
        )
        k_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime,
            idx_val, target.idx.ty,
        )
        self.builder.call(
            self.runtime["py_obj_setitem"], [obj, k_obj, v_obj],
        )

    def _store_value_at_attr(
        self, target: Attr, value: ir.Value, value_ty: Type,
    ) -> None:
        """Runtime attribute store given a pre-computed value."""
        obj = self._emit_expr(target.obj)
        v_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, value, value_ty,
        )
        name_gv = self._intern_str(target.name)
        self.builder.call(
            self.runtime["py_obj_setattr"], [obj, name_gv, v_obj],
        )

    def _emit_subscript_store(self, target: Subscript, value_expr: Expr) -> None:
        obj = self._emit_expr(target.obj)
        obj_ty = target.obj.ty
        idx_expr = target.idx
        rhs = self._emit_expr(value_expr)
        # RHS always marshals to PyObject* for container storage.
        rhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, rhs, value_expr.ty
        )
        if isinstance(obj_ty, ListType):
            idx_i64 = self._emit_expr_as_i64(idx_expr)
            self.builder.call(
                self.runtime["py_list_set"], [obj, idx_i64, rhs_obj]
            )
            return
        if isinstance(obj_ty, DictType):
            key_obj = self._emit_as_object(idx_expr)
            self.builder.call(
                self.runtime["py_dict_set"], [obj, key_obj, rhs_obj]
            )
            return
        if isinstance(obj_ty, TupleType):
            raise NotImplementedError(
                "tuples are immutable — subscript-assignment not allowed"
            )
        # Dynamic fallback for anything we didn't type statically.
        key_obj = self._emit_as_object(idx_expr)
        self.builder.call(
            self.runtime["py_obj_setitem"], [obj, key_obj, rhs_obj]
        )

    def _emit_augassign(self, stmt: AugAssign) -> None:
        op_bare = stmt.op.rstrip("=")
        if isinstance(stmt.target, Name):
            slot = self.env.get(stmt.target.ident)
            if slot is None:
                raise L1CodegenError(
                    f"augassign to undefined name {stmt.target.ident!r}"
                )
            alloca, ir_ty, declared_ty = slot
            cur = self.builder.load(
                alloca, name=self._fresh(stmt.target.ident),
            )
            rhs = self._emit_expr(stmt.value)
            result = self._emit_binop_value(
                op_bare, cur, declared_ty, rhs, stmt.value.ty,
                result_ty=declared_ty,
            )
            result = self._coerce(result, declared_ty, declared_ty)
            self.builder.store(result, alloca)
            return
        if isinstance(stmt.target, Subscript):
            # ``d[k] += rhs`` → d[k] = d[k] <op> rhs
            obj_val = self._emit_expr(stmt.target.obj)
            obj_ty = stmt.target.obj.ty
            idx_val = self._emit_expr(stmt.target.idx)
            idx_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                idx_val, stmt.target.idx.ty,
            )
            obj_as_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                obj_val, obj_ty,
            )
            cur_obj = self.builder.call(
                self.runtime["py_obj_getitem"], [obj_as_obj, idx_obj],
                name=self._fresh("augassign.cur"),
            )
            rhs = self._emit_expr(stmt.value)
            rhs_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                rhs, stmt.value.ty,
            )
            result_raw = self._emit_binop_value(
                op_bare, cur_obj, DynType(name="dyn"),
                rhs_obj, DynType(name="dyn"),
                result_ty=DynType(name="dyn"),
            )
            # Box if not already a PyObject* (Dyn int binops return
            # i64).
            if result_raw.type is not _CSTR:
                result_raw = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    result_raw, IntType(name="int"),
                )
            self.builder.call(
                self.runtime["py_obj_setitem"],
                [obj_as_obj, idx_obj, result_raw],
            )
            return
        if isinstance(stmt.target, Attr):
            # ``self.x += rhs`` — load via attr, op, store back.
            target = stmt.target
            obj_val = self._emit_expr(target.obj)
            name_ptr = self._attr_name_ptr(target.name)
            cur_obj = self.builder.call(
                self.runtime["py_obj_getattr"], [obj_val, name_ptr],
                name=self._fresh("augassign.attr.cur"),
            )
            rhs = self._emit_expr(stmt.value)
            rhs_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                rhs, stmt.value.ty,
            )
            result_raw = self._emit_binop_value(
                op_bare, cur_obj, DynType(name="dyn"),
                rhs_obj, DynType(name="dyn"),
                result_ty=DynType(name="dyn"),
            )
            if result_raw.type is not _CSTR:
                result_raw = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    result_raw, IntType(name="int"),
                )
            self.builder.call(
                self.runtime["py_obj_setattr"],
                [obj_val, name_ptr, result_raw],
            )
            return
        raise NotImplementedError(
            f"Layer 1 augassign target type "
            f"{type(stmt.target).__name__} not supported"
        )

    def _alloca_in_entry(self, ir_ty: ir.Type, name: str) -> ir.AllocaInstr:
        """Emit an alloca into the function's entry block.

        Matches the pattern used by :mod:`pcc.codegen.c_codegen`:
        allocas cluster at the top so mem2reg can promote them to
        SSA during opt passes.
        """
        fn = self.current_function
        entry = fn.entry_basic_block
        cur = self.builder.block
        # Position at the end of entry, but before the first non-alloca
        # instruction if entry already has body content.
        insert_before = None
        for instr in entry.instructions:
            if instr.opname != "alloca":
                insert_before = instr
                break
        tmp_builder = ir.IRBuilder(entry)
        if insert_before is not None:
            tmp_builder.position_before(insert_before)
        else:
            tmp_builder.position_at_end(entry)
        alloca = tmp_builder.alloca(ir_ty, name=name)
        # Restore the main builder's insertion point.
        self.builder.position_at_end(cur)
        return alloca

    # -- Expression statement -----------------------------------------

    def _emit_expr_stmt(self, stmt: ExprStmt) -> None:
        # Special-case top-level ``print(...)``.
        expr = stmt.expr
        if isinstance(expr, Call) and isinstance(expr.func, Name) and expr.func.ident == "print":
            self._emit_print_call(expr)
            return
        # Otherwise evaluate for side-effects.
        self._emit_expr(expr)

    # -- Print --------------------------------------------------------

    def _emit_print_call(self, call: Call) -> None:
        if call.kwargs:
            # Phase 2 supports sep / end keyword args when every positional
            # is boxable: route through py_print_many with an explicit sep
            # and end. Reject unknown kwargs so we don't silently eat them.
            for k, _ in call.kwargs:
                if k not in ("sep", "end"):
                    raise NotImplementedError(
                        f"print() kwarg {k!r} not supported (L3-only)"
                    )
            self._emit_print_many(call)
            return

        if len(call.args) == 0:
            # print() with no args → print a bare newline. Emit a
            # printf("\n") to stay in L1 without touching the runtime.
            nl_gv = self._cstr_global("\n", ".fmt_nl")
            self.builder.call(self._printf, [self._ptr_to_cstr(nl_gv)])
            return

        if len(call.args) > 1:
            self._emit_print_many(call)
            return

        arg = call.args[0]
        arg_ty = arg.ty
        value = self._emit_expr(arg)

        # CPython-backed value: convert to a pcc str via py_cpy_to_pcc_str
        # before feeding py_print.
        if value in getattr(self, "_cpy_values", ()):
            pcc_str = self.builder.call(
                self.runtime["py_cpy_to_pcc_str"], [value],
                name=self._fresh("cpy.str"),
            )
            self.builder.call(self.runtime["py_print"], [pcc_str])
            # Release the CPython reference held by ``value``.
            self.builder.call(self.runtime["py_cpy_decref"], [value])
            return

        if isinstance(arg_ty, IntType):
            fmt = self._ptr_to_cstr(self._get_fmt_int())
            self.builder.call(self._printf, [fmt, value])
            return
        if isinstance(arg_ty, FloatType):
            fmt = self._ptr_to_cstr(self._get_fmt_float())
            self.builder.call(self._printf, [fmt, value])
            return
        if isinstance(arg_ty, BoolType):
            # Select between "True\n" and "False\n" at runtime.
            true_fmt = self._ptr_to_cstr(self._get_fmt_bool_true())
            false_fmt = self._ptr_to_cstr(self._get_fmt_bool_false())
            chosen = self.builder.select(value, true_fmt, false_fmt,
                                          name=self._fresh("bool_fmt"))
            self.builder.call(self._printf, [chosen])
            return

        # Object-typed print (str / list / dict / tuple / None / dyn) —
        # dispatch to ``py_print``. The runtime handles repr + newline.
        obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, value, arg_ty
        )
        self.builder.call(self.runtime["py_print"], [obj])

    def _emit_print_many(self, call: Call) -> None:
        """Emit ``py_print_many(args_tuple, sep, end)`` for print with N args.

        Each positional is marshalled into a PyObject* and stored into
        a freshly allocated tuple. Keyword args ``sep`` / ``end`` are
        passed through if present; otherwise defaults (`" "` / `"\\n"`)
        get boxed inline.
        """
        n = len(call.args)
        n_val = ir.Constant(_I64, n)
        tup = self.builder.call(self.runtime["py_tuple_new"], [n_val],
                                  name=self._fresh("pr.args"))
        for i, arg in enumerate(call.args):
            v = self._emit_expr(arg)
            # CPython-backed values need to be converted to a pcc
            # PyStrObject before going into a pcc tuple — otherwise
            # py_print_many walks them as if they were pcc PyObject*
            # and prints the raw pointer.
            if v in getattr(self, "_cpy_values", ()):
                pcc_str = self.builder.call(
                    self.runtime["py_cpy_to_pcc_str"], [v],
                    name=self._fresh("cpy.str"),
                )
                self.builder.call(self.runtime["py_cpy_decref"], [v])
                v_obj = pcc_str
            else:
                v_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, v, arg.ty
                )
            idx = ir.Constant(_I64, i)
            self.builder.call(
                self.runtime["py_tuple_set_item"], [tup, idx, v_obj]
            )

        sep_obj: Optional[ir.Value] = None
        end_obj: Optional[ir.Value] = None
        for k, vexpr in call.kwargs:
            v = self._emit_expr(vexpr)
            boxed = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, vexpr.ty
            )
            if k == "sep":
                sep_obj = boxed
            elif k == "end":
                end_obj = boxed
        if sep_obj is None:
            sep_obj = self._emit_literal_str(" ")
        if end_obj is None:
            end_obj = self._emit_literal_str("\n")
        self.builder.call(
            self.runtime["py_print_many"], [tup, sep_obj, end_obj]
        )

    def _emit_literal_str(self, s: str) -> ir.Value:
        return self._emit_str_literal(s)

    # -- Imports (Phase 4 CPython C-API fallback) ---------------------

    def _cpy_module_global(self, local_name: str) -> ir.GlobalVariable:
        """Return (or create) the module-level ``i8*`` global that
        stores the imported CPython ``PyObject *``. Shared across
        functions so a user's ``main()`` can read a module bound by a
        top-level ``import`` statement."""
        gname = f".cpy.modref.{local_name}"
        existing = self.module.globals.get(gname)
        if isinstance(existing, ir.GlobalVariable):
            return existing
        g = ir.GlobalVariable(self.module, _CSTR, name=gname)
        g.linkage = "internal"
        g.initializer = ir.Constant(_CSTR, None)
        return g

    _EXTERN_SCAFFOLD_MODULES = frozenset({
        "pcc.extern", "pcc.llvm_capi",
    })

    # No-op decorator whitelist: decorators here are treated as
    # identity transforms at pcc codegen (the function definition
    # proceeds unchanged). Used for libraries like ``click`` whose
    # decorators attach CLI metadata that pcc doesn't model, and for
    # ``functools.wraps`` which is a no-op for pcc's purposes.
    _NOOP_DECORATOR_QUALIFIED = frozenset({
        "click.command",
        "click.option",
        "click.argument",
        "click.pass_context",
        "click.group",
        "functools.wraps",
    })

    _NOOP_DECORATOR_BARE = frozenset({
        "wraps",
    })

    def _decorator_qualname(self, dec):
        """Return a dotted identifier for a decorator expression or
        ``None`` if the decorator isn't a simple Name / Attr chain."""
        # ``@foo`` — bare Name
        if isinstance(dec, Name):
            return dec.ident
        # ``@foo.bar`` or deeper
        if isinstance(dec, Attr):
            parts: list[str] = [dec.name]
            cur = dec.obj
            while isinstance(cur, Attr):
                parts.append(cur.name)
                cur = cur.obj
            if isinstance(cur, Name):
                parts.append(cur.ident)
                return ".".join(reversed(parts))
            return None
        # ``@foo(args)`` or ``@foo.bar(args)`` — Call wrapping a name chain
        if isinstance(dec, Call):
            return self._decorator_qualname(dec.func)
        return None

    def _decorator_is_noop_whitelist(self, dec) -> bool:
        qn = self._decorator_qualname(dec)
        if qn is None:
            return False
        if qn in self._NOOP_DECORATOR_QUALIFIED:
            return True
        if "." not in qn and qn in self._NOOP_DECORATOR_BARE:
            return True
        return False

    def _decorator_repr(self, dec) -> str:
        qn = self._decorator_qualname(dec)
        return qn if qn is not None else type(dec).__name__

    # Compile-time-only stdlib modules whose imports pcc consumes at
    # the frontend (type annotations, decorators) before codegen runs.
    # Routing these through ``py_cpy_import`` would pull libpython into
    # every produced binary — instead the import emits nothing and the
    # names they expose never materialise as runtime values. Anything
    # that *does* need a runtime binding (e.g. ``typing.cast`` used as
    # a first-class value) must be added as an explicit builtin.
    _COMPILE_TIME_ONLY_MODULES = frozenset({
        "__future__",
        "typing",
        # ``click`` contributes decorators and marker values that pcc
        # treats as no-ops; see the decorator whitelist above. Tests
        # that actually need click-parsed CLI args run under CPython
        # and don't reach this path.
        "click",
    })

    def _emit_import(self, stmt: Import) -> None:
        """Lower ``import a`` / ``import a.b`` / ``import a.b as c`` via
        py_cpy_import. For dotted names without an alias, we import the
        full path (to ensure submodules are loaded) but bind the
        top-level package under its short name, matching CPython's
        ``import a.b`` semantics (access via ``a.b``). With an
        ``as`` alias we bind the leaf module to that alias."""
        # Compile-time-only modules drop out entirely.
        stmt_names = [
            (m, a) for (m, a) in stmt.names
            if m.split(".")[0] not in self._COMPILE_TIME_ONLY_MODULES
        ]
        if not stmt_names:
            return
        self._ensure_cpy_init()
        cpy_modules = self._cpy_modules()
        for mod_name, as_name in stmt_names:
            # Always import the full dotted path so side-effect
            # submodule registration runs.
            full_ptr = self._ptr_to_cstr(
                self._cstr_global(mod_name, f".cpy.mod.{mod_name}")
            )
            leaf_val = self.builder.call(
                self.runtime["py_cpy_import"], [full_ptr],
                name=self._fresh(f"cpy.import.{mod_name.replace('.', '_')}"),
            )
            if as_name is None and "." in mod_name:
                # ``import urllib.parse`` — bind urllib (the top-level),
                # not the leaf, so ``urllib.parse.quote`` works.
                top_name = mod_name.split(".")[0]
                top_ptr = self._ptr_to_cstr(
                    self._cstr_global(top_name, f".cpy.mod.{top_name}")
                )
                top_val = self.builder.call(
                    self.runtime["py_cpy_import"], [top_ptr],
                    name=self._fresh(f"cpy.import.{top_name}"),
                )
                gv = self._cpy_module_global(top_name)
                self.builder.store(top_val, gv)
                cpy_modules[top_name] = gv
                # Leaf reference is no longer needed — release.
                self.builder.call(self.runtime["py_cpy_decref"], [leaf_val])
            else:
                local_name = as_name or mod_name
                gv = self._cpy_module_global(local_name)
                self.builder.store(leaf_val, gv)
                cpy_modules[local_name] = gv

    def _predeclare_native_cross_module(
        self, stmt: ImportFrom, src_module: str, exports: dict,
    ) -> None:
        """First-pass declaration for native cross-module imports:
        declare the extern function globals and bind them in
        ``self.functions`` so user-function bodies lowered in the
        same compilation unit can resolve the call. Class imports
        declare the class global + method externs via
        ``class_lowering.declare_extern_class``."""
        sanitised = src_module.replace(".", "_").replace("-", "_")
        for attr_name, as_name in stmt.names:
            local_name = as_name or attr_name
            info = exports.get(attr_name)
            if info is None:
                # Not a native export — pre-seed a CPython-module
                # global so the Name lookup inside function bodies
                # resolves. The main-body walker will emit the actual
                # import via _import_from_cpython_single.
                self._cpy_module_global(local_name)
                self._cpy_modules()[local_name] = (
                    self._cpy_module_global(local_name)
                )
                continue
            if info["kind"] == "function":
                sym = f"user_{sanitised}_{attr_name}"
                existing = self.module.globals.get(sym)
                if isinstance(existing, ir.Function):
                    fn = existing
                else:
                    param_tys = [
                        self._map_type(t) for t in info["param_types"]
                    ]
                    ret_ty = info["return_ty"]
                    fnty = ir.FunctionType(
                        self._map_type(ret_ty) if ret_ty is not None else _VOID,
                        param_tys,
                    )
                    fn = ir.Function(self.module, fnty, name=sym)
                    fn.linkage = "external"
                self.functions[local_name] = fn
                if not hasattr(self, "_cross_module_func_defs"):
                    self._cross_module_func_defs = {}
                self._cross_module_func_defs[local_name] = info.get(
                    "func_def",
                )
                continue
            if info["kind"] == "class":
                self.class_lowering.declare_extern_class(
                    owning_module=src_module,
                    class_name=info["class_name"],
                    field_names=info["field_names"],
                    methods=info["methods"],
                    local_name=local_name,
                )
                continue
            # Other kinds — fall through to CPython shim.

    def _bind_native_cross_module_imports(
        self, stmt: ImportFrom, src_module: str, exports: dict,
    ) -> None:
        """For each name imported from a native sibling module,
        declare an extern function of matching signature and register
        in ``self.functions`` so subsequent calls resolve to it."""
        sanitised = src_module.replace(".", "_").replace("-", "_")
        for attr_name, as_name in stmt.names:
            local_name = as_name or attr_name
            info = exports.get(attr_name)
            if info is None:
                # Name isn't a top-level FuncDef/ClassDef export of
                # the native sibling — could be a module-alias
                # (``from . import foo as f``), a top-level constant,
                # or something the pre-pass doesn't model yet. Route
                # through CPython import so the binding still exists.
                self._import_from_cpython_single(
                    stmt, src_module, attr_name, as_name,
                )
                continue
            kind = info["kind"]
            if kind == "function":
                sym = f"user_{sanitised}_{attr_name}"
                existing = self.module.globals.get(sym)
                if isinstance(existing, ir.Function):
                    fn = existing
                else:
                    param_tys = [
                        self._map_type(t) for t in info["param_types"]
                    ]
                    ret_ty = info["return_ty"]
                    fnty = ir.FunctionType(
                        self._map_type(ret_ty) if ret_ty is not None else _VOID,
                        param_tys,
                    )
                    fn = ir.Function(self.module, fnty, name=sym)
                    fn.linkage = "external"
                self.functions[local_name] = fn
                # Record the original FuncDef-like signature so the
                # call-site kwargs resolver can map keyword → position.
                if not hasattr(self, "_cross_module_func_defs"):
                    self._cross_module_func_defs: dict = {}
                self._cross_module_func_defs[local_name] = info.get(
                    "func_def",
                )
                continue
            if kind == "class":
                # Class was pre-declared in the first pass; no runtime
                # IR needed here (declare_extern_class is idempotent).
                continue
            # Other kinds (constants) fall through to CPython so the
            # program still links.
            self._import_from_cpython_single(
                stmt, src_module, attr_name, as_name,
            )

    def _import_from_cpython_single(
        self, stmt: ImportFrom, src_module: str,
        attr_name: str, as_name,
    ) -> None:
        """Route a single ``from X import Y`` entry through the
        existing CPython-import machinery — used when the multi-file
        path can't model the exported symbol (class / constant for
        now)."""
        self._ensure_cpy_init()
        cpy_modules = self._cpy_modules()
        mod_ptr = self._ptr_to_cstr(
            self._cstr_global(src_module, f".cpy.mod.{src_module}")
        )
        mod_val = self.builder.call(
            self.runtime["py_cpy_import"], [mod_ptr],
            name=self._fresh(f"cpy.fromimport.{src_module}"),
        )
        local_name = as_name or attr_name
        attr_ptr = self._ptr_to_cstr(
            self._cstr_global(attr_name, f".cpy.attr.{attr_name}")
        )
        val = self.builder.call(
            self.runtime["py_cpy_getattr"], [mod_val, attr_ptr],
            name=self._fresh(f"cpy.from.{local_name}"),
        )
        gv = self._cpy_module_global(local_name)
        self.builder.store(val, gv)
        cpy_modules[local_name] = gv

    def _resolve_relative_import(self, stmt: ImportFrom) -> str:
        """Turn a relative ``from .lib import X`` into its absolute
        dotted module name using ``self.ast_module.name`` as the
        current package context. Non-relative imports are returned
        unchanged."""
        level = getattr(stmt, "level", 0) or 0
        if level == 0:
            return stmt.module or ""
        cur = self.ast_module.name or ""
        parts = cur.split(".") if cur else []
        # ``from . import X`` inside ``pkg.entry`` strips one segment
        # to land in ``pkg``; ``from .. import X`` two, etc.
        if level > len(parts):
            # Over-dotted relative import; fall back to the raw name.
            return stmt.module or ""
        base_parts = parts[: len(parts) - level]
        if stmt.module:
            return ".".join(base_parts + [stmt.module])
        return ".".join(base_parts)

    def _emit_import_from(self, stmt: ImportFrom) -> None:
        """Lower ``from a import b`` via py_cpy_import + py_cpy_getattr,
        UNLESS ``a`` is one of the pcc compile-time scaffold modules
        (``pcc.extern`` / ``pcc.llvm_capi``) — in that case the names
        are compile-time markers and we register each one in a
        per-module registry without emitting any runtime IR."""
        if stmt.module in self._EXTERN_SCAFFOLD_MODULES:
            self._register_extern_scaffold_imports(stmt)
            return
        if (
            stmt.module is not None
            and stmt.module.split(".")[0] in self._COMPILE_TIME_ONLY_MODULES
        ):
            # Consumed at parse / type-inference time; no runtime IR.
            return

        # Multi-file compile: if the source module is a sibling being
        # compiled in the same invocation, declare each imported name
        # as an external function (for now only functions — classes and
        # constants are follow-ups) and register in the user-function
        # table so direct calls emit ``call @user_<mod>_<fn>``.
        native_table = getattr(self, "_native_module_exports", None)
        if native_table is not None:
            resolved = self._resolve_relative_import(stmt)
            if resolved in native_table:
                self._bind_native_cross_module_imports(
                    stmt, resolved, native_table[resolved],
                )
                return
        self._ensure_cpy_init()
        cpy_modules = self._cpy_modules()
        mod_ptr = self._ptr_to_cstr(
            self._cstr_global(stmt.module, f".cpy.mod.{stmt.module}")
        )
        mod_val = self.builder.call(
            self.runtime["py_cpy_import"], [mod_ptr],
            name=self._fresh(f"cpy.fromimport.{stmt.module}"),
        )
        for attr_name, as_name in stmt.names:
            local_name = as_name or attr_name
            attr_ptr = self._ptr_to_cstr(
                self._cstr_global(attr_name, f".cpy.attr.{attr_name}")
            )
            val = self.builder.call(
                self.runtime["py_cpy_getattr"], [mod_val, attr_ptr],
                name=self._fresh(f"cpy.from.{local_name}"),
            )
            gv = self._cpy_module_global(local_name)
            self.builder.store(val, gv)
            cpy_modules[local_name] = gv

    _EXTERN_CTYPE_IR = {
        "c_void": _VOID,
        "c_bool": _I1,
        "c_int8": ir.IntType(8),
        "c_int16": ir.IntType(16),
        "c_int32": _I32,
        "c_int": _I32,
        "c_int64": _I64,
        "c_long": _I64,
        "c_uint8": ir.IntType(8),
        "c_uint16": ir.IntType(16),
        "c_uint32": _I32,
        "c_uint64": _I64,
        "c_size_t": _I64,
        "c_float": ir.FloatType(),
        "c_double": _DOUBLE,
        "c_ptr": _CSTR,  # opaque i8*
        "c_str": _CSTR,
    }

    def _register_extern_scaffold_imports(self, stmt: "ImportFrom") -> None:
        """Track ``from pcc.extern import extern, c_int, ...`` bindings
        so the Name-based check in :meth:`_maybe_register_extern_assign`
        can recognize the ``extern`` factory call.
        """
        if not hasattr(self, "_extern_bindings"):
            self._extern_bindings: dict[str, str] = {}
        for attr_name, as_name in stmt.names:
            local = as_name or attr_name
            self._extern_bindings[local] = attr_name

    def _maybe_register_extern_assign(self, stmt: "Assign") -> bool:
        """If the RHS is a call to the imported ``extern`` factory,
        record the decl and suppress runtime emission. Returns True if
        handled."""
        bindings = getattr(self, "_extern_bindings", {})
        if not bindings:
            return False
        value = stmt.value
        if not isinstance(value, Call) or not isinstance(value.func, Name):
            return False
        if bindings.get(value.func.ident) != "extern":
            return False
        if not value.args:
            return False
        symbol_expr = value.args[0]
        if not isinstance(symbol_expr, StrLit):
            return False
        symbol = symbol_expr.value
        # Parse argtypes tuple and restype from kwargs or positional.
        argtype_exprs: tuple = ()
        restype_name: str = "c_void"
        variadic = False
        for k, kv in value.kwargs:
            if k == "argtypes" and isinstance(kv, TupleExpr):
                argtype_exprs = kv.elems
            elif k == "restype" and isinstance(kv, Name):
                restype_name = kv.ident
            elif k == "variadic" and isinstance(kv, BoolLit):
                variadic = kv.value
        if not argtype_exprs and len(value.args) >= 2:
            a = value.args[1]
            if isinstance(a, TupleExpr):
                argtype_exprs = a.elems
        if restype_name == "c_void" and len(value.args) >= 3:
            rt = value.args[2]
            if isinstance(rt, Name):
                restype_name = rt.ident
        argtype_names: list[str] = []
        for ae in argtype_exprs:
            if not isinstance(ae, Name):
                return False
            argtype_names.append(ae.ident)
        for target in stmt.targets:
            if not isinstance(target, Name):
                continue
            if not hasattr(self, "_extern_decls"):
                self._extern_decls: dict[str, tuple[str, list[str], str, bool]] = {}
            self._extern_decls[target.ident] = (
                symbol, argtype_names, restype_name, variadic,
            )
        return True

    def _emit_extern_call(
        self, decl: tuple[str, list[str], str, bool], args: tuple,
    ) -> ir.Value:
        symbol, argtype_names, restype_name, variadic = decl
        # Build / get the declared function.
        param_tys = [
            self._EXTERN_CTYPE_IR[n] for n in argtype_names
        ]
        ret_ty = self._EXTERN_CTYPE_IR[restype_name]
        fnty = ir.FunctionType(ret_ty, param_tys, var_arg=variadic)
        fn = self.module.globals.get(symbol)
        if not isinstance(fn, ir.Function):
            fn = ir.Function(self.module, fnty, name=symbol)
            fn.linkage = "external"

        # Marshal each actual arg to the declared IR type.
        ir_args: list[ir.Value] = []
        for i, a in enumerate(args):
            v = self._emit_expr(a)
            if i < len(argtype_names):
                want = self._EXTERN_CTYPE_IR[argtype_names[i]]
                v = self._coerce_to_extern(v, a.ty, want, argtype_names[i])
            ir_args.append(v)
        call_name = (
            "" if isinstance(ret_ty, ir.VoidType)
            else self._fresh(f"extern.{symbol}.ret")
        )
        return self.builder.call(fn, ir_args, name=call_name)

    def _coerce_to_extern(
        self, v: ir.Value, ty: "Type", want: ir.Type, ctype_name: str,
    ) -> ir.Value:
        """Narrow bridge between pcc-native scalar types and the
        extern declaration's IR type. Handles int→i32/i64 truncate+
        sext, pcc str → i8*, bool zext."""
        if isinstance(want, ir.VoidType):
            return v
        if ctype_name in {"c_str", "c_ptr"}:
            # pcc str is already i8* (points to PyStrObject); for the
            # narrow P6C.1 case we want the underlying C string, not
            # the PyStrObject. This requires a runtime helper — for
            # now pass through and document the sharp edge.
            return v
        if isinstance(want, ir.IntType):
            i64 = self._to_int64(v, ty)
            if want.width == 64:
                return i64
            if want.width < 64:
                return self.builder.trunc(
                    i64, want, name=self._fresh(f"extern.trunc{want.width}"),
                )
            return self.builder.sext(
                i64, want, name=self._fresh(f"extern.sext{want.width}"),
            )
        if isinstance(want, ir.DoubleType):
            return self._to_double(v, ty)
        return v

    def _cpy_modules(self) -> dict:
        """Module-wide map of imported local name → global variable."""
        if not hasattr(self, "_cpy_module_env"):
            self._cpy_module_env = {}
        return self._cpy_module_env

    def _ensure_cpy_init(self) -> None:
        """Emit a one-time ``py_cpy_ensure_init()`` in the current
        function. Idempotent both in IR (py_cpy_ensure_init's atomic
        guard) and in emission (we only emit it once per function
        compilation)."""
        if not hasattr(self, "_cpy_init_emitted_fns"):
            self._cpy_init_emitted_fns = set()
        fn_id = id(self.current_function)
        if fn_id in self._cpy_init_emitted_fns:
            return
        self.builder.call(self.runtime["py_cpy_ensure_init"], [])
        self._cpy_init_emitted_fns.add(fn_id)

    # -- With-statement (context manager) -----------------------------

    def _emit_with(self, stmt: With) -> None:
        """Narrow-subset ``with EXPR as VAR: BODY`` lowering.

        For a single context expression whose value is CPython-backed
        (e.g. ``with open(...) as f:``), emit the happy-path sequence::

            _cm   = <expr>
            _val  = py_cpy_call1(py_cpy_getattr(_cm, "__enter__"), _cm)  # bound method
            VAR   = _val                                                 # if as-clause
            <body>
            py_cpy_call3(py_cpy_getattr(_cm, "__exit__"), None, None, None)

        Exception-path unwinding through __exit__ is deferred —
        exceptions inside the body propagate past __exit__ in this
        subset.
        """
        if len(stmt.items) != 1:
            raise NotImplementedError(
                "Layer 1 with-statement only handles a single context expression"
            )
        ctx_expr, as_expr = stmt.items[0]
        ctx_val = self._emit_expr(ctx_expr)
        if ctx_val not in getattr(self, "_cpy_values", ()):
            raise NotImplementedError(
                "Layer 1 with-statement only supports CPython-backed "
                "context managers (e.g. ``with open(...)``) in this subset"
            )

        # Call __enter__ via py_cpy_call_noargs on a bound method — i.e.
        # PyObject_GetAttr returns a bound method that already knows the
        # receiver, so we don't pass self explicitly.
        enter_ptr = self._ptr_to_cstr(
            self._cstr_global("__enter__", ".cpy.attr.__enter__")
        )
        enter_fn = self.builder.call(
            self.runtime["py_cpy_getattr"], [ctx_val, enter_ptr],
            name=self._fresh("with.enter.fn"),
        )
        enter_val = self.builder.call(
            self.runtime["py_cpy_call_noargs"], [enter_fn],
            name=self._fresh("with.enter.val"),
        )
        self.builder.call(self.runtime["py_cpy_decref"], [enter_fn])

        # Tag the __enter__ result as CPython.
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(enter_val)

        if as_expr is not None:
            if not isinstance(as_expr, Name):
                raise NotImplementedError(
                    "Layer 1 with: as-clause must be a bare name"
                )
            slot = self.env.get(as_expr.ident)
            if slot is None:
                alloca = self._alloca_in_entry(
                    _CSTR, name=f"{as_expr.ident}.addr",
                )
                self.env[as_expr.ident] = (
                    alloca, _CSTR, DynType(name="dyn"),
                )
                slot = self.env[as_expr.ident]
            self.builder.store(enter_val, slot[0])
            if not hasattr(self, "_cpy_env_flags"):
                self._cpy_env_flags = {}
            self._cpy_env_flags[as_expr.ident] = True

        self._emit_stmts(stmt.body)

        if not self.builder.block.is_terminated:
            # __exit__(None, None, None). Use py_None from pcc's
            # runtime to seed a CPython None — actually safer: pass
            # NULL through py_cpy_call3; libpython interprets NULL as
            # no-argument. Wait no — we need real Py_None. Get it
            # once via PyImport_ImportModule("builtins").__dict__[None]
            # — too heavy. Instead, pass the CPython builtin None via
            # py_cpy_to_pcc_str of "None" round-tripped? Simpler:
            # fetch None through a dedicated helper. For this narrow
            # subset, call __exit__ with no args via
            # py_cpy_call_noargs — many context managers (file, lock)
            # accept it.
            exit_ptr = self._ptr_to_cstr(
                self._cstr_global("__exit__", ".cpy.attr.__exit__")
            )
            exit_fn = self.builder.call(
                self.runtime["py_cpy_getattr"], [ctx_val, exit_ptr],
                name=self._fresh("with.exit.fn"),
            )
            # __exit__ needs 3 args; libpython's ``open`` implementation
            # tolerates call-with-None-triple via CallFunctionObjArgs
            # but our narrow path here prefers a best-effort
            # call_noargs that most stdlib file-like context managers
            # accept. A proper lowering would pass Py_None × 3.
            self.builder.call(
                self.runtime["py_cpy_call_noargs"], [exit_fn],
                name=self._fresh("with.exit.val"),
            )
            self.builder.call(self.runtime["py_cpy_decref"], [exit_fn])
            self.builder.call(self.runtime["py_cpy_decref"], [enter_val])
            # ctx_val was obtained from _emit_expr(ctx_expr); if it
            # was freshly produced (not a borrowed module ref), it's
            # also owned. Leave the decref policy to whoever emitted
            # ctx_expr.

    # -- Exceptions: raise + try/except/finally -----------------------

    _BUILTIN_EXC_TAG = {
        "BaseException": 0,
        "Exception": 1,
        "ValueError": 2,
        "TypeError": 3,
        "KeyError": 4,
        "IndexError": 5,
        "AttributeError": 6,
        "RuntimeError": 7,
        "StopIteration": 8,
        "ZeroDivisionError": 9,
        "NameError": 10,
        "NotImplementedError": 11,
        "ArithmeticError": 12,
        "LookupError": 13,
        "OSError": 14,
        "OverflowError": 15,
        "AssertionError": 16,
    }

    def _set_personality(self) -> None:
        """Attach the Itanium personality to the current function once."""
        fn = self.current_function
        if fn is None:
            return
        if getattr(fn, "_pcc_personality_set", False):
            return
        # Declare once per module.
        pers_name = "__gxx_personality_v0"
        pers = self.module.globals.get(pers_name)
        if pers is None:
            pers_ty = ir.FunctionType(_I32, [], var_arg=True)
            pers = ir.Function(self.module, pers_ty, name=pers_name)
            pers.linkage = "external"
        # llvmlite IRBuilder fn has an ``attributes`` list for function
        # attrs and a ``personality`` slot for the personality function.
        # Note: llvmlite only emits personality / alignstack in the
        # prototype when the attribute set is truthy, so we unconditionally
        # add ``uwtable`` (always safe and correct for EH-enabled funcs).
        fn.attributes.add("uwtable")
        fn.attributes.personality = pers
        fn._pcc_personality_set = True

    def _typeinfo_global(self) -> ir.GlobalVariable:
        name = "py_exception_typeinfo"
        g = self.module.globals.get(name)
        if g is not None:
            return g
        # Runtime defines this as a ``const void *const``. Model the
        # external with an opaque pointer-typed global.
        g = ir.GlobalVariable(self.module, _CSTR, name=name)
        g.linkage = "external"
        return g

    def _emit_raise(self, stmt: Raise) -> None:
        if stmt.exc is None:
            # Bare ``raise`` inside a handler: re-raise the current
            # exception (py_current_exception) via py_raise.
            cur = self.builder.call(
                self.runtime["py_current_exception"], [],
                name=self._fresh("reraise.exc"),
            )
            self._invoke_or_call_noreturn(
                self.runtime["py_raise"], [cur]
            )
            return

        exc_val = self._build_exception_value(stmt.exc)

        if stmt.cause is not None:
            cause_val = self._emit_expr(stmt.cause)
            self.builder.call(
                self.runtime["py_exc_set_cause"], [exc_val, cause_val]
            )

        self._invoke_or_call_noreturn(
            self.runtime["py_raise"], [exc_val]
        )

    def _invoke_or_call_noreturn(
        self, fn: ir.Function, args: list[ir.Value]
    ) -> None:
        """Emit ``py_raise`` (or an equivalent noreturn throw helper).

        When we're lowering a statement that lives inside a ``try``
        block, use LLVM ``invoke`` so the thrown exception unwinds to
        our landingpad. Otherwise fall back to a plain ``call`` — the
        unwinder will walk past this frame looking for a handler, or
        abort with an unhandled-exception message.
        """
        lpad = getattr(self, "_try_lpad", None)
        if lpad is None:
            self.builder.call(fn, args)
            self.builder.unreachable()
            return
        parent_fn = self.current_function
        # Create a dead fall-through block for the non-unwind edge —
        # ``py_raise`` is noreturn so this edge is never actually taken.
        dead = parent_fn.append_basic_block(
            name=self._fresh("raise.cont")
        )
        self.builder.invoke(fn, args, dead, lpad)
        self.builder.position_at_end(dead)
        self.builder.unreachable()

    def _build_exception_value(self, exc_expr: Expr) -> ir.Value:
        """Lower a ``raise`` operand expression to a ``PyObject*``.

        Supports ``ExceptionClass("msg")`` / ``ExceptionClass()`` for
        builtin exception names, plus a bare ``Name`` for re-raising an
        already-bound exception.
        """
        if isinstance(exc_expr, Call) and isinstance(exc_expr.func, Name):
            cls_name = exc_expr.func.ident
            tag = self._BUILTIN_EXC_TAG.get(cls_name)
            if tag is not None:
                if exc_expr.args:
                    first = exc_expr.args[0]
                    if isinstance(first, StrLit):
                        msg_ptr = self._ptr_to_cstr(
                            self._cstr_global(first.value, ".exc.msg")
                        )
                    else:
                        # Fallback: the constructor just stashes a name.
                        msg_ptr = self._ptr_to_cstr(
                            self._cstr_global("", ".exc.msg.empty")
                        )
                else:
                    msg_ptr = self._ptr_to_cstr(
                        self._cstr_global("", ".exc.msg.empty")
                    )
                return self.builder.call(
                    self.runtime["py_exc_new"],
                    [ir.Constant(_I32, tag), msg_ptr],
                    name=self._fresh(f"exc.{cls_name}"),
                )
        # ``raise NotImplementedError`` (bare builtin exception name, no
        # call). Instantiate the exception with an empty message.
        if (
            isinstance(exc_expr, Name)
            and exc_expr.ident in self._BUILTIN_EXC_TAG
            and exc_expr.ident not in self.env
        ):
            cls_name = exc_expr.ident
            tag = self._BUILTIN_EXC_TAG[cls_name]
            msg_ptr = self._ptr_to_cstr(
                self._cstr_global("", ".exc.msg.empty")
            )
            return self.builder.call(
                self.runtime["py_exc_new"],
                [ir.Constant(_I32, tag), msg_ptr],
                name=self._fresh(f"exc.{cls_name}"),
            )
        # Fallback: evaluate as an object (e.g. re-raising a bound var).
        return self._emit_as_object(exc_expr)

    def _emit_try(self, stmt: Try) -> None:
        if not stmt.handlers and not stmt.finally_body:
            # Plain try with neither handlers nor finally: just emit
            # body (shouldn't occur from the parser, but be defensive).
            self._emit_stmts(stmt.body)
            return

        # Phase-3 subset: we support try/except, optional else, and a
        # single-level try/finally via a dedicated cleanup fallthrough.
        # Nested try inside the body is handled naturally by recursion.
        self._set_personality()

        fn = self.current_function
        done_bb = fn.append_basic_block(name=self._fresh("try.done"))
        lpad_bb = fn.append_basic_block(name=self._fresh("try.lpad"))

        # Stash lpad so nested ``raise`` / ``invoke`` can see it.
        prev_lpad = getattr(self, "_try_lpad", None)
        self._try_lpad = lpad_bb

        # Emit the body.
        self._emit_stmts(stmt.body)

        # After body: fall through to else_body (if any), then to done.
        if not self.builder.block.is_terminated:
            if stmt.else_body:
                self._emit_stmts(stmt.else_body)
            if not self.builder.block.is_terminated:
                if stmt.finally_body:
                    self._emit_stmts(stmt.finally_body)
                if not self.builder.block.is_terminated:
                    self.builder.branch(done_bb)

        # Pop lpad before emitting the landing pad body — nested raises
        # in the handler itself propagate to the outer frame.
        self._try_lpad = prev_lpad

        # Landingpad.
        self.builder.position_at_end(lpad_bb)
        lp_struct_ty = ir.LiteralStructType([_CSTR, _I32])
        lp = self.builder.landingpad(lp_struct_ty, name=self._fresh("lp"))
        # Catch-all: ``catch ptr null`` in Itanium C++ ABI matches any
        # thrown object, skipping the typeinfo vtable dereference that
        # a real C++ ``typeid`` descriptor would require. Our runtime
        # doesn't ship a full ``std::type_info`` — class discrimination
        # runs inside the handler via ``py_exc_matches``.
        lp.add_clause(ir.CatchClause(ir.Constant(_CSTR, None)))
        exc_ptr = self.builder.extract_value(
            lp, 0, name=self._fresh("lp.exc_ptr"),
        )
        # __cxa_begin_catch returns the thrown object pointer (which we
        # already track via py_current_exception anyway).
        self.builder.call(
            self.runtime["__cxa_begin_catch"], [exc_ptr]
        )
        current_exc = self.builder.call(
            self.runtime["py_current_exception"], [],
            name=self._fresh("lp.cur_exc"),
        )

        if not stmt.handlers:
            # Pure try/finally: on exception, run finally_body then
            # rethrow. Whether the exception is truly "unhandled" is
            # the outer frame's problem; we just propagate.
            if stmt.finally_body:
                self._emit_stmts(stmt.finally_body)
            if not self.builder.block.is_terminated:
                self.builder.call(self.runtime["__cxa_end_catch"], [])
                self.builder.call(
                    self.runtime["py_raise"], [current_exc]
                )
                self.builder.unreachable()
            self.builder.position_at_end(done_bb)
            return

        # Walk handlers, dispatch by py_exc_matches. Structure: after
        # landingpad setup we fall through to a chain of match blocks;
        # each match block either branches into its handler body or
        # into the next match block (or, for the last handler, into a
        # rethrow block).
        current_test_bb = self.builder.block  # we're still in lpad_bb
        for i, h in enumerate(stmt.handlers):
            test_bb = fn.append_basic_block(
                name=self._fresh(f"except.test.{i}")
            )
            body_bb = fn.append_basic_block(
                name=self._fresh(f"except.body.{i}")
            )
            # Fall-through into this handler's test block.
            if not self.builder.block.is_terminated:
                self.builder.branch(test_bb)
            self.builder.position_at_end(test_bb)

            if h.exc_type is None:
                # bare ``except:`` — always match.
                cond = ir.Constant(_I1, 1)
            elif isinstance(h.exc_type, TupleExpr):
                # ``except (A, B, C):`` — OR of per-class matches.
                cond = None
                for sub in h.exc_type.elems:
                    cls_val = self._emit_exception_class_ref(sub)
                    match_i32 = self.builder.call(
                        self.runtime["py_exc_matches"],
                        [current_exc, cls_val],
                        name=self._fresh("exc.matches"),
                    )
                    this = self.builder.icmp_signed(
                        "!=", match_i32, ir.Constant(_I32, 0),
                        name=self._fresh("exc.matches.i1"),
                    )
                    cond = this if cond is None else self.builder.or_(
                        cond, this, name=self._fresh("exc.or"),
                    )
                assert cond is not None
            else:
                cls_val = self._emit_exception_class_ref(h.exc_type)
                match_i32 = self.builder.call(
                    self.runtime["py_exc_matches"],
                    [current_exc, cls_val],
                    name=self._fresh("exc.matches"),
                )
                cond = self.builder.icmp_signed(
                    "!=", match_i32, ir.Constant(_I32, 0),
                    name=self._fresh("exc.matches.i1"),
                )

            if i + 1 < len(stmt.handlers):
                next_test_bb = fn.append_basic_block(
                    name=self._fresh(f"except.next.{i + 1}")
                )
                self.builder.cbranch(cond, body_bb, next_test_bb)
            else:
                rethrow_bb = fn.append_basic_block(
                    name=self._fresh("except.rethrow")
                )
                self.builder.cbranch(cond, body_bb, rethrow_bb)
                next_test_bb = None
                # Fill in the rethrow block. Whether the exception is
                # truly unhandled gets decided by the runtime's terminate
                # handler — we just propagate.
                self.builder.position_at_end(rethrow_bb)
                self.builder.call(self.runtime["__cxa_end_catch"], [])
                self.builder.call(
                    self.runtime["py_raise"], [current_exc]
                )
                self.builder.unreachable()

            # Handler body.
            self.builder.position_at_end(body_bb)
            if h.name is not None:
                slot = self._alloca_in_entry(_CSTR, name=f"{h.name}.addr")
                self.builder.store(current_exc, slot)
                self.env[h.name] = (slot, _CSTR, DynType(name="dyn"))
            self._emit_stmts(h.body)
            if not self.builder.block.is_terminated:
                self.builder.call(self.runtime["py_clear_exception"], [])
                self.builder.call(self.runtime["__cxa_end_catch"], [])
                if stmt.finally_body:
                    self._emit_stmts(stmt.finally_body)
                if not self.builder.block.is_terminated:
                    self.builder.branch(done_bb)

            # Move on to the next handler's test block (or exit the
            # loop if this was the last one).
            if next_test_bb is not None:
                self.builder.position_at_end(next_test_bb)

        # Done.
        self.builder.position_at_end(done_bb)

    def _emit_exception_class_ref(self, expr: Expr) -> ir.Value:
        """Build a PyObject* for an exception class used in
        ``except <Expr>:``. Supports a bare builtin Name."""
        if isinstance(expr, Name):
            tag = self._BUILTIN_EXC_TAG.get(expr.ident)
            if tag is not None:
                return self.builder.call(
                    self.runtime["py_exc_builtin_class"],
                    [ir.Constant(_I32, tag)],
                    name=self._fresh(f"exc.cls.{expr.ident}"),
                )
            # User class? Look up in class_lowering.
            if (
                hasattr(self, "class_lowering")
                and expr.ident in self.class_lowering.classes
            ):
                info = self.class_lowering.classes[expr.ident]
                return self.builder.load(
                    info.global_var,
                    name=self._fresh(f"exc.ucls.{expr.ident}"),
                )
        raise NotImplementedError(
            f"Layer 1 except-clause class expression {type(expr).__name__} "
            "not supported"
        )

    # -- Control flow --------------------------------------------------

    def _emit_if(self, stmt: If) -> None:
        cond = self._emit_expr(stmt.cond)
        cond_i1 = self._truthy(cond, stmt.cond.ty)

        fn = self.current_function
        then_bb = fn.append_basic_block(name=self._fresh("if.then"))
        else_bb = fn.append_basic_block(name=self._fresh("if.else"))
        merge_bb = fn.append_basic_block(name=self._fresh("if.end"))

        self.builder.cbranch(cond_i1, then_bb, else_bb)

        self.builder.position_at_end(then_bb)
        self._emit_stmts(stmt.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_bb)

        self.builder.position_at_end(else_bb)
        if stmt.else_body:
            self._emit_stmts(stmt.else_body)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_bb)

        # If both branches terminated, merge block is unreachable —
        # still position into it so subsequent stmts have a home.
        self.builder.position_at_end(merge_bb)

    def _emit_while(self, stmt: While) -> None:
        if stmt.else_body:
            raise NotImplementedError(
                "Layer 1 does not handle while-else"
            )
        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("while.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("while.body"))
        end_bb = fn.append_basic_block(name=self._fresh("while.end"))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cond = self._emit_expr(stmt.cond)
        cond_i1 = self._truthy(cond, stmt.cond.ty)
        self.builder.cbranch(cond_i1, body_bb, end_bb)

        self.loop_stack.append((cond_bb, end_bb))
        self.builder.position_at_end(body_bb)
        self._emit_stmts(stmt.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)
        self.loop_stack.pop()

        self.builder.position_at_end(end_bb)

    def _emit_for_cpython_iter(
        self, stmt: For, iter_src_val: ir.Value,
    ) -> None:
        """Lower ``for <name> in <cpython_iterable>:`` via PyObject_GetIter
        + PyIter_Next. Each iteration binds the target name to the
        returned CPython PyObject* (tagged as cpy)."""
        fn = self.current_function
        iter_obj = self.builder.call(
            self.runtime["py_cpy_iter"], [iter_src_val],
            name=self._fresh("cpy.iter"),
        )

        header_bb = fn.append_basic_block(name=self._fresh("for.cpy.header"))
        body_bb = fn.append_basic_block(name=self._fresh("for.cpy.body"))
        after_bb = fn.append_basic_block(name=self._fresh("for.cpy.after"))

        self.builder.branch(header_bb)
        self.builder.position_at_end(header_bb)
        item = self.builder.call(
            self.runtime["py_cpy_iter_next"], [iter_obj],
            name=self._fresh("cpy.next"),
        )
        is_null = self.builder.icmp_signed(
            "==", item, ir.Constant(_CSTR, None),
            name=self._fresh("cpy.next.isnull"),
        )
        self.builder.cbranch(is_null, after_bb, body_bb)

        self.builder.position_at_end(body_bb)
        # Bind the target name: alloca if new, then store.
        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            alloca = self._alloca_in_entry(_CSTR, name=f"{target_ident}.addr")
            self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))
            slot = self.env[target_ident]
        self.builder.store(item, slot[0])
        # Mark target as CPython-backed.
        if not hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags = {}
        self._cpy_env_flags[target_ident] = True

        # Loop control stack: continue -> header, break -> after.
        self.loop_stack.append((header_bb, after_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self.builder.block.is_terminated:
            # Release item (we took ownership from PyIter_Next).
            # Note: storing into the slot didn't bump ref; we hold
            # exactly one.
            self.builder.branch(header_bb)

        self.builder.position_at_end(after_bb)
        self.builder.call(self.runtime["py_cpy_decref"], [iter_obj])

    def _emit_for_list_index(
        self, stmt: For, iter_val: ir.Value, iter_ty: Type,
    ) -> None:
        """Lower ``for <name> in <list|tuple>:`` via index + length.

        Covers ``ListType`` / ``TupleType`` iters where the runtime
        value is a PyObject* tuple/list. Element type flows from
        ``iter_ty.elem`` (list) or ``DynType`` (tuple — element types
        differ per slot, so we fall back to Dyn here).
        """
        fn = self.current_function
        iter_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime,
            iter_val, iter_ty,
        )
        if isinstance(iter_ty, ListType):
            len_helper = "py_list_len"
            get_helper = "py_list_get"
            elem_ty: Type = iter_ty.elem
        else:
            len_helper = "py_tuple_len"
            get_helper = "py_tuple_get"
            elem_ty = DynType(name="dyn")
        n_val = self.builder.call(
            self.runtime[len_helper], [iter_obj],
            name=self._fresh("for.len"),
        )

        idx_slot = self._alloca_in_entry(_I64, name="for.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            # Allocate as PyObject* when element is dyn, else native.
            if isinstance(elem_ty, DynType):
                target_ir_ty = _CSTR
            else:
                target_ir_ty = self._map_type(elem_ty)
            alloca = self._alloca_in_entry(
                target_ir_ty, name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, target_ir_ty, elem_ty)
            slot = self.env[target_ident]

        cond_bb = fn.append_basic_block(name=self._fresh("for.lst.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.lst.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.lst.step"))
        end_bb = fn.append_basic_block(name=self._fresh("for.lst.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("for.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("for.cond"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        elem_obj = self.builder.call(
            self.runtime[get_helper], [iter_obj, cur],
            name=self._fresh("for.elem"),
        )
        target_alloca, target_ir_ty, _ = slot
        if isinstance(elem_ty, DynType):
            self.builder.store(elem_obj, target_alloca)
        else:
            native_val = marshal.marshal_from_object(
                self.builder, self.module, self.runtime,
                elem_obj, elem_ty,
            )
            self.builder.store(native_val, target_alloca)

        self.loop_stack.append((step_bb, end_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self.builder.block.is_terminated:
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("for.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1), name=self._fresh("for.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _for_iter_is_enumerate(self, stmt: For) -> bool:
        it = stmt.iter
        return (
            isinstance(it, Call)
            and isinstance(it.func, Name)
            and it.func.ident == "enumerate"
            and len(it.args) == 1
            and not it.kwargs
        )

    def _for_iter_is_zip(self, stmt: For) -> bool:
        """``for <...> in zip(xs, ys, ...):`` — optionally with
        ``strict=True``, which we accept and drop."""
        it = stmt.iter
        if not (
            isinstance(it, Call)
            and isinstance(it.func, Name)
            and it.func.ident == "zip"
            and len(it.args) >= 2
        ):
            return False
        for kwn, _ in it.kwargs:
            if kwn != "strict":
                return False
        return True

    def _normalise_for_zip(self, stmt: For) -> For:
        """Rewrite ``for (a, b, ...) in zip(xs, ys, ...):`` into::

            for __zip_i__<k> in range(min(len(xs), len(ys), ...)):
                (a, b, ...) = (xs[__zip_i__<k>], ys[__zip_i__<k>], ...)
                <orig body>

        Accepts any tuple-arity on the target (pcc normalises tuple
        targets further down the pipeline) and any iterable count.
        """
        from dataclasses import replace as _replace
        it = stmt.iter
        assert isinstance(it, Call)
        xs_list = it.args
        span = stmt.span
        int_ty = IntType(name="int")
        idx_name = self._fresh("zip_i")
        idx_ref = Name(span=span, ty=int_ty, ident=idx_name)

        # Build ``min(len(xs0), len(xs1), ...)``.
        def _len_call(e):
            return Call(
                span=span, ty=int_ty,
                func=Name(span=span, ty=DynType(name="dyn"), ident="len"),
                args=(e,),
            )
        if len(xs_list) == 1:
            stop_expr = _len_call(xs_list[0])
        else:
            # ``min(a, b, c, ...)`` — only the 2-arg form is wired as a
            # builtin fast path, so chain it left-associatively.
            stop_expr = _len_call(xs_list[0])
            for rest in xs_list[1:]:
                stop_expr = Call(
                    span=span, ty=int_ty,
                    func=Name(span=span, ty=DynType(name="dyn"), ident="min"),
                    args=(stop_expr, _len_call(rest)),
                )
        # ``range(stop_expr)`` drives the indexed walk.
        new_iter = Call(
            span=span, ty=DynType(name="dyn"),
            func=Name(span=span, ty=DynType(name="dyn"), ident="range"),
            args=(stop_expr,),
        )
        # Build ``(a, b, ...) = (xs0[i], xs1[i], ...)`` prelude.
        # Derive each subscript's type from its list/tuple elem type so
        # downstream store code picks the correct i64/ptr slot rather
        # than defaulting to DynType (which would mix ptr and i64 in
        # the same alloca).
        def _subscript_ty(xs: Expr) -> Type:
            xt = xs.ty
            if isinstance(xt, ListType):
                return xt.elem
            if isinstance(xt, TupleType) and xt.elems:
                # Assume homogenous for zip purposes — falls back to
                # the first element type which is usually correct.
                return xt.elems[0]
            return DynType(name="dyn")
        pair_elems = tuple(
            Subscript(
                span=span, ty=_subscript_ty(xs), obj=xs, idx=idx_ref,
            )
            for xs in xs_list
        )
        if isinstance(stmt.target, TupleExpr):
            # Re-use the existing tuple target.
            assign_unpack = Assign(
                span=span,
                targets=(stmt.target,),
                value=TupleExpr(
                    span=span,
                    ty=TupleType(
                        name="tuple", elems=tuple(e.ty for e in pair_elems),
                    ),
                    elems=pair_elems,
                ),
                annotation=None,
            )
        elif isinstance(stmt.target, Name):
            # ``for pair in zip(...):`` — bind whole tuple.
            assign_unpack = Assign(
                span=span,
                targets=(stmt.target,),
                value=TupleExpr(
                    span=span,
                    ty=TupleType(
                        name="tuple", elems=tuple(e.ty for e in pair_elems),
                    ),
                    elems=pair_elems,
                ),
                annotation=None,
            )
        else:
            raise NotImplementedError(
                "zip() target must be a Name or TupleExpr of Names"
            )
        new_body = (assign_unpack,) + tuple(stmt.body)
        return _replace(
            stmt, target=idx_ref, iter=new_iter, body=new_body,
        )

    def _normalise_for_enumerate(self, stmt: For) -> For:
        """Rewrite ``for <target> in enumerate(xs):`` into the
        equivalent manually-indexed loop::

            __enum_i__<k> = 0
            for <target-sans-index> in xs:
                <target> = (__enum_i__<k>, <target-sans-index>)
                <orig body>
                __enum_i__<k> = __enum_i__<k> + 1

        The synthetic counter is an annotated int so inference keeps
        it on the native path; tuple-target unpacking picks up the
        rest via the existing ``_normalise_for_tuple_target`` helper.
        """
        it = stmt.iter
        assert isinstance(it, Call)
        inner_iter = it.args[0]
        cnt_name = self._fresh("enum_i")
        span = stmt.span
        int_ty = IntType(name="int")
        zero_lit = IntLit(span=span, ty=int_ty, value=0)
        one_lit = IntLit(span=span, ty=int_ty, value=1)
        cnt_ref = Name(span=span, ty=int_ty, ident=cnt_name)

        # Insert the counter init *before* the for-loop itself by
        # synthesising an Assign statement and prepending it to the
        # caller's scope. Since we can't rewrite the surrounding
        # body here, fold the init into the pre-loop region by
        # stashing it on the codegen — the emitter will see it on
        # the next ``_emit_stmt`` entry. That infra is intrusive;
        # instead, emit the init *inline* here via a direct alloca
        # + store, side-stepping the need to touch the parent list.
        # The tuple-normaliser runs after us, so pre-loop allocation
        # inside the body avoids re-running inside each iteration.
        # We use a dedicated "pre-loop bootstrap" list on ``self``.
        # Simplest: add the init as the first statement of the new
        # stmt's body. This re-inits the counter every iteration —
        # wrong. Instead, emit the init via a small runtime routine
        # using an explicit @_pcc_py_* helper. Since that's overkill
        # for a desugar, we register a per-loop alloca outside the
        # body using an auxiliary stash consumed by _emit_stmts.
        #
        # Pragmatic approach: leave the alloca/store emission to
        # ``_emit_for`` itself, and only rewrite the AST to handle
        # the bookkeeping. Hand the ``_emit_for`` a counter name
        # plus target binding via a side-channel on ``stmt``.
        from dataclasses import replace as _replace

        # Synthesize a target Name for the "value" slot.
        if isinstance(stmt.target, TupleExpr) and len(stmt.target.elems) == 2:
            idx_target, val_target = stmt.target.elems
            if not (
                isinstance(idx_target, Name)
                and isinstance(val_target, Name)
            ):
                raise NotImplementedError(
                    "enumerate() target must be a (Name, Name) pair"
                )
            assign_idx = Assign(
                span=span,
                targets=(idx_target,),
                value=cnt_ref,
                annotation=int_ty,
            )
            new_target = val_target
            prelude = (assign_idx,)
        elif isinstance(stmt.target, Name):
            # ``for pair in enumerate(xs):`` — bind the whole tuple.
            tup_target = stmt.target
            val_name = self._fresh("enum_val")
            val_target = Name(span=span, ty=DynType(name="dyn"), ident=val_name)
            pair_expr = TupleExpr(
                span=span,
                ty=TupleType(name="tuple", elems=(int_ty, val_target.ty)),
                elems=(cnt_ref, val_target),
            )
            assign_pair = Assign(
                span=span,
                targets=(tup_target,),
                value=pair_expr,
                annotation=None,
            )
            new_target = val_target
            prelude = (assign_pair,)
        else:
            raise NotImplementedError(
                "enumerate() target must be a Name or (Name, Name)"
            )

        incr_stmt = AugAssign(
            span=span,
            target=cnt_ref,
            op="+=",
            value=one_lit,
        )
        new_body = prelude + tuple(stmt.body) + (incr_stmt,)

        # Emit the counter alloca + zero-store *now* so the rewritten
        # for-loop body sees ``__enum_i__`` already bound in ``self.env``.
        # Requires an active IRBuilder; _emit_for is called during
        # body lowering so the builder is positioned on the enclosing
        # block.
        ir_ty = self._map_type(int_ty)
        alloca = self._alloca_in_entry(ir_ty, name=f"{cnt_name}.addr")
        self.builder.store(ir.Constant(ir_ty, 0), alloca)
        self.env[cnt_name] = (alloca, ir_ty, int_ty)

        return _replace(stmt, target=new_target, iter=inner_iter, body=new_body)

    def _normalise_for_tuple_target(self, stmt: For) -> For:
        """Rewrite ``for (a, b) in items:`` into::

            for __foritem__<k> in items:
                a, b = __foritem__<k>
                <original body>

        The fresh Name carries the iter's element type so the existing
        tuple-unpack assignment codegen (literal / runtime branch)
        picks the right shape.
        """
        target = stmt.target
        assert isinstance(target, TupleExpr)
        tmp_name = self._fresh("foritem")
        iter_ty = stmt.iter.ty
        elem_ty: Type = DynType(name="dyn")
        if isinstance(iter_ty, ListType):
            elem_ty = iter_ty.elem
        elif isinstance(iter_ty, TupleType) and iter_ty.elems:
            first = iter_ty.elems[0]
            if all(type(e) is type(first) and e == first for e in iter_ty.elems):
                elem_ty = first
        tmp_ref = Name(
            span=target.span, ty=elem_ty, ident=tmp_name,
        )
        unpack_stmt = Assign(
            span=target.span,
            targets=(target,),
            value=tmp_ref,
            annotation=None,
        )
        new_body = (unpack_stmt,) + tuple(stmt.body)
        from dataclasses import replace as _replace
        return _replace(stmt, target=tmp_ref, body=new_body)

    def _emit_for_obj_index(self, stmt: For, iter_val: ir.Value) -> None:
        """DynType for-loop: iterate by index using ``py_obj_len`` +
        ``py_obj_getitem``. Each iteration binds the target to a
        PyObject*; downstream callers see it as DynType."""
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_obj_len"], [iter_val],
            name=self._fresh("for.obj.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="for.obj.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            alloca = self._alloca_in_entry(
                _CSTR, name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))
            slot = self.env[target_ident]

        cond_bb = fn.append_basic_block(name=self._fresh("for.obj.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.obj.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.obj.step"))
        end_bb = fn.append_basic_block(name=self._fresh("for.obj.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("for.obj.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("for.obj.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        # Box the index as a PyObject* int for py_obj_getitem.
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("for.obj.idx.box"),
        )
        elem = self.builder.call(
            self.runtime["py_obj_getitem"], [iter_val, idx_box],
            name=self._fresh("for.obj.elem"),
        )
        alloca, _, _ = slot
        self.builder.store(elem, alloca)
        self.loop_stack.append((step_bb, end_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self.builder.block.is_terminated:
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("for.obj.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1),
            name=self._fresh("for.obj.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _emit_for_str_chars(self, stmt: For, iter_val: ir.Value) -> None:
        """StrType for-loop: iterate codepoints via ``py_str_slice(s, i, i+1, 1)``.
        Target binds to a 1-char StrType slice each iteration."""
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_str_len"], [iter_val],
            name=self._fresh("for.str.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="for.str.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        one_box = self.builder.call(
            self.runtime["py_int_from_i64"], [ir.Constant(_I64, 1)],
            name=self._fresh("for.str.step"),
        )

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            alloca = self._alloca_in_entry(
                _CSTR, name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, _CSTR, StrType(name="str"))
            slot = self.env[target_ident]

        cond_bb = fn.append_basic_block(name=self._fresh("for.str.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.str.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.str.step_bb"))
        end_bb = fn.append_basic_block(name=self._fresh("for.str.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("for.str.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("for.str.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        lo_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("for.str.lo"),
        )
        hi = self.builder.add(
            cur, ir.Constant(_I64, 1), name=self._fresh("for.str.hi.i64"),
        )
        hi_box = self.builder.call(
            self.runtime["py_int_from_i64"], [hi],
            name=self._fresh("for.str.hi"),
        )
        ch = self.builder.call(
            self.runtime["py_str_slice"],
            [iter_val, lo_box, hi_box, one_box],
            name=self._fresh("for.str.ch"),
        )
        alloca, _, _ = slot
        self.builder.store(ch, alloca)
        self.loop_stack.append((step_bb, end_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self.builder.block.is_terminated:
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("for.str.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1),
            name=self._fresh("for.str.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _emit_for(self, stmt: For) -> None:
        if stmt.else_body:
            raise NotImplementedError(
                "Layer 1 does not handle for-else"
            )
        # ``for (i, x) in enumerate(xs):`` — desugar to an indexed
        # iteration so the rest of this function never sees
        # ``enumerate`` as a special iter form.
        if self._for_iter_is_enumerate(stmt):
            stmt = self._normalise_for_enumerate(stmt)
        # ``for (a, b, ...) in zip(xs, ys, ...):`` — desugar to indexed
        # iteration over the shortest-length iterable. The strict=True
        # kwarg is accepted and dropped (pcc doesn't yet raise on
        # length mismatch, but CPython-matching min-length is close
        # enough for stdlib-style usage).
        if self._for_iter_is_zip(stmt):
            stmt = self._normalise_for_zip(stmt)
        # ``for (a, b) in items:`` — normalise by introducing a fresh
        # scalar target and prepending an unpack assign to the loop body.
        if isinstance(stmt.target, TupleExpr):
            stmt = self._normalise_for_tuple_target(stmt)
        if not isinstance(stmt.target, Name):
            raise NotImplementedError(
                "Layer 1 for-loop target must be a plain Name or a "
                "TupleExpr of Names"
            )
        # ``for <name> in range(...)`` stays on the L1 fast path.
        is_range_call = (
            isinstance(stmt.iter, Call)
            and isinstance(stmt.iter.func, Name)
            and stmt.iter.func.ident == "range"
        )
        if not is_range_call:
            # CPython iterable? Use PyObject_GetIter + PyIter_Next.
            iter_val = self._emit_expr(stmt.iter)
            if iter_val in getattr(self, "_cpy_values", ()):
                return self._emit_for_cpython_iter(stmt, iter_val)
            # ListType / TupleType iteration via index: length from
            # ``py_{list,tuple}_len``, element via ``py_{list,tuple}_get``.
            iter_ty = stmt.iter.ty
            if isinstance(iter_ty, (ListType, TupleType)):
                return self._emit_for_list_index(
                    stmt, iter_val, iter_ty,
                )
            # DictType: ``for k in d:`` iterates keys. Materialise
            # ``py_dict_keys(d)`` (returns a list) and reuse the
            # list-index loop with the key type.
            if isinstance(iter_ty, DictType):
                keys_val = self.builder.call(
                    self.runtime["py_dict_keys"], [iter_val],
                    name=self._fresh("for.dict.keys"),
                )
                synthetic_ty = ListType(name="list", elem=iter_ty.key)
                return self._emit_for_list_index(
                    stmt, keys_val, synthetic_ty,
                )
            # StrType: ``for ch in s:`` iterates codepoints. Slice each
            # index into a 1-char str — keeps the whole loop libpython-
            # free. The bound target is typed str.
            if isinstance(iter_ty, StrType):
                return self._emit_for_str_chars(stmt, iter_val)
            # DynType: fall back to ``py_obj_len`` + ``py_obj_getitem``
            # — works for any pcc-native sequence (list, tuple, dict
            # keys, etc.) and stays libpython-free. The bound target is
            # tagged DynType, so subsequent uses see a PyObject*.
            if isinstance(iter_ty, DynType):
                return self._emit_for_obj_index(stmt, iter_val)
            raise NotImplementedError(
                "Layer 1 only handles 'for <name> in range(...)', a "
                "CPython-backed iterable, a list/tuple/dict/dyn "
                "container; other iterables need L3"
            )
        call = stmt.iter
        if call.kwargs:
            raise NotImplementedError("Layer 1 range() has no keyword args")
        if len(call.args) == 1:
            start_val: ir.Value = ir.Constant(_I64, 0)
            stop_val = self._emit_expr_as_i64(call.args[0])
            step_val: ir.Value = ir.Constant(_I64, 1)
        elif len(call.args) == 2:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = ir.Constant(_I64, 1)
        elif len(call.args) == 3:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = self._emit_expr_as_i64(call.args[2])
        else:
            raise L1CodegenError(
                f"range() takes 1–3 args; got {len(call.args)}"
            )

        # Allocate the loop variable (i64).
        target_name = stmt.target.ident
        existing = self.env.get(target_name)
        if existing is None:
            alloca = self._alloca_in_entry(_I64, name=f"{target_name}.addr")
            self.env[target_name] = (alloca, _I64, IntType(name="int"))
        else:
            alloca, ir_ty, _decl = existing
            if ir_ty is not _I64:
                raise L1CodegenError(
                    f"for-range target {target_name!r} already bound "
                    f"with type {ir_ty}, expected i64"
                )
        self.builder.store(start_val, alloca)

        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("for.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.step"))
        end_bb = fn.append_basic_block(name=self._fresh("for.end"))

        # Hoist step as a stable SSA value — we already have it in
        # ``step_val`` so no further work.
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(alloca, name=self._fresh(target_name))
        # Condition depends on step sign: positive step -> i<stop,
        # negative step -> i>stop. We emit both and select.
        zero64 = ir.Constant(_I64, 0)
        step_pos = self.builder.icmp_signed(">", step_val, zero64,
                                              name=self._fresh("step_pos"))
        cond_pos = self.builder.icmp_signed("<", cur, stop_val,
                                              name=self._fresh("fwd_cmp"))
        cond_neg = self.builder.icmp_signed(">", cur, stop_val,
                                              name=self._fresh("bwd_cmp"))
        cond_i1 = self.builder.select(step_pos, cond_pos, cond_neg,
                                        name=self._fresh("for_cond"))
        self.builder.cbranch(cond_i1, body_bb, end_bb)

        self.loop_stack.append((step_bb, end_bb))
        self.builder.position_at_end(body_bb)
        self._emit_stmts(stmt.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(step_bb)
        self.loop_stack.pop()

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(alloca, name=self._fresh(target_name))
        next_val = self.builder.add(cur2, step_val, name=self._fresh("next"))
        self.builder.store(next_val, alloca)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _emit_expr_as_i64(self, expr: Expr) -> ir.Value:
        """Emit an expression and coerce the result to ``i64``.

        Accepts native int/bool (fast path) and object-typed integers
        (via ``py_int_to_i64``, for e.g. a ``dict`` value that was typed
        as int but materialised as PyObject*).
        """
        value = self._emit_expr(expr)
        if isinstance(expr.ty, IntType):
            if value.type is _I64:
                return value
            if isinstance(value.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, value, expr.ty
                )
            return self.builder.sext(value, _I64, name=self._fresh("sext64"))
        if isinstance(expr.ty, BoolType):
            if value.type is _I1:
                return self.builder.zext(value, _I64, name=self._fresh("b2i"))
            if isinstance(value.type, ir.PointerType):
                i = marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, value,
                    IntType(name="int"),
                )
                return i
            return self.builder.zext(value, _I64, name=self._fresh("b2i"))
        if isinstance(expr.ty, FloatType):
            return self.builder.fptosi(value, _I64, name=self._fresh("f2i"))
        if isinstance(expr.ty, DynType) or self._is_object(expr.ty):
            # Go through the runtime.
            boxed = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, value, expr.ty
            )
            return marshal.marshal_from_object(
                self.builder, self.module, self.runtime, boxed,
                IntType(name="int"),
            )
        raise NotImplementedError(
            f"Layer 1 cannot reduce {type(expr.ty).__name__} to i64"
        )

    # ------------------------------------------------------- expressions

    def _emit_expr(self, expr: Expr) -> ir.Value:
        if isinstance(expr, IntLit):
            return ir.Constant(_I64, int(expr.value))
        if isinstance(expr, FloatLit):
            return ir.Constant(_DOUBLE, float(expr.value))
        if isinstance(expr, BoolLit):
            return ir.Constant(_I1, 1 if expr.value else 0)
        if isinstance(expr, NoneLit):
            return self._emit_none_literal()
        if isinstance(expr, StrLit):
            return self._emit_str_literal(expr.value)
        if isinstance(expr, ListExpr):
            return self._emit_list_literal(expr)
        if isinstance(expr, DictExpr):
            return self._emit_dict_literal(expr)
        if isinstance(expr, TupleExpr):
            return self._emit_tuple_literal(expr)
        if isinstance(expr, Name):
            return self._emit_name(expr)
        if isinstance(expr, Subscript):
            return self._emit_subscript_load(expr)
        if isinstance(expr, Attr):
            return self._emit_attr(expr)
        if isinstance(expr, BinOp):
            # Class-based arithmetic dunder fast path: ``a + b`` on a
            # hinted class with ``__add__`` dispatches there before
            # falling back to numeric coercion. Mirrors the compare
            # path in ``_emit_compare``.
            arith_dunder = {
                "+": "__add__",
                "-": "__sub__",
                "*": "__mul__",
                "/": "__truediv__",
                "//": "__floordiv__",
                "%": "__mod__",
            }.get(expr.op)
            if arith_dunder is not None:
                dunder = self._try_dispatch_dunder_unary(
                    expr.lhs, arith_dunder, (expr.rhs,)
                )
                if dunder is not None:
                    return dunder
            lhs = self._emit_expr(expr.lhs)
            rhs = self._emit_expr(expr.rhs)
            return self._emit_binop_value(
                expr.op, lhs, expr.lhs.ty, rhs, expr.rhs.ty, result_ty=expr.ty
            )
        if isinstance(expr, UnaryOp):
            return self._emit_unary(expr)
        if isinstance(expr, Compare):
            return self._emit_compare(expr)
        if isinstance(expr, BoolExpr):
            return self._emit_boolexpr(expr)
        if isinstance(expr, Call):
            return self._emit_call(expr)
        if isinstance(expr, IfExpr):
            return self._emit_if_expr(expr)
        raise NotImplementedError(
            f"Layer 1 does not handle expression {type(expr).__name__}"
        )

    def _emit_if_expr(self, expr: IfExpr) -> ir.Value:
        """Lower ``then_e if cond else else_e`` into a diamond CFG +
        phi. Both arms are coerced to ``expr.ty`` so downstream uses
        see a consistent SSA value type."""
        result_ty = expr.ty
        cond_val = self._emit_expr(expr.cond)
        cond_b = self._truthy(cond_val, expr.cond.ty)

        fn = self.current_function
        then_bb = fn.append_basic_block(name=self._fresh("ternary_true"))
        else_bb = fn.append_basic_block(name=self._fresh("ternary_false"))
        join_bb = fn.append_basic_block(name=self._fresh("ternary_end"))
        self.builder.cbranch(cond_b, then_bb, else_bb)

        self.builder.position_at_end(then_bb)
        then_val = self._emit_expr(expr.then_e)
        then_val = self._coerce(then_val, expr.then_e.ty, result_ty)
        then_exit = self.builder.block
        self.builder.branch(join_bb)

        self.builder.position_at_end(else_bb)
        else_val = self._emit_expr(expr.else_e)
        else_val = self._coerce(else_val, expr.else_e.ty, result_ty)
        else_exit = self.builder.block
        self.builder.branch(join_bb)

        self.builder.position_at_end(join_bb)
        phi_ty = self._map_type(result_ty)
        phi = self.builder.phi(phi_ty, name=self._fresh("ternary"))
        phi.add_incoming(then_val, then_exit)
        phi.add_incoming(else_val, else_exit)
        return phi

    # -- Collection literals ------------------------------------------

    def _emit_list_literal(self, expr: ListExpr) -> ir.Value:
        n = len(expr.elems)
        n_val = ir.Constant(_I64, n)
        lst = self.builder.call(self.runtime["py_list_new"], [n_val],
                                  name=self._fresh("list.new"))
        for el in expr.elems:
            v = self._emit_expr(el)
            v_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, el.ty
            )
            self.builder.call(
                self.runtime["py_list_append"], [lst, v_obj]
            )
        return lst

    def _emit_dict_literal(self, expr: DictExpr) -> ir.Value:
        d = self.builder.call(self.runtime["py_dict_new"], [],
                                name=self._fresh("dict.new"))
        for k_expr, v_expr in expr.pairs:
            k = self._emit_expr(k_expr)
            v = self._emit_expr(v_expr)
            k_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, k, k_expr.ty
            )
            v_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, v_expr.ty
            )
            self.builder.call(
                self.runtime["py_dict_set"], [d, k_obj, v_obj]
            )
        return d

    def _emit_tuple_literal(self, expr: TupleExpr) -> ir.Value:
        n = len(expr.elems)
        n_val = ir.Constant(_I64, n)
        tup = self.builder.call(self.runtime["py_tuple_new"], [n_val],
                                  name=self._fresh("tup.new"))
        for i, el in enumerate(expr.elems):
            v = self._emit_expr(el)
            v_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, el.ty
            )
            idx = ir.Constant(_I64, i)
            self.builder.call(
                self.runtime["py_tuple_set_item"], [tup, idx, v_obj]
            )
        return tup

    # -- Comprehensions -----------------------------------------------

    def _emit_comprehension(self, expr: Call, kind: str) -> ir.Value:
        """Lower list/set/dict comprehension sentinels into explicit
        loops over a freshly-allocated runtime container.

        The native parser lifts comprehensions to::

            _list_comp(elt,          _gen_clause(target, iter, (ifs,)), ...)
            _set_comp(elt,           _gen_clause(...), ...)
            _dict_comp(TupleExpr(k,v), _gen_clause(...), ...)

        while the CPython-AST path emits::

            __listcomp__(elt,
                         ((target, iter, (ifs,), is_async), ...))
            __setcomp__(elt,  ...)
            __dictcomp__(key, val,
                         ((target, iter, (ifs,), is_async), ...))

        Only single-generator, non-async forms with a plain ``Name``
        target are supported here.
        """
        if not isinstance(expr.func, Name):
            raise NotImplementedError("comprehension sentinel lost its name")
        sentinel = expr.func.ident
        is_native = not sentinel.startswith("__")

        # Extract the element expression + any auxiliary per-kind value.
        if kind == "dict":
            if is_native:
                if len(expr.args) < 2:
                    raise NotImplementedError(
                        "_dict_comp expects (TupleExpr(k,v), _gen_clause…)"
                    )
                elt = expr.args[0]
                if (
                    not isinstance(elt, TupleExpr)
                    or len(elt.elems) != 2
                ):
                    raise NotImplementedError(
                        "_dict_comp element must be TupleExpr(k, v)"
                    )
                key_expr, val_expr = elt.elems
                gen_args = expr.args[1:]
            else:  # __dictcomp__(key, val, ((...),))
                if len(expr.args) != 3:
                    raise NotImplementedError(
                        "__dictcomp__ expects (key, val, generators)"
                    )
                key_expr, val_expr, gens_tuple = expr.args
                gen_args = (gens_tuple,)
        else:
            if len(expr.args) < 2:
                raise NotImplementedError(
                    f"{sentinel} expects elt plus at least one generator"
                )
            elt_expr = expr.args[0]
            gen_args = expr.args[1:]

        # Decode generator clauses.
        def _native_gen(gen_call: Expr):
            if not (
                isinstance(gen_call, Call)
                and isinstance(gen_call.func, Name)
                and gen_call.func.ident == "_gen_clause"
                and len(gen_call.args) == 3
            ):
                return None
            target, iter_e, ifs_tuple = gen_call.args
            return target, iter_e, ifs_tuple, False

        def _cpy_gen(gen_tuple: Expr):
            if not (
                isinstance(gen_tuple, TupleExpr)
                and len(gen_tuple.elems) == 4
            ):
                return None
            target, iter_e, ifs_tuple, is_async = gen_tuple.elems
            async_flag = isinstance(is_async, BoolLit) and is_async.value
            return target, iter_e, ifs_tuple, async_flag

        generators: list = []
        if is_native:
            for g in gen_args:
                u = _native_gen(g)
                if u is None:
                    raise NotImplementedError(
                        f"malformed {sentinel} generator clause"
                    )
                generators.append(u)
        else:
            gens_tuple = gen_args[0]
            if not isinstance(gens_tuple, TupleExpr):
                raise NotImplementedError(
                    f"{sentinel} generators arg must be a TupleExpr"
                )
            for g in gens_tuple.elems:
                u = _cpy_gen(g)
                if u is None:
                    raise NotImplementedError(
                        f"malformed {sentinel} generator tuple"
                    )
                generators.append(u)

        for _, _, _, is_async in generators:
            if is_async:
                raise NotImplementedError(
                    "Layer 1 comprehensions are sync-only"
                )
        # Desugar tuple targets: ``for (a, b) in pairs`` becomes a fresh
        # scalar name + an unpack-assign that the inner body emits
        # before its own work. Stash the unpacks per-generator so the
        # innermost body in the chain below sees them at the right
        # nesting level.
        tuple_unpacks: list = []
        desugared = []
        for target, iter_e, ifs_tuple, is_async in generators:
            if isinstance(target, TupleExpr):
                tmp_name = self._fresh("comp_pair")
                # The temp carries the iter's *element* type so the
                # tuple-unpack runtime branch picks the right shape.
                elem_ty = DynType(name="dyn")
                if isinstance(iter_e.ty, ListType):
                    elem_ty = iter_e.ty.elem
                elif isinstance(iter_e.ty, TupleType) and iter_e.ty.elems:
                    first = iter_e.ty.elems[0]
                    if all(type(e) is type(first) and e == first
                           for e in iter_e.ty.elems):
                        elem_ty = first
                tmp_ref = Name(
                    span=target.span, ty=elem_ty, ident=tmp_name,
                )
                unpack_stmt = Assign(
                    span=target.span,
                    targets=(target,),
                    value=tmp_ref,
                    annotation=None,
                )
                desugared.append(
                    (tmp_ref, iter_e, ifs_tuple, is_async)
                )
                tuple_unpacks.append(unpack_stmt)
            elif isinstance(target, Name):
                desugared.append((target, iter_e, ifs_tuple, is_async))
                tuple_unpacks.append(None)
            else:
                raise NotImplementedError(
                    "Layer 1 comprehension target must be a Name or "
                    "TupleExpr"
                )
        generators = desugared

        # Allocate result container.
        if kind == "list":
            container = self.builder.call(
                self.runtime["py_list_new"], [ir.Constant(_I64, 0)],
                name=self._fresh("listcomp"),
            )
        elif kind == "set":
            container = self.builder.call(
                self.runtime["py_set_new"], [],
                name=self._fresh("setcomp"),
            )
        elif kind == "dict":
            container = self.builder.call(
                self.runtime["py_dict_new"], [],
                name=self._fresh("dictcomp"),
            )
        else:
            raise NotImplementedError(
                f"comprehension kind {kind!r} not supported"
            )

        def emit_innermost() -> None:
            if kind == "dict":
                k = self._emit_expr(key_expr)
                v = self._emit_expr(val_expr)
                k_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    k, key_expr.ty,
                )
                v_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, val_expr.ty,
                )
                self.builder.call(
                    self.runtime["py_dict_set"],
                    [container, k_obj, v_obj],
                )
                return
            v = self._emit_expr(elt_expr)
            v_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                v, elt_expr.ty,
            )
            fn_name = (
                "py_list_append" if kind == "list" else "py_set_add"
            )
            self.builder.call(
                self.runtime[fn_name], [container, v_obj],
            )

        # Chain the generators from innermost to outermost; each wrapper
        # applies its own if-guards before running the next-level body.
        def make_body_for(idx: int, inner):
            target, iter_e, ifs_tuple, _ = generators[idx]
            if_exprs: tuple = ()
            if isinstance(ifs_tuple, TupleExpr):
                if_exprs = ifs_tuple.elems
            unpack_stmt = (
                tuple_unpacks[idx] if idx < len(tuple_unpacks) else None
            )

            def body() -> None:
                # Tuple target desugar: run the unpack assign before
                # any if-guards so the filter expressions see the
                # unpacked names.
                if unpack_stmt is not None:
                    self._emit_assign(unpack_stmt)
                if_exits: list = []
                for cond_expr in if_exprs:
                    cond_val = self._emit_expr(cond_expr)
                    cond_b = self._truthy(cond_val, cond_expr.ty)
                    keep_bb = self.current_function.append_basic_block(
                        name=self._fresh(f"{kind}comp.keep")
                    )
                    skip_bb = self.current_function.append_basic_block(
                        name=self._fresh(f"{kind}comp.skip")
                    )
                    self.builder.cbranch(cond_b, keep_bb, skip_bb)
                    self.builder.position_at_end(keep_bb)
                    if_exits.append(skip_bb)
                inner()
                for skip_bb in reversed(if_exits):
                    if not self.builder.block.is_terminated:
                        self.builder.branch(skip_bb)
                    self.builder.position_at_end(skip_bb)

            return body, target, iter_e

        current_body = emit_innermost
        for i in range(len(generators) - 1, -1, -1):
            wrapped, tgt, it = make_body_for(i, current_body)
            # Close over the current wrapped body + tgt + it so the
            # outer loop dispatches the correct generator.
            def step(wrapped=wrapped, tgt=tgt, it=it):
                self._emit_comprehension_generator(tgt, it, wrapped)
            current_body = step
        current_body()
        return container

    def _emit_enumerate_loop_in_comp(
        self, target: Name, inner_iter: Expr, emit_body,
    ) -> None:
        """Lower ``enumerate(xs)`` inside a comprehension.

        The comprehension's tuple-target desugar already rewrote
        ``[... for (i, x) in enumerate(xs)]`` so ``target`` is a
        fresh scalar Name carrying the iter's element type
        (``tuple[int, X]``); the ``i, x = __comp_pair__`` unpack
        statement is prepended to ``emit_body``.

        We build a 2-element tuple per iteration and bind it to
        ``target`` so the unpack sees the expected shape.
        """
        # Iterate ``inner_iter`` using the existing generator dispatch.
        inner_name = self._fresh("enum_val")
        inner_target = Name(
            span=target.span,
            ty=(inner_iter.ty.elem
                if isinstance(inner_iter.ty, ListType)
                else DynType(name="dyn")),
            ident=inner_name,
        )
        # Counter alloca on entry block.
        cnt_name = self._fresh("enum_i")
        cnt_alloca = self._alloca_in_entry(_I64, name=f"{cnt_name}.addr")
        self.builder.store(ir.Constant(_I64, 0), cnt_alloca)
        self.env[cnt_name] = (cnt_alloca, _I64, IntType(name="int"))
        # Target alloca (holds the 2-tuple PyObject).
        target_alloca = self._alloca_in_entry(
            _CSTR, name=f"{target.ident}.addr",
        )
        self.env[target.ident] = (
            target_alloca, _CSTR, DynType(name="dyn"),
        )

        def body_wrap() -> None:
            # Build ``(i, inner_val)`` tuple PyObject* and bind
            # to ``target``.
            i_val = self.builder.load(
                cnt_alloca, name=self._fresh(cnt_name),
            )
            i_box = self.builder.call(
                self.runtime["py_int_from_i64"], [i_val],
                name=self._fresh("enum.i.box"),
            )
            # Fetch inner value from the binding set by the nested
            # generator loop.
            inner_slot = self.env[inner_name]
            inner_alloca, inner_ir_ty, inner_ty = inner_slot
            inner_val = self.builder.load(
                inner_alloca, name=self._fresh(inner_name),
            )
            inner_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                inner_val, inner_ty,
            )
            pair = self.builder.call(
                self.runtime["py_tuple_new"], [ir.Constant(_I64, 2)],
                name=self._fresh("enum.pair.new"),
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [pair, ir.Constant(_I64, 0), i_box],
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [pair, ir.Constant(_I64, 1), inner_obj],
            )
            self.builder.store(pair, target_alloca)
            # Run user's emit_body — this will use the unpack stmt
            # that draws from target_alloca.
            emit_body()
            # Bump counter.
            cur = self.builder.load(
                cnt_alloca, name=self._fresh("enum.i.cur"),
            )
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("enum.i.next"),
            )
            self.builder.store(nxt, cnt_alloca)

        self._emit_comprehension_generator(
            inner_target, inner_iter, body_wrap,
        )

    def _emit_comprehension_indexed(
        self, target: Name, iter_val: ir.Value, iter_ty,
        emit_body,
    ) -> None:
        """Indexed iteration over a typed list / tuple: same shape
        as ``_emit_for_list_index`` but the inner block runs
        ``emit_body()`` instead of a user body statement list."""
        fn = self.current_function
        iter_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, iter_val, iter_ty,
        )
        if isinstance(iter_ty, ListType):
            len_helper = "py_list_len"
            get_helper = "py_list_get"
            elem_ty = iter_ty.elem
        else:
            len_helper = "py_tuple_len"
            get_helper = "py_tuple_get"
            elem_ty = DynType(name="dyn")
        n_val = self.builder.call(
            self.runtime[len_helper], [iter_obj],
            name=self._fresh("comp.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="comp.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = target.ident
        if isinstance(elem_ty, DynType):
            target_ir_ty = _CSTR
        else:
            target_ir_ty = self._map_type(elem_ty)
        alloca = self._alloca_in_entry(
            target_ir_ty, name=f"{target_ident}.addr",
        )
        self.env[target_ident] = (alloca, target_ir_ty, elem_ty)

        cond_bb = fn.append_basic_block(name=self._fresh("comp.idx.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.idx.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.idx.step"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.idx.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("comp.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("comp.cond"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        elem_obj = self.builder.call(
            self.runtime[get_helper], [iter_obj, cur],
            name=self._fresh("comp.elem"),
        )
        if isinstance(elem_ty, DynType):
            self.builder.store(elem_obj, alloca)
        else:
            native_val = marshal.marshal_from_object(
                self.builder, self.module, self.runtime,
                elem_obj, elem_ty,
            )
            self.builder.store(native_val, alloca)
        emit_body()
        if not self.builder.block.is_terminated:
            self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("comp.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1), name=self._fresh("comp.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)

    def _emit_comprehension_obj_indexed(
        self, target: Name, iter_val: ir.Value, emit_body,
    ) -> None:
        """Generic DynType iteration via ``py_obj_len`` +
        ``py_obj_getitem`` — mirrors ``_emit_for_obj_index``."""
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_obj_len"], [iter_val],
            name=self._fresh("comp.obj.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="comp.obj.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = target.ident
        alloca = self._alloca_in_entry(
            _CSTR, name=f"{target_ident}.addr",
        )
        self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))

        cond_bb = fn.append_basic_block(name=self._fresh("comp.obj.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.obj.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.obj.step"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.obj.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("comp.obj.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("comp.obj.cond"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("comp.obj.idx.box"),
        )
        elem = self.builder.call(
            self.runtime["py_obj_getitem"], [iter_val, idx_box],
            name=self._fresh("comp.obj.elem"),
        )
        self.builder.store(elem, alloca)
        emit_body()
        if not self.builder.block.is_terminated:
            self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("comp.obj.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1), name=self._fresh("comp.obj.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)

    def _emit_comprehension_generator(
        self, target: Name, iter_e: Expr, emit_body,
    ) -> None:
        """Emit a ``for target in iter_e:`` loop that invokes
        ``emit_body()`` each iteration. Supports ``range(...)`` iters,
        ``enumerate(xs)`` (desugar to indexed loop with a synthetic
        counter), CPython iterables, typed list / tuple / dict
        containers, and generic DynType containers via ``py_obj_len``
        + ``py_obj_getitem``."""
        # Fast path: range(...) iter.
        if (
            isinstance(iter_e, Call)
            and isinstance(iter_e.func, Name)
            and iter_e.func.ident == "range"
        ):
            self._emit_range_loop(target, iter_e, emit_body)
            return
        # enumerate(xs) — the comprehension tuple-target desugaring
        # above synthesises ``__comp_pair__`` whose value we build
        # here as a ``(i, xs_elem)`` pair to feed into the unpack.
        if (
            isinstance(iter_e, Call)
            and isinstance(iter_e.func, Name)
            and iter_e.func.ident == "enumerate"
            and len(iter_e.args) == 1
            and not iter_e.kwargs
        ):
            self._emit_enumerate_loop_in_comp(
                target, iter_e.args[0], emit_body,
            )
            return
        iter_val = self._emit_expr(iter_e)
        if iter_val in getattr(self, "_cpy_values", ()):
            self._emit_cpy_iter_loop(target, iter_val, emit_body)
            return
        iter_ty = iter_e.ty
        if isinstance(iter_ty, (ListType, TupleType)):
            self._emit_comprehension_indexed(
                target, iter_val, iter_ty, emit_body,
            )
            return
        if isinstance(iter_ty, DictType):
            keys_val = self.builder.call(
                self.runtime["py_dict_keys"], [iter_val],
                name=self._fresh("comp.dict.keys"),
            )
            synthetic_ty = ListType(name="list", elem=iter_ty.key)
            self._emit_comprehension_indexed(
                target, keys_val, synthetic_ty, emit_body,
            )
            return
        if isinstance(iter_ty, StrType):
            self._emit_comprehension_str_chars(
                target, iter_val, emit_body,
            )
            return
        if isinstance(iter_ty, DynType):
            self._emit_comprehension_obj_indexed(
                target, iter_val, emit_body,
            )
            return
        raise NotImplementedError(
            "Layer 1 comprehension iter must be range(...) or a "
            "CPython-backed iterable"
        )

    def _emit_comprehension_str_chars(
        self, target: Name, iter_val: ir.Value, emit_body,
    ) -> None:
        """StrType comprehension iter: slice each char."""
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_str_len"], [iter_val],
            name=self._fresh("comp.str.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="comp.str.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        one_box = self.builder.call(
            self.runtime["py_int_from_i64"], [ir.Constant(_I64, 1)],
            name=self._fresh("comp.str.step"),
        )
        tgt_name = target.ident
        if tgt_name not in self.env:
            alloca = self._alloca_in_entry(
                _CSTR, name=f"{tgt_name}.addr",
            )
            self.env[tgt_name] = (alloca, _CSTR, StrType(name="str"))

        cond_bb = fn.append_basic_block(name=self._fresh("comp.str.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.str.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.str.step_bb"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.str.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("comp.str.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("comp.str.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        lo_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("comp.str.lo"),
        )
        hi = self.builder.add(
            cur, ir.Constant(_I64, 1), name=self._fresh("comp.str.hi.i64"),
        )
        hi_box = self.builder.call(
            self.runtime["py_int_from_i64"], [hi],
            name=self._fresh("comp.str.hi"),
        )
        ch = self.builder.call(
            self.runtime["py_str_slice"],
            [iter_val, lo_box, hi_box, one_box],
            name=self._fresh("comp.str.ch"),
        )
        alloca, _, _ = self.env[tgt_name]
        self.builder.store(ch, alloca)
        emit_body()
        if not self.builder.block.is_terminated:
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("comp.str.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1),
            name=self._fresh("comp.str.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _emit_range_loop(
        self, target: Name, call: Call, emit_body,
    ) -> None:
        if call.kwargs:
            raise NotImplementedError(
                "range() with keyword args not supported in comprehension"
            )
        if len(call.args) == 1:
            start_val: ir.Value = ir.Constant(_I64, 0)
            stop_val = self._emit_expr_as_i64(call.args[0])
            step_val: ir.Value = ir.Constant(_I64, 1)
        elif len(call.args) == 2:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = ir.Constant(_I64, 1)
        elif len(call.args) == 3:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = self._emit_expr_as_i64(call.args[2])
        else:
            raise L1CodegenError(
                f"range() takes 1–3 args; got {len(call.args)}"
            )
        target_name = target.ident
        existing = self.env.get(target_name)
        if existing is None:
            alloca = self._alloca_in_entry(_I64, name=f"{target_name}.addr")
            self.env[target_name] = (alloca, _I64, IntType(name="int"))
        else:
            alloca, ir_ty, _decl = existing
            if ir_ty is not _I64:
                raise L1CodegenError(
                    f"comprehension target {target_name!r} bound to "
                    f"{ir_ty}, expected i64"
                )
        self.builder.store(start_val, alloca)
        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("comp.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.step"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(alloca, name=self._fresh(target_name))
        zero64 = ir.Constant(_I64, 0)
        step_pos = self.builder.icmp_signed(
            ">", step_val, zero64, name=self._fresh("step_pos"),
        )
        cond_pos = self.builder.icmp_signed(
            "<", cur, stop_val, name=self._fresh("fwd_cmp"),
        )
        cond_neg = self.builder.icmp_signed(
            ">", cur, stop_val, name=self._fresh("bwd_cmp"),
        )
        cond_i1 = self.builder.select(
            step_pos, cond_pos, cond_neg, name=self._fresh("comp_cond"),
        )
        self.builder.cbranch(cond_i1, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        emit_body()
        if not self.builder.block.is_terminated:
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(alloca, name=self._fresh(target_name))
        next_val = self.builder.add(cur2, step_val, name=self._fresh("next"))
        self.builder.store(next_val, alloca)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _emit_cpy_iter_loop(
        self, target: Name, iter_src: ir.Value, emit_body,
    ) -> None:
        """Shared CPython-iteration loop for comprehensions."""
        target_name = target.ident
        fn = self.current_function
        iter_obj = self.builder.call(
            self.runtime["py_cpy_iter"], [iter_src],
            name=self._fresh("comp.iter"),
        )
        cond_bb = fn.append_basic_block(name=self._fresh("comp.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.body"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        nxt = self.builder.call(
            self.runtime["py_cpy_iter_next"], [iter_obj],
            name=self._fresh("comp.next"),
        )
        null_p = ir.Constant(nxt.type, None)
        is_done = self.builder.icmp_unsigned(
            "==", nxt, null_p, name=self._fresh("comp.done"),
        )
        self.builder.cbranch(is_done, end_bb, body_bb)
        self.builder.position_at_end(body_bb)
        existing = self.env.get(target_name)
        if existing is None:
            alloca = self._alloca_in_entry(
                nxt.type, name=f"{target_name}.addr",
            )
            self.env[target_name] = (alloca, nxt.type, DynType(name="dyn"))
            if not hasattr(self, "_cpy_env_flags"):
                self._cpy_env_flags = {}
            self._cpy_env_flags[target_name] = True
        else:
            alloca, _, _ = existing
        self.builder.store(nxt, alloca)
        emit_body()
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)

    # -- Subscript / attribute load -----------------------------------

    def _emit_slice_load(self, expr: Subscript) -> ir.Value:
        """Lower ``xs[lo:hi:step]`` for list / tuple / str. Each bound
        lowers to a PyObject* (None when absent) and dispatches to the
        type-specific ``py_{list,tuple,str}_slice`` runtime helper —
        all of which return a freshly-allocated container of the
        same kind."""
        sl = expr.idx
        assert isinstance(sl, Slice)
        obj = self._emit_expr(expr.obj)
        obj_ty = expr.obj.ty

        def _bound(e: Optional[Expr]) -> ir.Value:
            if e is None:
                return ir.Constant(_CSTR, None)
            v = self._emit_expr(e)
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, e.ty,
            )

        lo = _bound(sl.lo)
        hi = _bound(sl.hi)
        step = _bound(sl.step)
        if isinstance(obj_ty, ListType):
            helper = "py_list_slice"
        elif isinstance(obj_ty, TupleType):
            helper = "py_list_slice"  # py_tuple_slice doesn't exist; treat as list
        elif isinstance(obj_ty, StrType):
            helper = "py_str_slice"
        elif isinstance(obj_ty, DynType):
            # Can't pick statically; default to list slicer (works
            # correctly for list, but will mis-type tuples / strs).
            helper = "py_list_slice"
        else:
            raise NotImplementedError(
                f"Layer 1 slice on type {type(obj_ty).__name__} "
                "not supported"
            )
        return self.builder.call(
            self.runtime[helper], [obj, lo, hi, step],
            name=self._fresh("slice"),
        )

    def _emit_subscript_load(self, expr: Subscript) -> ir.Value:
        # Slice form ``xs[lo:hi:step]`` routes to the type-specific
        # runtime slicer before any dunder / scalar-index path.
        if isinstance(expr.idx, Slice):
            return self._emit_slice_load(expr)
        # Class-based __getitem__ fast path: if ``expr.obj`` is a Name
        # bound to a known class that defines ``__getitem__``, dispatch
        # directly rather than going through ``py_obj_getitem`` (which
        # doesn't yet do user-class dispatch in the runtime).
        dunder = self._try_dispatch_dunder_unary(expr, "__getitem__", (expr.idx,))
        if dunder is not None:
            return dunder

        obj = self._emit_expr(expr.obj)
        # CPython-backed object: dispatch via py_cpy_getitem
        # (PyObject_GetItem) with a boxed key. Result is a fresh
        # CPython reference — tagged for the caller.
        if obj in getattr(self, "_cpy_values", ()):
            key_val = self._emit_expr(expr.idx)
            cpy_key, owned = self._marshal_to_cpython(key_val, expr.idx.ty)
            result = self.builder.call(
                self.runtime["py_cpy_getitem"], [obj, cpy_key],
                name=self._fresh("cpy.getitem"),
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_key])
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(result)
            return result
        obj_ty = expr.obj.ty
        if isinstance(obj_ty, ListType):
            idx = self._emit_expr_as_i64(expr.idx)
            got = self.builder.call(
                self.runtime["py_list_get"], [obj, idx],
                name=self._fresh("list.get"),
            )
            return self._coerce_from_object(got, obj_ty.elem)
        if isinstance(obj_ty, TupleType):
            idx = self._emit_expr_as_i64(expr.idx)
            got = self.builder.call(
                self.runtime["py_tuple_get"], [obj, idx],
                name=self._fresh("tup.get"),
            )
            # Best-effort: if all tuple elements share one type, unbox
            # to that; otherwise leave as PyObject*.
            elem_ty: Type
            if obj_ty.elems:
                first = obj_ty.elems[0]
                if all(type(e) is type(first) for e in obj_ty.elems):
                    elem_ty = first
                else:
                    return got
            else:
                return got
            return self._coerce_from_object(got, elem_ty)
        if isinstance(obj_ty, DictType):
            key_obj = self._emit_as_object(expr.idx)
            got = self.builder.call(
                self.runtime["py_dict_get"], [obj, key_obj],
                name=self._fresh("dict.get"),
            )
            # Phase 3 will raise KeyError on NULL result; for now, pass
            # the PyObject* through (callers can test against NULL).
            return self._coerce_from_object(got, obj_ty.value)
        if isinstance(obj_ty, StrType):
            idx_obj = self._emit_as_object(expr.idx)
            return self.builder.call(
                self.runtime["py_str_index"], [obj, idx_obj],
                name=self._fresh("str.idx"),
            )
        # Generic dyn/object fallback.
        key_obj = self._emit_as_object(expr.idx)
        return self.builder.call(
            self.runtime["py_obj_getitem"], [obj, key_obj],
            name=self._fresh("obj.getitem"),
        )

    def _emit_attr(self, expr: Attr) -> ir.Value:
        # CPython-backed fast path: if the object evaluates to a
        # CPython ``PyObject*`` (either bound directly via a Name in
        # _cpy_module_env / _cpy_env_flags, or through a nested
        # ``a.b.c`` chain where an inner node is CPython), route the
        # attribute load through py_cpy_getattr. Otherwise fall
        # through to the pcc native path.
        if isinstance(expr.obj, Name):
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(expr.obj.ident)
            if cpy_gv is not None:
                mod_val = self.builder.load(
                    cpy_gv, name=self._fresh(f"cpy.{expr.obj.ident}")
                )
                return self._emit_cpy_attr(mod_val, expr.name)
            if getattr(self, "_cpy_env_flags", {}).get(expr.obj.ident, False):
                obj_val = self._emit_expr(expr.obj)
                return self._emit_cpy_attr(obj_val, expr.name)
        if isinstance(expr.obj, (Attr, Subscript, Call)):
            chain_val = self._emit_expr(expr.obj)
            if chain_val in getattr(self, "_cpy_values", ()):
                return self._emit_cpy_attr(chain_val, expr.name)

        # Property getter fast path: if the attribute is a @property on
        # a hinted class, dispatch to the getter function.
        if isinstance(expr.obj, Name):
            hint = self.env_class_hint.get(expr.obj.ident)
            if hint is not None:
                info = self._resolve_property_mro(hint, expr.name)
                if info is not None:
                    getter = info.properties[expr.name]
                    obj_val = self._emit_expr(expr.obj)
                    return self.builder.call(
                        getter, [obj_val],
                        name=self._fresh(f"prop.{expr.name}"),
                    )

        # Fast path for ``self.<attr>`` inside a method body: use the
        # declared-field index when known, otherwise fall through to the
        # generic ``py_obj_getattr`` call.
        current_class = getattr(self, "current_class", None)
        if (
            current_class is not None
            and isinstance(expr.obj, Name)
            and expr.obj.ident == "self"
        ):
            # self.<prop> — dispatch to getter when present.
            info_p = self._resolve_property_mro(current_class.name, expr.name)
            if info_p is not None:
                getter = info_p.properties[expr.name]
                self_val = self.builder.load(
                    self.env["self"][0], name=self._fresh("self")
                )
                return self.builder.call(
                    getter, [self_val],
                    name=self._fresh(f"self.prop.{expr.name}"),
                )
            self_val = self.builder.load(
                self.env["self"][0], name=self._fresh("self")
            )
            return self.class_lowering.emit_self_attr_load(
                current_class, expr.name, self_val
            )

        obj = self._emit_expr(expr.obj)
        # Any object goes through py_obj_getattr; if the object is
        # ``None`` at runtime the runtime lib raises AttributeError —
        # that's the correct Python semantic (no segfault).
        name_ptr = self._attr_name_ptr(expr.name)
        return self.builder.call(
            self.runtime["py_obj_getattr"], [obj, name_ptr],
            name=self._fresh(f"attr.{expr.name}"),
        )

    def _emit_attr_store(self, target: Attr, value_expr: Expr) -> None:
        # Property setter fast path.
        if isinstance(target.obj, Name):
            hint = self.env_class_hint.get(target.obj.ident)
            if hint is not None:
                info = self._resolve_property_setter_mro(hint, target.name)
                if info is not None:
                    setter_fn = info.property_setters[target.name]
                    obj_val = self._emit_expr(target.obj)
                    value = self._emit_expr(value_expr)
                    if len(setter_fn.args) >= 2:
                        param_ty = setter_fn.args[1].type
                        if isinstance(param_ty, ir.IntType) and param_ty.width == 64:
                            value = self._coerce(value, value_expr.ty, IntType(name="int"))
                        elif isinstance(param_ty, ir.PointerType):
                            value = marshal.marshal_to_object(
                                self.builder, self.module, self.runtime,
                                value, value_expr.ty,
                            )
                    self.builder.call(setter_fn, [obj_val, value])
                    return

        current_class = getattr(self, "current_class", None)
        if (
            current_class is not None
            and isinstance(target.obj, Name)
            and target.obj.ident == "self"
        ):
            self_val = self.builder.load(
                self.env["self"][0], name=self._fresh("self")
            )
            # The value needs to reach the runtime as PyObject*.
            value = self._emit_as_object(value_expr)
            self.class_lowering.emit_self_attr_store(
                current_class, target.name, self_val, value
            )
            return
        # Generic fallback: obj.name = value via py_obj_setattr.
        obj = self._emit_expr(target.obj)
        value = self._emit_as_object(value_expr)
        name_ptr = self._attr_name_ptr(target.name)
        self.builder.call(
            self.runtime["py_obj_setattr"], [obj, name_ptr, value]
        )

    def _emit_as_object(self, expr: Expr) -> ir.Value:
        """Emit ``expr`` and marshal the result to PyObject*."""
        v = self._emit_expr(expr)
        return marshal.marshal_to_object(
            self.builder, self.module, self.runtime, v, expr.ty
        )

    def _emit_name(self, expr: Name) -> ir.Value:
        slot = self.env.get(expr.ident)
        if slot is None:
            # Module-level dunder that pcc can resolve at compile time
            # when the file is being compiled as a top-level script.
            # Matches CPython's behavior for ``python myscript.py``.
            if expr.ident == "__name__":
                return self._emit_str_literal("__main__")
            # Module-level constant? Emit a load of the global.
            module_globals = getattr(self, "_module_globals", {})
            if expr.ident in module_globals:
                gv, _declared_ty = module_globals[expr.ident]
                return self.builder.load(
                    gv, name=self._fresh(expr.ident),
                )
            # Fall back to the module-wide CPython import registry for
            # ``from os import sep`` / ``import sys`` style bindings.
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(expr.ident)
            if cpy_gv is not None:
                val = self.builder.load(
                    cpy_gv, name=self._fresh(f"cpy.{expr.ident}")
                )
                if not hasattr(self, "_cpy_values"):
                    self._cpy_values = set()
                self._cpy_values.add(val)
                return val
            raise L1CodegenError(
                f"reference to unbound name {expr.ident!r}"
            )
        alloca, ir_ty, _ = slot
        val = self.builder.load(alloca, name=self._fresh(expr.ident))
        # Re-tag as a CPython value when the binding was recorded as
        # one. Without this, downstream coercions see a bare DynType
        # and route through the pcc (non-CPython) unbox path.
        if getattr(self, "_cpy_env_flags", {}).get(expr.ident, False):
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(val)
        return val

    # -- BinOp ---------------------------------------------------------

    def _emit_binop_value(
        self,
        op: str,
        lhs: ir.Value,
        lhs_ty: Type,
        rhs: ir.Value,
        rhs_ty: Type,
        result_ty: Type,
    ) -> ir.Value:
        # Phase 2 object ops (str concat / repeat, list concat). Keeping
        # the dispatch here lets augassign (``s += "x"``, ``lst += ...``)
        # take the same code path as the value-form expression.
        if op == "+" and isinstance(lhs_ty, StrType) and isinstance(rhs_ty, StrType):
            return self.builder.call(
                self.runtime["py_str_concat"], [lhs, rhs],
                name=self._fresh("str.concat"),
            )
        if op == "*" and isinstance(lhs_ty, StrType) and isinstance(rhs_ty, (IntType, BoolType)):
            rhs_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, rhs, rhs_ty
            )
            return self.builder.call(
                self.runtime["py_str_repeat"], [lhs, rhs_obj],
                name=self._fresh("str.rep"),
            )
        if op == "*" and isinstance(rhs_ty, StrType) and isinstance(lhs_ty, (IntType, BoolType)):
            lhs_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, lhs, lhs_ty
            )
            return self.builder.call(
                self.runtime["py_str_repeat"], [rhs, lhs_obj],
                name=self._fresh("str.rep"),
            )
        if op == "+" and isinstance(lhs_ty, ListType) and isinstance(rhs_ty, ListType):
            return self.builder.call(
                self.runtime["py_list_concat"], [lhs, rhs],
                name=self._fresh("list.concat"),
            )

        # Shortcut: bitwise ops + shifts are integer-only.
        if op in ("&", "|", "^", "<<", ">>"):
            lv = self._to_int64(lhs, lhs_ty)
            rv = self._to_int64(rhs, rhs_ty)
            if op == "&":
                return self.builder.and_(lv, rv, name=self._fresh("and"))
            if op == "|":
                return self.builder.or_(lv, rv, name=self._fresh("or"))
            if op == "^":
                return self.builder.xor(lv, rv, name=self._fresh("xor"))
            if op == "<<":
                return self.builder.shl(lv, rv, name=self._fresh("shl"))
            if op == ">>":
                return self.builder.ashr(lv, rv, name=self._fresh("ashr"))

        # Python ``/`` always returns float even if both operands are
        # integers.
        if op == "/":
            lf = self._to_double(lhs, lhs_ty)
            rf = self._to_double(rhs, rhs_ty)
            return self.builder.fdiv(lf, rf, name=self._fresh("fdiv"))

        # Pick the result's IR type: float if either operand is float.
        if isinstance(lhs_ty, FloatType) or isinstance(rhs_ty, FloatType):
            lf = self._to_double(lhs, lhs_ty)
            rf = self._to_double(rhs, rhs_ty)
            return self._emit_binop_float(op, lf, rf)

        # String ops: ``s * n`` / ``n * s`` → ``py_str_repeat``;
        # ``s + t`` → ``py_str_concat``. Any Dyn operand is boxed
        # via the marshal helper so the runtime's py_str_* helpers
        # see PyObject*.
        if op == "*" and (
            isinstance(lhs_ty, StrType) or isinstance(rhs_ty, StrType)
        ):
            if isinstance(lhs_ty, StrType):
                s_val, s_ty = lhs, lhs_ty
                n_val, n_ty = rhs, rhs_ty
            else:
                s_val, s_ty = rhs, rhs_ty
                n_val, n_ty = lhs, lhs_ty
            s_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, s_val, s_ty,
            )
            n_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, n_val, n_ty,
            )
            return self.builder.call(
                self.runtime["py_str_repeat"], [s_obj, n_obj],
                name=self._fresh("str.repeat"),
            )
        if op == "+" and (
            (isinstance(lhs_ty, StrType) or isinstance(rhs_ty, StrType))
            and (
                isinstance(lhs_ty, (StrType, DynType))
                and isinstance(rhs_ty, (StrType, DynType))
            )
        ):
            l_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, lhs, lhs_ty,
            )
            r_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, rhs, rhs_ty,
            )
            return self.builder.call(
                self.runtime["py_str_concat"], [l_obj, r_obj],
                name=self._fresh("str.concat"),
            )

        # Integer (and bool-as-int) path.
        lv = self._to_int64(lhs, lhs_ty)
        rv = self._to_int64(rhs, rhs_ty)
        return self._emit_binop_int(op, lv, rv)

    def _emit_binop_int(self, op: str, lv: ir.Value, rv: ir.Value) -> ir.Value:
        if op == "+":
            return self.builder.add(lv, rv, name=self._fresh("add"))
        if op == "-":
            return self.builder.sub(lv, rv, name=self._fresh("sub"))
        if op == "*":
            return self.builder.mul(lv, rv, name=self._fresh("mul"))
        if op == "//":
            return self._python_floordiv_i64(lv, rv)
        if op == "%":
            return self._python_mod_i64(lv, rv)
        if op == "**":
            raise NotImplementedError(
                "Layer 1 does not handle ** (int power); use Phase 2 runtime"
            )
        raise NotImplementedError(f"Layer 1 int binop {op!r} not supported")

    def _emit_binop_float(self, op: str, lv: ir.Value, rv: ir.Value) -> ir.Value:
        if op == "+":
            return self.builder.fadd(lv, rv, name=self._fresh("fadd"))
        if op == "-":
            return self.builder.fsub(lv, rv, name=self._fresh("fsub"))
        if op == "*":
            return self.builder.fmul(lv, rv, name=self._fresh("fmul"))
        if op == "//":
            # Python float-floor div: floor(a / b).
            q = self.builder.fdiv(lv, rv, name=self._fresh("fdiv_q"))
            # Inline llvm.floor.f64 intrinsic.
            floor_fn = self._get_floor_intrinsic()
            return self.builder.call(floor_fn, [q], name=self._fresh("ffloor"))
        if op == "%":
            # Python float mod uses fmod + correction; simplest is to
            # call libc ``fmod`` and adjust sign.
            fmod_fn = self._get_fmod_function()
            r = self.builder.call(fmod_fn, [lv, rv], name=self._fresh("fmod"))
            # Correct sign: if (r != 0) and (sign(r) != sign(b)) → r += b.
            zero_f = ir.Constant(_DOUBLE, 0.0)
            r_nz = self.builder.fcmp_ordered("!=", r, zero_f,
                                              name=self._fresh("fmod_nz"))
            r_neg = self.builder.fcmp_ordered("<", r, zero_f,
                                              name=self._fresh("fmod_r_neg"))
            b_neg = self.builder.fcmp_ordered("<", rv, zero_f,
                                              name=self._fresh("fmod_b_neg"))
            sign_diff = self.builder.xor(r_neg, b_neg,
                                          name=self._fresh("fmod_sign_diff"))
            need_fix = self.builder.and_(r_nz, sign_diff,
                                           name=self._fresh("fmod_fix"))
            corrected = self.builder.fadd(r, rv, name=self._fresh("fmod_corr"))
            return self.builder.select(need_fix, corrected, r,
                                         name=self._fresh("fmod_res"))
        if op == "**":
            raise NotImplementedError(
                "Layer 1 does not handle ** (float power)"
            )
        raise NotImplementedError(f"Layer 1 float binop {op!r} not supported")

    def _python_floordiv_i64(self, a: ir.Value, b: ir.Value) -> ir.Value:
        """Python-correct signed floor division on i64.

        ``q = a sdiv b; r = a srem b; if (r != 0) && ((r < 0) != (b < 0))
        then q = q - 1``.
        """
        q = self.builder.sdiv(a, b, name=self._fresh("q"))
        r = self.builder.srem(a, b, name=self._fresh("r"))
        zero = ir.Constant(_I64, 0)
        one = ir.Constant(_I64, 1)
        r_nz = self.builder.icmp_signed("!=", r, zero,
                                         name=self._fresh("r_nz"))
        r_neg = self.builder.icmp_signed("<", r, zero,
                                          name=self._fresh("r_neg"))
        b_neg = self.builder.icmp_signed("<", b, zero,
                                          name=self._fresh("b_neg"))
        sign_diff = self.builder.xor(r_neg, b_neg,
                                      name=self._fresh("sign_diff"))
        need_fix = self.builder.and_(r_nz, sign_diff,
                                      name=self._fresh("need_fix"))
        q_minus_1 = self.builder.sub(q, one, name=self._fresh("q_fix"))
        return self.builder.select(need_fix, q_minus_1, q,
                                     name=self._fresh("floordiv"))

    def _python_mod_i64(self, a: ir.Value, b: ir.Value) -> ir.Value:
        """Python-correct signed mod on i64; sign follows divisor.

        ``r = a srem b; if (r != 0) && ((r < 0) != (b < 0)) then r = r + b``.
        """
        r = self.builder.srem(a, b, name=self._fresh("r"))
        zero = ir.Constant(_I64, 0)
        r_nz = self.builder.icmp_signed("!=", r, zero,
                                         name=self._fresh("r_nz"))
        r_neg = self.builder.icmp_signed("<", r, zero,
                                          name=self._fresh("r_neg"))
        b_neg = self.builder.icmp_signed("<", b, zero,
                                          name=self._fresh("b_neg"))
        sign_diff = self.builder.xor(r_neg, b_neg,
                                      name=self._fresh("sign_diff"))
        need_fix = self.builder.and_(r_nz, sign_diff,
                                      name=self._fresh("need_fix"))
        r_plus_b = self.builder.add(r, b, name=self._fresh("r_fix"))
        return self.builder.select(need_fix, r_plus_b, r,
                                     name=self._fresh("mod"))

    def _get_floor_intrinsic(self) -> ir.Function:
        name = "llvm.floor.f64"
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_DOUBLE, [_DOUBLE])
        fn = ir.Function(self.module, fnty, name=name)
        fn.linkage = "external"
        return fn

    def _get_fmod_function(self) -> ir.Function:
        name = "fmod"
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_DOUBLE, [_DOUBLE, _DOUBLE])
        fn = ir.Function(self.module, fnty, name=name)
        fn.linkage = "external"
        return fn

    # -- UnaryOp -------------------------------------------------------

    def _emit_unary(self, expr: UnaryOp) -> ir.Value:
        operand = self._emit_expr(expr.operand)
        ty = expr.operand.ty
        if expr.op == "+":
            return operand
        if expr.op == "-":
            if isinstance(ty, FloatType):
                zero = ir.Constant(_DOUBLE, 0.0)
                return self.builder.fsub(zero, operand,
                                           name=self._fresh("fneg"))
            ival = self._to_int64(operand, ty)
            return self.builder.neg(ival, name=self._fresh("neg"))
        if expr.op == "~":
            ival = self._to_int64(operand, ty)
            return self.builder.not_(ival, name=self._fresh("bnot"))
        if expr.op == "not":
            b = self._truthy(operand, ty)
            return self.builder.not_(b, name=self._fresh("not"))
        raise NotImplementedError(f"Layer 1 unary {expr.op!r} not supported")

    # -- Compare -------------------------------------------------------

    def _emit_compare(self, expr: Compare) -> ir.Value:
        # Identity against None: pointer compare against @py_None.
        if expr.op in ("is", "is not"):
            return self._emit_identity_compare(expr)
        if expr.op in ("in", "not in"):
            return self._emit_membership(expr)

        # Class-based comparison dunder fast path.
        cmp_dunder = {
            "==": "__eq__",
            "!=": "__ne__",
            "<":  "__lt__",
            "<=": "__le__",
            ">":  "__gt__",
            ">=": "__ge__",
        }.get(expr.op)
        if cmp_dunder is not None:
            dunder = self._try_dispatch_dunder_unary(
                expr.lhs, cmp_dunder, (expr.rhs,)
            )
            if dunder is not None:
                if dunder.type is _I1:
                    return dunder
                if isinstance(dunder.type, ir.IntType) and dunder.type.width > 1:
                    return self.builder.icmp_signed(
                        "!=", dunder, ir.Constant(dunder.type, 0),
                        name=self._fresh("dunder.i1"),
                    )
                if isinstance(dunder.type, ir.PointerType):
                    # Returned PyObject*: run py_obj_truthy to get i1.
                    as_i32 = self.builder.call(
                        self.runtime["py_obj_truthy"], [dunder],
                        name=self._fresh("dunder.truthy"),
                    )
                    return self.builder.trunc(
                        as_i32, _I1, name=self._fresh("dunder.truthy.i1"),
                    )
                return dunder

        lhs_ty = expr.lhs.ty
        rhs_ty = expr.rhs.ty

        # String equality → runtime py_str_eq; other relational ops on
        # strings fall to the generic py_obj_eq path.
        if isinstance(lhs_ty, StrType) and isinstance(rhs_ty, StrType):
            lhs = self._emit_expr(expr.lhs)
            rhs = self._emit_expr(expr.rhs)
            if expr.op in ("==", "!="):
                eq = self.builder.call(
                    self.runtime["py_str_eq"], [lhs, rhs],
                    name=self._fresh("str.eq"),
                )
                eq_i1 = self.builder.icmp_signed(
                    "!=", eq, ir.Constant(_I32, 0), name=self._fresh("str.eq.i1")
                )
                if expr.op == "!=":
                    return self.builder.not_(eq_i1, name=self._fresh("str.ne"))
                return eq_i1
            # <, <=, >, >= — fall back to a lexicographic comparison via
            # the runtime's generic object compare isn't exposed; emit
            # py_str_eq for ==, else NotImplementedError until L3.
            raise NotImplementedError(
                f"Layer 2 does not handle str compare op {expr.op!r}"
            )

        # Object-vs-object equality (for two boxed operands): delegate.
        if self._is_object(lhs_ty) and self._is_object(rhs_ty):
            if expr.op in ("==", "!="):
                lhs = self._emit_expr(expr.lhs)
                rhs = self._emit_expr(expr.rhs)
                eq = self.builder.call(
                    self.runtime["py_obj_eq"], [lhs, rhs],
                    name=self._fresh("obj.eq"),
                )
                eq_i1 = self.builder.icmp_signed(
                    "!=", eq, ir.Constant(_I32, 0),
                    name=self._fresh("obj.eq.i1"),
                )
                if expr.op == "!=":
                    return self.builder.not_(eq_i1, name=self._fresh("obj.ne"))
                return eq_i1
            # For <, <=, >, >= on DynType values, try to unbox both
            # operands to ``i64`` via the DynType coercion path. Works
            # for the common case where both sides are known-int
            # attribute reads (``self.v < other.v``).
            if (
                isinstance(lhs_ty, DynType)
                and isinstance(rhs_ty, DynType)
                and expr.op in ("<", "<=", ">", ">=")
            ):
                lhs = self._emit_expr(expr.lhs)
                rhs = self._emit_expr(expr.rhs)
                lv = self._to_int64(lhs, lhs_ty)
                rv = self._to_int64(rhs, rhs_ty)
                return self.builder.icmp_signed(
                    expr.op, lv, rv, name=self._fresh("dyn.icmp"),
                )
            raise NotImplementedError(
                f"Layer 2 does not handle object compare op {expr.op!r}"
            )

        lhs = self._emit_expr(expr.lhs)
        rhs = self._emit_expr(expr.rhs)
        if isinstance(lhs_ty, FloatType) or isinstance(rhs_ty, FloatType):
            lf = self._to_double(lhs, lhs_ty)
            rf = self._to_double(rhs, rhs_ty)
            return self.builder.fcmp_ordered(expr.op, lf, rf,
                                               name=self._fresh("fcmp"))
        lv = self._to_int64(lhs, lhs_ty)
        rv = self._to_int64(rhs, rhs_ty)
        return self.builder.icmp_signed(expr.op, lv, rv,
                                          name=self._fresh("icmp"))

    def _emit_identity_compare(self, expr: Compare) -> ir.Value:
        """``is`` / ``is not`` — pointer compare, typically against None.

        Both operands are marshalled to PyObject* and compared as
        pointers. Interning of small ints / bools is handled by the
        runtime (``py_int_from_i64`` returns the canonical global for
        small ints), so ``is`` behaves consistently with CPython on
        those.

        Fast path: if one operand is a NoneLit and the other is a native
        scalar (int/float/bool), the answer is a compile-time constant
        (False for ``is``, True for ``is not``).
        """
        # Constant-fold ``<native> is None`` and ``<native> is not None``.
        native_lhs = self._is_native_scalar_type(expr.lhs.ty)
        native_rhs = self._is_native_scalar_type(expr.rhs.ty)
        none_lhs = isinstance(expr.lhs, NoneLit) or isinstance(expr.lhs.ty, NoneType)
        none_rhs = isinstance(expr.rhs, NoneLit) or isinstance(expr.rhs.ty, NoneType)
        if (native_lhs and none_rhs) or (native_rhs and none_lhs):
            # The native value can never be literally the py_None pointer.
            return ir.Constant(_I1, 1 if expr.op == "is not" else 0)

        lhs = self._emit_expr(expr.lhs)
        rhs = self._emit_expr(expr.rhs)
        lhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, lhs, expr.lhs.ty
        )
        rhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, rhs, expr.rhs.ty
        )
        # Compare pointers as integers so the IR is independent of the
        # llvmlite version's pointer-compare support.
        lhs_i = self.builder.ptrtoint(lhs_obj, _I64, name=self._fresh("is.l"))
        rhs_i = self.builder.ptrtoint(rhs_obj, _I64, name=self._fresh("is.r"))
        eq = self.builder.icmp_signed(
            "==", lhs_i, rhs_i, name=self._fresh("is")
        )
        if expr.op == "is not":
            return self.builder.not_(eq, name=self._fresh("is_not"))
        return eq

    def _emit_membership(self, expr: Compare) -> ir.Value:
        """``in`` / ``not in`` over str / list / dict / set / tuple."""
        container_ty = expr.rhs.ty
        lhs = self._emit_expr(expr.lhs)
        rhs = self._emit_expr(expr.rhs)
        lhs_ty = expr.lhs.ty

        if isinstance(container_ty, StrType):
            # Needle is expected to be a pcc str (single char or
            # substring). When the lhs type is DynType (e.g. a
            # comprehension loop variable bound by ``for ch in s``
            # where the comp-scope inference didn't propagate the
            # element type), we still have a ``PyObject*`` — py_str_*
            # helpers tolerate foreign types by length/bytes compare.
            needle = lhs
            if not isinstance(lhs.type, ir.PointerType):
                needle = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, lhs, lhs_ty
                )
            res_i32 = self.builder.call(
                self.runtime["py_str_contains"], [rhs, needle],
                name=self._fresh("str.in"),
            )
        elif isinstance(container_ty, ListType):
            needle = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, lhs, lhs_ty
            )
            res_i32 = self.builder.call(
                self.runtime["py_list_contains"], [rhs, needle],
                name=self._fresh("list.in"),
            )
        elif isinstance(container_ty, DictType):
            key = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, lhs, lhs_ty
            )
            res_i32 = self.builder.call(
                self.runtime["py_dict_contains"], [rhs, key],
                name=self._fresh("dict.in"),
            )
        elif isinstance(container_ty, TupleType):
            # No dedicated py_tuple_contains in the ABI; fall through to
            # the generic iterator-less object-eq loop by unrolling
            # against each static element if the tuple literal has a
            # known shape. For the general (non-literal) case we defer
            # to L3 via a NotImplementedError.
            if isinstance(expr.rhs, TupleExpr):
                return self._emit_membership_tuple_literal(
                    lhs, lhs_ty, expr.rhs, negate=(expr.op == "not in")
                )
            raise NotImplementedError(
                "Layer 2 tuple-in on non-literal tuples needs L3"
            )
        elif isinstance(container_ty, DynType):
            # DynType container — route through the runtime
            # ``py_obj_contains`` dispatcher. Accepts any pcc-native
            # container type at runtime; no libpython needed.
            rhs = self._emit_expr(expr.rhs)
            key = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, lhs, lhs_ty,
            )
            res_i32 = self.builder.call(
                self.runtime["py_obj_contains"], [rhs, key],
                name=self._fresh("obj.in"),
            )
            result = self.builder.icmp_signed(
                "!=", res_i32, ir.Constant(_I32, 0),
                name=self._fresh("obj.in.i1"),
            )
            if expr.op == "not in":
                result = self.builder.not_(
                    result, name=self._fresh("obj.notin"),
                )
            return result
        else:
            # Other object fallback — not yet wired.
            raise NotImplementedError(
                f"Layer 2 'in' on type {type(container_ty).__name__} "
                "needs L3"
            )

        res = self.builder.icmp_signed(
            "!=", res_i32, ir.Constant(_I32, 0), name=self._fresh("in.i1")
        )
        if expr.op == "not in":
            return self.builder.not_(res, name=self._fresh("not_in"))
        return res

    def _emit_membership_tuple_literal(
        self, lhs: ir.Value, lhs_ty: Type, rhs: TupleExpr, negate: bool
    ) -> ir.Value:
        """Unroll ``x in (a, b, c)`` as ``x==a or x==b or x==c``."""
        lhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, lhs, lhs_ty
        )
        acc: Optional[ir.Value] = None
        for el in rhs.elems:
            v = self._emit_expr(el)
            v_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, el.ty
            )
            eq_i32 = self.builder.call(
                self.runtime["py_obj_eq"], [lhs_obj, v_obj],
                name=self._fresh("tup.eq"),
            )
            eq_i1 = self.builder.icmp_signed(
                "!=", eq_i32, ir.Constant(_I32, 0),
                name=self._fresh("tup.eq.i1"),
            )
            if acc is None:
                acc = eq_i1
            else:
                acc = self.builder.or_(acc, eq_i1, name=self._fresh("tup.or"))
        if acc is None:
            # Empty tuple: ``x in ()`` is always False.
            acc = ir.Constant(_I1, 0)
        if negate:
            return self.builder.not_(acc, name=self._fresh("tup.not_in"))
        return acc

    # -- BoolExpr ------------------------------------------------------

    def _emit_boolexpr(self, expr: BoolExpr) -> ir.Value:
        # Short-circuit via branch. Python's ``and``/``or`` return one
        # of the operands, but under L1 the result type is always
        # ``BoolType`` (the type_infer pass enforces this for typed
        # code); we therefore materialise as i1.
        fn = self.current_function

        lhs = self._emit_expr(expr.left)
        lhs_b = self._truthy(lhs, expr.left.ty)

        rhs_bb = fn.append_basic_block(name=self._fresh("bool.rhs"))
        end_bb = fn.append_basic_block(name=self._fresh("bool.end"))
        entry_bb = self.builder.block

        if expr.op == "and":
            # if lhs then compute rhs else short-circuit false.
            self.builder.cbranch(lhs_b, rhs_bb, end_bb)
        elif expr.op == "or":
            # if lhs then short-circuit true else compute rhs.
            self.builder.cbranch(lhs_b, end_bb, rhs_bb)
        else:
            raise NotImplementedError(
                f"Layer 1 bool op {expr.op!r} not supported"
            )

        self.builder.position_at_end(rhs_bb)
        rhs = self._emit_expr(expr.right)
        rhs_b = self._truthy(rhs, expr.right.ty)
        rhs_exit = self.builder.block
        self.builder.branch(end_bb)

        self.builder.position_at_end(end_bb)
        phi = self.builder.phi(_I1, name=self._fresh(expr.op))
        if expr.op == "and":
            phi.add_incoming(ir.Constant(_I1, 0), entry_bb)
            phi.add_incoming(rhs_b, rhs_exit)
        else:  # "or"
            phi.add_incoming(ir.Constant(_I1, 1), entry_bb)
            phi.add_incoming(rhs_b, rhs_exit)
        return phi

    # -- Call ----------------------------------------------------------

    def _emit_call(self, expr: Call) -> ir.Value:
        if isinstance(expr.func, Attr):
            return self._emit_method_call(expr)
        if not isinstance(expr.func, Name):
            raise NotImplementedError(
                "Layer 1 only handles direct-name calls (no method/attr)"
            )
        name = expr.func.ident
        # Comprehension sentinels emitted by the parser. Lowered to an
        # explicit loop that appends into a runtime list/dict/set.
        if name in ("__listcomp__", "_list_comp", "_gen_comp", "__genexpr__"):
            # Generator expressions eagerly materialise to a list —
            # pcc doesn't support lazy generators yet; the common use
            # sites (``sum(x for x in xs)``, ``"".join(s for …)``)
            # iterate the result once so a list works identically.
            return self._emit_comprehension(expr, "list")
        if name in ("__setcomp__", "_set_comp"):
            return self._emit_comprehension(expr, "set")
        if name in ("__dictcomp__", "_dict_comp"):
            return self._emit_comprehension(expr, "dict")
        # print() has a bespoke kwarg parser (sep=, end=) handled inline.
        if name == "print":
            self._emit_print_call(expr)
            return ir.Constant(_I1, 0)
        # Builtins below don't support kwargs — reject early.
        if expr.kwargs and name in ("range", "len", "str", "isinstance"):
            raise NotImplementedError(
                f"Layer 1 builtin {name}() does not accept keyword args"
            )
        if name == "range":
            raise NotImplementedError(
                "Layer 1 only supports range() inside 'for'"
            )
        if name == "_walrus":
            return self._emit_walrus(expr)
        if name == "len":
            return self._emit_len_call(expr)
        if name == "str":
            return self._emit_str_builtin(expr)
        if name == "isinstance":
            return self._emit_isinstance_call(expr)
        # ``field(default_factory=F)`` from ``dataclasses.field``
        # appears as the RHS of a dataclass body assign. At codegen
        # time we collapse it to a call of ``F()``. The other
        # ``field`` kwargs (init, repr, ...) are informational — pcc
        # doesn't vary emission based on them.
        if name == "field" and not expr.args:
            for k, v in expr.kwargs:
                if k == "default_factory":
                    if isinstance(v, Name):
                        # Known builtin factories.
                        if v.ident == "list":
                            return self.builder.call(
                                self.runtime["py_list_new"],
                                [ir.Constant(_I64, 0)],
                                name=self._fresh("field.list"),
                            )
                        if v.ident == "dict":
                            return self.builder.call(
                                self.runtime["py_dict_new"], [],
                                name=self._fresh("field.dict"),
                            )
                        if v.ident == "set":
                            return self.builder.call(
                                self.runtime["py_set_new"], [],
                                name=self._fresh("field.set"),
                            )
                        if v.ident == "tuple":
                            return self.builder.call(
                                self.runtime["py_tuple_new"],
                                [ir.Constant(_I64, 0)],
                                name=self._fresh("field.tuple"),
                            )
                    # Unknown factory — attempt to call it as a
                    # user function. Falls back to regular dispatch.
                    return self._emit_call(Call(
                        span=expr.span, ty=expr.ty,
                        func=v, args=(), kwargs=(),
                    ))
            # No default_factory → default value (None).
            return ir.Constant(_CSTR, None)
        # ``cls(args)`` inside a @classmethod body — treat as a
        # normal instantiation of the owning class. pcc doesn't
        # support calling arbitrary ``cls`` pointers yet, so we
        # resolve to the enclosing class statically.
        if (
            name == "cls"
            and "cls" in self.env
            and getattr(self, "current_class", None) is not None
        ):
            args = expr.args
            if expr.kwargs:
                init_fd = self.class_lowering._find_method_def(
                    self.current_class.name, "__init__",
                )
                if init_fd is not None:
                    args = tuple(self._resolve_call_kwargs(
                        expr.args, expr.kwargs, init_fd.args,
                        skip_self=True,
                    ))
                else:
                    args = expr.args  # fallthrough to original
            return self.class_lowering.emit_instantiate(
                self.current_class.name, args, self,
            )
        if name in ("min", "max") and len(expr.args) == 2:
            return self._emit_min_max_builtin(expr, name)
        if name in ("min", "max") and len(expr.args) == 1:
            result = self._maybe_emit_min_max_iter(expr, name)
            if result is not None:
                return result
        if name == "abs" and len(expr.args) == 1:
            return self._emit_abs_builtin(expr)
        if name in ("any", "all") and len(expr.args) == 1:
            result = self._maybe_emit_any_all_literal(expr, name)
            if result is not None:
                return result
        if name == "sum" and 1 <= len(expr.args) <= 2:
            result = self._maybe_emit_sum_literal(expr)
            if result is not None:
                return result
        if name == "int" and 1 <= len(expr.args) <= 2:
            result = self._maybe_emit_int_builtin(expr)
            if result is not None:
                return result
        if name == "bool" and len(expr.args) == 1:
            # ``bool(x)`` — truthiness check; reuse ``_truthy`` on the
            # operand's type. Zero args (``bool()`` → ``False``)
            # handled trivially.
            v = self._emit_expr(expr.args[0])
            return self._truthy(v, expr.args[0].ty)
        if name == "bool" and not expr.args:
            return ir.Constant(_I1, 0)
        if name == "float" and len(expr.args) == 1:
            v = self._emit_expr(expr.args[0])
            ty = expr.args[0].ty
            if isinstance(ty, FloatType):
                return v
            if isinstance(ty, (IntType, BoolType)):
                if v.type is _I1:
                    v = self.builder.zext(
                        v, _I64, name=self._fresh("float.from_bool"),
                    )
                return self.builder.sitofp(
                    v, _DOUBLE, name=self._fresh("float.from_int"),
                )
        if name in ("set", "frozenset") and len(expr.args) <= 1:
            # pcc has no distinct ``frozenset`` runtime type; treat
            # as ``set`` — immutable vs mutable doesn't matter for
            # the compile-free pcc path since we don't mutate the
            # constant containers declared as module globals.
            result = self._maybe_emit_set_builtin(expr)
            if result is not None:
                return result
        if name == "list" and len(expr.args) <= 1:
            result = self._maybe_emit_list_builtin(expr)
            if result is not None:
                return result
        if name == "tuple" and len(expr.args) <= 1:
            result = self._maybe_emit_tuple_builtin(expr)
            if result is not None:
                return result
        if name == "dict" and len(expr.args) <= 1:
            result = self._maybe_emit_dict_builtin(expr)
            if result is not None:
                return result
        if name == "sorted" and len(expr.args) == 1 and not expr.kwargs:
            src_val = self._emit_expr(expr.args[0])
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, expr.args[0].ty,
            )
            return self.builder.call(
                self.runtime["py_obj_sorted"], [src_obj],
                name=self._fresh("sorted"),
            )
        if name == "repr" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_obj_repr"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("repr"),
            )
        if name == "hash" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_obj_hash"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("hash"),
            )
        if name == "id" and len(expr.args) == 1:
            v = self._emit_as_object(expr.args[0])
            return self.builder.ptrtoint(
                v, _I64, name=self._fresh("id"),
            )
        if name == "hasattr" and len(expr.args) == 2:
            # ``hasattr(x, "name")`` — pcc doesn't distinguish "missing"
            # from "present but None" without full dunder support, but
            # for the common usage (gate on attribute existence) the
            # presence-check via py_obj_getattr returning non-NULL
            # works on pcc-native classes.
            obj = self._emit_as_object(expr.args[0])
            nm = expr.args[1]
            if isinstance(nm, StrLit):
                name_ptr = self._attr_name_ptr(nm.value)
            else:
                nv = self._emit_expr(nm)
                n_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    nv, nm.ty,
                )
                name_ptr = self.builder.call(
                    self.runtime["py_str_utf8"], [n_obj],
                    name=self._fresh("hasattr.name"),
                )
            got = self.builder.call(
                self.runtime["py_obj_getattr"], [obj, name_ptr],
                name=self._fresh("hasattr.got"),
            )
            null = ir.Constant(_CSTR, None)
            return self.builder.icmp_signed(
                "!=", got, null, name=self._fresh("hasattr.i1"),
            )
        if name == "ord" and len(expr.args) == 1:
            # ``ord(s)`` where s is a one-char str. Read first byte.
            s_val = self._emit_as_object(expr.args[0])
            cstr = self.builder.call(
                self.runtime["py_str_utf8"], [s_val],
                name=self._fresh("ord.cstr"),
            )
            gep = self.builder.gep(
                cstr, [ir.Constant(_I64, 0)], inbounds=True,
                name=self._fresh("ord.p"),
            )
            b = self.builder.load(
                gep, name=self._fresh("ord.b"),
            )
            # i8 → i64 zext.
            return self.builder.zext(
                b, _I64, name=self._fresh("ord"),
            )
        if name == "getattr" and 2 <= len(expr.args) <= 3:
            return self._emit_getattr_builtin(expr)
        if name == "type" and len(expr.args) == 1:
            return self._emit_type_builtin(expr)

        # Extern-C direct call (P6C.1): name bound to extern("symbol"...).
        extern_decls = getattr(self, "_extern_decls", {})
        if name in extern_decls:
            if expr.kwargs:
                raise NotImplementedError(
                    "Layer 1 extern-C calls do not accept keyword args"
                )
            return self._emit_extern_call(extern_decls[name], expr.args)

        # User class instantiation: ``MyClass(args)``.
        if (
            hasattr(self, "class_lowering")
            and name in self.class_lowering.classes
        ):
            resolved_args = expr.args
            if expr.kwargs:
                init_fd = self.class_lowering._find_method_def(
                    name, "__init__"
                )
                if init_fd is None:
                    raise NotImplementedError(
                        f"class {name!r} with kwargs needs __init__ "
                        "to resolve parameter names"
                    )
                resolved_args = tuple(self._resolve_call_kwargs(
                    expr.args, expr.kwargs, init_fd.args, skip_self=True,
                ))
            return self.class_lowering.emit_instantiate(
                name, resolved_args, self,
            )

        # Callable instance via ``__call__`` — ``double(5)`` where
        # ``double`` was assigned a class instance that defines
        # ``__call__``.
        if hasattr(self, "env_class_hint"):
            hint = self.env_class_hint.get(name)
            if hint is not None:
                info = self._resolve_method_mro(hint, "__call__")
                if info is not None:
                    obj_val = self._emit_name(Name(
                        span=expr.span, ty=DynType(name="dyn"), ident=name,
                    ))
                    method_fn = info.methods["__call__"]
                    return self._emit_direct_method_call(
                        method_fn, obj_val, info, "__call__", expr.args,
                        kwargs=expr.kwargs,
                    )

        fn = self.functions.get(name)
        if fn is None:
            # CPython-backed callable (e.g. a ``from .sibling import
            # foo`` where ``foo`` isn't a native-sibling FuncDef)
            # dispatches via PyObject_Call. Pulls libpython but is
            # correct for the import route.
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(name)
            if cpy_gv is not None:
                fn_val = self.builder.load(
                    cpy_gv, name=self._fresh(f"cpy.fn.{name}"),
                )
                if expr.kwargs:
                    return self._finish_cpy_call_kw(
                        fn_val, name, expr.args, expr.kwargs,
                    )
                return self._emit_cpy_func_call(fn_val, name, expr.args)
            raise NotImplementedError(
                f"Layer 1 unknown function {name!r}; builtins other than "
                "print/range/len/str need L2/L3"
            )
        ast_func_def = self._find_user_funcdef(name)
        resolved_args = self._resolve_call_kwargs(
            expr.args, expr.kwargs, ast_func_def.args,
        )
        args_ir: list[ir.Value] = []
        for ast_arg, arg_def in zip(resolved_args, ast_func_def.args):
            v = self._emit_expr(ast_arg)
            v = self._coerce(v, ast_arg.ty, arg_def.annotation)
            args_ir.append(v)
        call_name = "" if isinstance(fn.function_type.return_type, ir.VoidType) \
                       else self._fresh(f"{name}_ret")
        return self._call_user(fn, args_ir, call_name)

    def _call_user(
        self,
        fn: ir.Function,
        args_ir: list[ir.Value],
        call_name: str,
    ) -> ir.Value:
        """Call a user function, using ``invoke`` when we're inside a
        ``try`` block so a raised exception unwinds to our landingpad
        instead of propagating past this frame."""
        lpad = getattr(self, "_try_lpad", None)
        if lpad is None:
            return self.builder.call(fn, args_ir, name=call_name)
        parent_fn = self.current_function
        cont = parent_fn.append_basic_block(
            name=self._fresh("call.cont")
        )
        result = self.builder.invoke(
            fn, args_ir, cont, lpad, name=call_name,
        )
        self.builder.position_at_end(cont)
        return result

    def _emit_method_call(self, expr: Call) -> ir.Value:
        """Lower ``obj.method(args)`` using the class method registry.

        Fast path: if ``obj`` is a Name bound in the local env to a
        ``DynType`` instance of a known class in the current module,
        and ``method`` is a direct member of that class (no MRO
        walking), dispatch to the declared pcc method function. The
        generic ``py_obj_call_method`` path is used otherwise.
        """
        attr = expr.func
        assert isinstance(attr, Attr)

        # Typed-container method dispatch on pcc-native containers —
        # stays on pcc runtime so the produced binary has no libpython
        # dep. Only a curated method set is recognised; anything else
        # surfaces as NotImplementedError rather than falling through
        # to the generic CPython helper.
        obj_ty0 = attr.obj.ty
        if isinstance(obj_ty0, ListType):
            native = self._maybe_emit_list_method(expr, obj_ty0)
            if native is not None:
                return native
        if isinstance(obj_ty0, DictType):
            native = self._maybe_emit_dict_method(expr, obj_ty0)
            if native is not None:
                return native
        if isinstance(obj_ty0, StrType):
            native = self._maybe_emit_str_method(expr)
            if native is not None:
                return native

        # Case -1: ``<CPython value>.method(args)``.
        #
        # Chained access (``os.path.join``) lowers the inner attr chain
        # through ``_emit_attr``, which already routes through
        # ``py_cpy_getattr`` whenever the root is an imported module or
        # a CPython-flagged local. If the resulting SSA value lands in
        # ``_cpy_values``, dispatch the method call through libpython.
        if isinstance(attr.obj, Name):
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(attr.obj.ident)
            if cpy_gv is not None:
                return self._emit_cpy_method_call_src(
                    self.builder.load(cpy_gv, name=self._fresh("cpy.mod")),
                    attr.name, expr.args, kwargs=expr.kwargs,
                )
            if getattr(self, "_cpy_env_flags", {}).get(attr.obj.ident, False):
                return self._emit_cpy_method_call_src(
                    self._emit_expr(attr.obj), attr.name, expr.args,
                    kwargs=expr.kwargs,
                )
        if isinstance(attr.obj, Attr):
            # Evaluate the chain eagerly; if the result was tagged as a
            # CPython value (e.g. ``os.path``), dispatch there.
            chain_val = self._emit_expr(attr.obj)
            if chain_val in getattr(self, "_cpy_values", ()):
                return self._emit_cpy_method_call_src(
                    chain_val, attr.name, expr.args, kwargs=expr.kwargs,
                )

        # Case 0: ``super().method(args)`` inside a method body.
        # Resolve the method by walking the current class's declared
        # bases. The ``self`` argument is forwarded unchanged.
        current_class = getattr(self, "current_class", None)
        if (
            current_class is not None
            and isinstance(attr.obj, Call)
            and isinstance(attr.obj.func, Name)
            and attr.obj.func.ident == "super"
            and not attr.obj.args
        ):
            parent_info = self._resolve_super_method(
                current_class, attr.name
            )
            if parent_info is not None:
                self_val = self.builder.load(
                    self.env["self"][0], name=self._fresh("self")
                )
                method_fn = parent_info.methods[attr.name]
                return self._emit_direct_method_call(
                    method_fn, self_val,
                    parent_info, attr.name, expr.args,
                    kwargs=expr.kwargs,
                )
            # Parent is a foreign base (e.g. ``Exception``) not tracked
            # by pcc's ClassInfo registry. For the well-known dunders
            # (``__init__`` / ``__new__``) we fall through quietly —
            # pcc-emitted classes already have their ctor state
            # populated by ``_pcc_py_module_init_*``, and calling an
            # unknown foreign super is typically only used for its
            # side effects which have no equivalent on the pcc side.
            if attr.name in ("__init__", "__new__"):
                return ir.Constant(_CSTR, None)

        # Case 1: ``self.method(...)`` inside a method body of the
        # currently-lowered class. Try the method on the class itself,
        # then walk the declared bases.
        if (
            current_class is not None
            and isinstance(attr.obj, Name)
            and attr.obj.ident == "self"
        ):
            method_info = self._resolve_method_mro(
                current_class.name, attr.name
            )
            if method_info is not None:
                self_val = self.builder.load(
                    self.env["self"][0], name=self._fresh("self")
                )
                method_fn = method_info.methods[attr.name]
                return self._emit_direct_method_call(
                    method_fn, self_val,
                    method_info, attr.name, expr.args,
                    kwargs=expr.kwargs,
                )

        # Case 2: ``ClassName.method(...)`` — direct static/classmethod
        # dispatch on a bare class reference (no instance).
        if (
            isinstance(attr.obj, Name)
            and attr.obj.ident in self.class_lowering.classes
        ):
            info = self._resolve_method_mro(attr.obj.ident, attr.name)
            if info is not None:
                kind = info.method_kinds.get(attr.name, "instance")
                if kind == "static":
                    method_fn = info.methods[attr.name]
                    return self._emit_static_method_call(
                        method_fn, info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )
                if kind == "classmethod":
                    cls_ptr = self.builder.load(
                        info.global_var, name=self._fresh(".cls.recv")
                    )
                    method_fn = info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn, cls_ptr, info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )
                # instance method referenced via ``Class.method(self, ...)``
                # — unsupported in the Phase-3 subset.

        # Case 3: ``other_obj.method(...)`` — first try the class hint
        # recorded at assignment time, walking up the MRO of that
        # class for the first definition of the method. Fall back to
        # the first class in the module that declares the method so
        # single-class programs keep working when the hint is missing.
        if isinstance(attr.obj, Name):
            hint = self.env_class_hint.get(attr.obj.ident)
            if hint is not None:
                info = self._resolve_method_mro(hint, attr.name)
                if info is not None:
                    kind = info.method_kinds.get(attr.name, "instance")
                    if kind == "static":
                        method_fn = info.methods[attr.name]
                        return self._emit_static_method_call(
                            method_fn, info, attr.name, expr.args,
                            kwargs=expr.kwargs,
                        )
                    obj_val = self._emit_expr(attr.obj)
                    if kind == "classmethod":
                        obj_val = self.builder.load(
                            info.global_var, name=self._fresh(".cls.recv")
                        )
                    method_fn = info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn, obj_val,
                        info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )
            # Fallback: any class declaring the method.
            for info in self.class_lowering.classes.values():
                if attr.name in info.methods:
                    kind = info.method_kinds.get(attr.name, "instance")
                    if kind == "static":
                        method_fn = info.methods[attr.name]
                        return self._emit_static_method_call(
                            method_fn, info, attr.name, expr.args,
                            kwargs=expr.kwargs,
                        )
                    obj_val = self._emit_expr(attr.obj)
                    if kind == "classmethod":
                        obj_val = self.builder.load(
                            info.global_var, name=self._fresh(".cls.recv")
                        )
                    method_fn = info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn, obj_val,
                        info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )

        # Last-resort CPython fallback: when the receiver is a DynType
        # value (typical for foreign / imported-module classes whose
        # annotations such as ``llvm.ModuleRef`` resolve to DynType at
        # type-inference time), dispatch the method via
        # ``PyObject_CallMethod``. This unlocks ``module.verify()``
        # and similar idioms without requiring a CPython-class registry
        # on the pcc side.
        #
        # Typed containers (list / dict / tuple / str) deliberately do
        # *not* fall through here: pcc's pure-self-host story requires
        # that typed-collection methods stay on pcc-native runtime paths
        # so the produced binary has no libpython dependency. Missing
        # methods there surface as NotImplementedError so we can add a
        # dedicated fast path rather than silently pulling libpython in.
        obj_ty = attr.obj.ty
        if isinstance(obj_ty, DynType):
            # DynType receiver: when the method is a known pcc-native
            # str helper, dispatch through the runtime (assumes the
            # value really is a str at runtime — matches CPython
            # behaviour which would raise AttributeError on type
            # mismatch; we emit a probable crash). Keeps the binary
            # libpython-free for the common ``DynType str result``
            # idiom (function return + splitlines / rstrip / …).
            native = self._maybe_emit_str_method_via_dyn(expr)
            if native is not None:
                return native
            obj_val = self._emit_expr(attr.obj)
            return self._emit_cpy_method_call_src(
                obj_val, attr.name, expr.args, kwargs=expr.kwargs,
            )

        raise NotImplementedError(
            f"Layer 1 method call {attr.name!r}: no matching class "
            "method found in module (dynamic dispatch via dunder path "
            "is deferred)"
        )

    def _try_dispatch_dunder_unary(
        self,
        host_expr: "Expr",
        dunder_name: str,
        arg_exprs: tuple["Expr", ...],
    ) -> Optional[ir.Value]:
        """If ``host_expr`` is a Name bound to a hinted class that
        defines ``dunder_name`` (via MRO), emit the direct method call
        with ``arg_exprs`` and return the result. Otherwise return None.
        """
        if isinstance(host_expr, Subscript):
            host = host_expr.obj
        else:
            host = host_expr
        if not isinstance(host, Name):
            return None
        hint = self.env_class_hint.get(host.ident)
        if hint is None:
            return None
        info = self._resolve_method_mro(hint, dunder_name)
        if info is None:
            return None
        obj_val = self._emit_expr(host)
        method_fn = info.methods[dunder_name]
        return self._emit_direct_method_call(
            method_fn, obj_val, info, dunder_name, arg_exprs,
        )

    def _resolve_super_method(self, info, method_name: str):
        """Walk the bases of ``info`` and return the first one that
        defines ``method_name``. Models a single-inheritance ``super()``
        call — the multi-base case needs full C3 linearisation which
        remains TODO in :class:`ClassLowering`.
        """
        for base_expr in info.bases_ast:
            if not isinstance(base_expr, Name) or base_expr.ident == "object":
                continue
            found = self._resolve_method_mro(base_expr.ident, method_name)
            if found is not None:
                return found
        return None

    def _resolve_property_setter_mro(self, class_name: str, prop_name: str):
        """Walk the MRO of ``class_name`` for a ``@<prop>.setter``."""
        visited: set[str] = set()
        queue = [class_name]
        while queue:
            cname = queue.pop(0)
            if cname in visited:
                continue
            visited.add(cname)
            info = self.class_lowering.classes.get(cname)
            if info is None:
                continue
            if prop_name in info.property_setters:
                return info
            for base_expr in info.bases_ast:
                if isinstance(base_expr, Name) and base_expr.ident != "object":
                    queue.append(base_expr.ident)
        return None

    def _resolve_property_mro(self, class_name: str, prop_name: str):
        """Walk the MRO of ``class_name`` for a ``@property`` ``prop_name``."""
        visited: set[str] = set()
        queue = [class_name]
        while queue:
            cname = queue.pop(0)
            if cname in visited:
                continue
            visited.add(cname)
            info = self.class_lowering.classes.get(cname)
            if info is None:
                continue
            if prop_name in info.properties:
                return info
            for base_expr in info.bases_ast:
                if isinstance(base_expr, Name) and base_expr.ident != "object":
                    queue.append(base_expr.ident)
        return None

    def _resolve_method_mro(self, class_name: str, method_name: str):
        """Walk the declared bases of ``class_name`` looking for the
        first class that defines ``method_name``. Uses the AST base
        list order (a shallow subset of full C3 MRO, sufficient for
        the single-inheritance + simple multi-inheritance cases in the
        current phase-3 corpus)."""
        visited: set[str] = set()
        queue = [class_name]
        while queue:
            cname = queue.pop(0)
            if cname in visited:
                continue
            visited.add(cname)
            info = self.class_lowering.classes.get(cname)
            if info is None:
                continue
            if method_name in info.methods:
                return info
            for base_expr in info.bases_ast:
                if isinstance(base_expr, Name) and base_expr.ident != "object":
                    queue.append(base_expr.ident)
        return None

    def _emit_cpy_attr(self, obj_val: ir.Value, name: str) -> ir.Value:
        """Lower ``cpy_obj.<name>`` through py_cpy_getattr, tagging the
        result as CPython-backed."""
        attr_ptr = self._ptr_to_cstr(
            self._cstr_global(name, f".cpy.attr.{name}")
        )
        val = self.builder.call(
            self.runtime["py_cpy_getattr"], [obj_val, attr_ptr],
            name=self._fresh(f"cpy.get.{name}"),
        )
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(val)
        return val

    def _emit_cpy_func_call(
        self, fn_val: ir.Value, name_hint: str,
        arg_exprs: tuple[Expr, ...],
    ) -> ir.Value:
        """Dispatch ``fn_val(args)`` via py_cpy_callN for a CPython
        callable already loaded into ``fn_val`` (e.g. from a
        ``from mod import fn`` binding). Args marshal via
        ``_marshal_to_cpython``. Shares the argv path with
        ``_emit_cpy_method_call_src``."""
        cpy_args: list[ir.Value] = []
        for arg in arg_exprs:
            v = self._emit_expr(arg)
            cpy_arg, _owned = self._marshal_to_cpython(v, arg.ty)
            cpy_args.append(cpy_arg)
        n = len(cpy_args)
        if n == 0:
            return self.builder.call(
                self.runtime["py_cpy_call_noargs"], [fn_val],
                name=self._fresh(f"cpy.call0.{name_hint}"),
            )
        if n == 1:
            return self.builder.call(
                self.runtime["py_cpy_call1"], [fn_val, cpy_args[0]],
                name=self._fresh(f"cpy.call1.{name_hint}"),
            )
        if n == 2:
            return self.builder.call(
                self.runtime["py_cpy_call2"], [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call2.{name_hint}"),
            )
        if n == 3:
            return self.builder.call(
                self.runtime["py_cpy_call3"], [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call3.{name_hint}"),
            )
        ptr_arr_ty = ir.ArrayType(_CSTR, n)
        argv = self._alloca_in_entry(
            ptr_arr_ty, name=f"cpy.argv.{name_hint}",
        )
        for i, ca in enumerate(cpy_args):
            gep = self.builder.gep(
                argv, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                inbounds=True, name=self._fresh(f"argv.{i}"),
            )
            self.builder.store(ca, gep)
        argv_p = self.builder.gep(
            argv, [ir.Constant(_I32, 0), ir.Constant(_I32, 0)],
            inbounds=True, name=self._fresh("argv.p"),
        )
        return self.builder.call(
            self.runtime["py_cpy_call_argv"],
            [fn_val, ir.Constant(_I64, n), argv_p],
            name=self._fresh(f"cpy.callN.{name_hint}"),
        )

    def _emit_cpy_method_call_src(
        self, mod_val: ir.Value, attr_name: str,
        arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        """Lower ``<CPython value>.method(args)`` through py_cpy_getattr
        + py_cpy_callN with scalar → CPython marshalling for typed args
        (int / float / str)."""
        attr_ptr = self._ptr_to_cstr(
            self._cstr_global(attr_name, f".cpy.attr.{attr_name}")
        )
        fn_val = self.builder.call(
            self.runtime["py_cpy_getattr"], [mod_val, attr_ptr],
            name=self._fresh(f"cpy.fn.{attr_name}"),
        )

        if kwargs:
            return self._finish_cpy_call_kw(
                fn_val, attr_name, arg_exprs, kwargs,
            )

        # Marshal each arg from its pcc native form to a CPython PyObject*.
        # ``owned`` parallel tracks whether we created the CPython ref
        # (and therefore must decref after the call).
        cpy_args: list[ir.Value] = []
        owned: list[bool] = []
        for arg in arg_exprs:
            v = self._emit_expr(arg)
            cpy_arg, is_owned = self._marshal_to_cpython(v, arg.ty)
            cpy_args.append(cpy_arg)
            owned.append(is_owned)

        n = len(cpy_args)
        if n == 0:
            result = self.builder.call(
                self.runtime["py_cpy_call_noargs"], [fn_val],
                name=self._fresh(f"cpy.call0.{attr_name}"),
            )
        elif n == 1:
            result = self.builder.call(
                self.runtime["py_cpy_call1"], [fn_val, cpy_args[0]],
                name=self._fresh(f"cpy.call1.{attr_name}"),
            )
        elif n == 2:
            result = self.builder.call(
                self.runtime["py_cpy_call2"], [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call2.{attr_name}"),
            )
        elif n == 3:
            result = self.builder.call(
                self.runtime["py_cpy_call3"], [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call3.{attr_name}"),
            )
        else:
            # Build an alloca argv[n] array and dispatch via
            # py_cpy_call_argv (PyObject_Call over a fresh tuple). The
            # runtime helper steals each argv[i] ref, so we do NOT
            # decref the owned args afterwards — only borrowed args
            # need a fresh ref (py_cpy_from_* produces one already).
            ptr_arr_ty = ir.ArrayType(_CSTR, n)
            argv = self._alloca_in_entry(
                ptr_arr_ty, name=f"cpy.argv.{attr_name}",
            )
            for i, (ca, is_owned) in enumerate(zip(cpy_args, owned)):
                if not is_owned:
                    # Caller-owned borrowed ref — give the tuple its
                    # own reference by wrapping the value. We don't
                    # have a direct Py_IncRef export, so marshal via
                    # an identity round-trip. For the narrow Phase-4
                    # subset, borrowed-arg calls with >3 args are
                    # rare enough to defer.
                    raise NotImplementedError(
                        "Layer 1: >3-arg CPython calls with borrowed "
                        "CPython args need a Py_IncRef export; "
                        "pcc hasn't added it yet."
                    )
                idx0 = ir.Constant(_I32, 0)
                idx = ir.Constant(_I32, i)
                slot = self.builder.gep(argv, [idx0, idx], inbounds=True,
                                          name=self._fresh(f"argv.{i}"))
                self.builder.store(ca, slot)
            # Decay the array pointer to a ``ptr`` for the varargs call.
            argv_ptr = self.builder.bitcast(
                argv, _CSTR, name=self._fresh("argv.ptr"),
            )
            result = self.builder.call(
                self.runtime["py_cpy_call_argv"],
                [fn_val, ir.Constant(_I64, n), argv_ptr],
                name=self._fresh(f"cpy.calln.{attr_name}"),
            )
            # py_cpy_call_argv stole each owned ref; skip the decref
            # loop below.
            self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(result)
            return result

        # Release only the CPython args we owned (native scalars we
        # boxed). Borrowed DynType/CPython values keep their
        # caller-owned ref.
        for ca, is_owned in zip(cpy_args, owned):
            if is_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [ca])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])

        # Mark the result as a CPython value so downstream print/str go
        # through the conversion path.
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _finish_cpy_call_kw(
        self, fn_val: ir.Value, name_hint: str,
        pos_exprs: tuple[Expr, ...],
        kwargs: tuple,
    ) -> ir.Value:
        """Dispatch a CPython callable with mixed positional + keyword
        arguments through ``py_cpy_call_kw``. Positional refs are stolen
        into the tuple; keyword refs are borrowed by PyDict_SetItem so
        we still decref our owned kw values after."""
        n_pos = len(pos_exprs)
        n_kw = len(kwargs)
        pos_vals: list[ir.Value] = []
        for arg in pos_exprs:
            v = self._emit_expr(arg)
            ca, _ = self._marshal_to_cpython(v, arg.ty)
            pos_vals.append(ca)
        kw_vals: list[ir.Value] = []
        kw_owned: list[bool] = []
        for _name, kv in kwargs:
            v = self._emit_expr(kv)
            ca, is_owned = self._marshal_to_cpython(v, kv.ty)
            kw_vals.append(ca)
            kw_owned.append(is_owned)

        # Build positional argv[n_pos]
        if n_pos == 0:
            pos_argv_ptr = ir.Constant(_CSTR, None)
        else:
            pos_arr_ty = ir.ArrayType(_CSTR, n_pos)
            pos_argv = self._alloca_in_entry(
                pos_arr_ty, name=f"cpy.pos.{name_hint}",
            )
            for i, ca in enumerate(pos_vals):
                gep = self.builder.gep(
                    pos_argv, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"pos.{i}"),
                )
                self.builder.store(ca, gep)
            pos_argv_ptr = self.builder.bitcast(
                pos_argv, _CSTR, name=self._fresh("pos.p"),
            )

        if n_kw == 0:
            names_ptr = ir.Constant(_CSTR, None)
            vals_ptr = ir.Constant(_CSTR, None)
        else:
            names_arr_ty = ir.ArrayType(_CSTR, n_kw)
            vals_arr_ty = ir.ArrayType(_CSTR, n_kw)
            names_arr = self._alloca_in_entry(
                names_arr_ty, name=f"cpy.kwn.{name_hint}",
            )
            vals_arr = self._alloca_in_entry(
                vals_arr_ty, name=f"cpy.kwv.{name_hint}",
            )
            for i, (kwn, _kv) in enumerate(kwargs):
                name_gv = self._cstr_global(
                    kwn, f".cpy.kwname.{name_hint}.{i}",
                )
                ngep = self.builder.gep(
                    names_arr, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"kwn.{i}"),
                )
                self.builder.store(self._ptr_to_cstr(name_gv), ngep)
                vgep = self.builder.gep(
                    vals_arr, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"kwv.{i}"),
                )
                self.builder.store(kw_vals[i], vgep)
            names_ptr = self.builder.bitcast(
                names_arr, _CSTR, name=self._fresh("kwn.p"),
            )
            vals_ptr = self.builder.bitcast(
                vals_arr, _CSTR, name=self._fresh("kwv.p"),
            )

        result = self.builder.call(
            self.runtime["py_cpy_call_kw"],
            [fn_val, ir.Constant(_I64, n_pos), pos_argv_ptr,
             ir.Constant(_I64, n_kw), names_ptr, vals_ptr],
            name=self._fresh(f"cpy.callkw.{name_hint}"),
        )
        # kw_vals are borrowed by PyDict_SetItemString (refcount
        # incremented by CPython); decref any we owned.
        for ca, is_owned in zip(kw_vals, kw_owned):
            if is_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [ca])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _marshal_to_cpython(self, v: ir.Value, ty: Type) -> tuple[ir.Value, bool]:
        """Convert a pcc-native value to a CPython PyObject*.

        Returns (cpython_value, owned) — ``owned`` is True when the
        caller must decref the result after use. Borrowed values
        (already-CPython DynType) return False.
        """
        if isinstance(ty, IntType) or isinstance(ty, BoolType):
            i64 = self._to_int64(v, ty)
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_i64"], [i64],
                    name=self._fresh("cpy.from_i64"),
                ),
                True,
            )
        if isinstance(ty, FloatType):
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_f64"], [v],
                    name=self._fresh("cpy.from_f64"),
                ),
                True,
            )
        if isinstance(ty, StrType):
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_pccstr"], [v],
                    name=self._fresh("cpy.from_pccstr"),
                ),
                True,
            )
        if isinstance(ty, NoneType):
            # None → CPython's Py_None (borrowed ref from the universal
            # converter on a pcc py_None). Use the same converter so we
            # don't have to teach codegen about the CPython Py_None sym.
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"], [v],
                    name=self._fresh("cpy.from_pcc_none"),
                ),
                True,
            )
        if isinstance(ty, (ListType, DictType, TupleType)):
            # pcc-native list/dict/tuple — rebuild as a CPython container.
            # The universal converter walks the pcc object via type tag
            # and recurses through nested containers.
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"], [v],
                    name=self._fresh(f"cpy.from_pcc_{type(ty).__name__.lower()[:-4]}"),
                ),
                True,
            )
        # DynType value that was itself produced by a CPython call —
        # pass through as a borrowed reference (caller still owns).
        if v in getattr(self, "_cpy_values", ()):
            return v, False
        # DynType with a native integer / float / pointer payload: pick
        # the marshaller that matches the IR type we actually hold.
        if isinstance(ty, DynType):
            if isinstance(v.type, ir.IntType):
                if v.type.width == 1:
                    # bool → CPython bool via int(0/1).
                    i64 = self.builder.zext(v, _I64, name=self._fresh("b2i64"))
                else:
                    i64 = v if v.type.width == 64 else self.builder.sext(
                        v, _I64, name=self._fresh("sext64"),
                    )
                return (
                    self.builder.call(
                        self.runtime["py_cpy_from_i64"], [i64],
                        name=self._fresh("cpy.from_i64.dyn"),
                    ),
                    True,
                )
            if isinstance(v.type, (ir.FloatType, ir.DoubleType)):
                return (
                    self.builder.call(
                        self.runtime["py_cpy_from_f64"], [v],
                        name=self._fresh("cpy.from_f64.dyn"),
                    ),
                    True,
                )
            if isinstance(v.type, ir.PointerType):
                # pcc PyObject* that isn't a CPython ref — best effort:
                # go through py_cpy_to_pcc_str reverse? No, it's a pcc
                # object. For now, marshal via pccstr path iff it's a
                # known str-shaped value, otherwise fail cleanly.
                return (
                    self.builder.call(
                        self.runtime["py_cpy_from_pccstr"], [v],
                        name=self._fresh("cpy.from_pcc.dyn"),
                    ),
                    True,
                )
        raise NotImplementedError(
            f"Layer 1 cannot marshal {type(ty).__name__} to CPython yet"
        )

    def _emit_static_method_call(
        self, method_fn: ir.Function, info,
        method_name: str, arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        """Lower ``ClassName.staticmethod(args)`` without any receiver
        and with argument coercion honouring declared annotations."""
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        if kwargs:
            if ast_fd is None:
                raise NotImplementedError(
                    f"staticmethod {info.name}.{method_name} with kwargs "
                    "needs a FuncDef to resolve parameter names"
                )
            arg_exprs = tuple(self._resolve_call_kwargs(
                arg_exprs, kwargs, ast_fd.args,
            ))
        declared = ast_fd.args if ast_fd else ()
        args_ir: list[ir.Value] = []
        for i, arg_expr in enumerate(arg_exprs):
            v = self._emit_expr(arg_expr)
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, arg_expr.ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, arg_expr.ty,
                )
            args_ir.append(v)
        ret_ty = method_fn.function_type.return_type
        call_name = "" if isinstance(ret_ty, ir.VoidType) \
                       else self._fresh(f"{info.name}.{method_name}.ret")
        return self._call_user(method_fn, args_ir, call_name)

    def _emit_direct_method_call(
        self, method_fn: ir.Function, self_val: ir.Value,
        info, method_name: str, arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        args_ir: list[ir.Value] = [self_val]
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        if kwargs:
            if ast_fd is None:
                raise NotImplementedError(
                    f"method {info.name}.{method_name} with kwargs needs a "
                    "FuncDef to resolve parameter names"
                )
            arg_exprs = tuple(self._resolve_call_kwargs(
                arg_exprs, kwargs, ast_fd.args, skip_self=True,
            ))
        declared = ast_fd.args[1:] if ast_fd else ()
        for i, arg_expr in enumerate(arg_exprs):
            v = self._emit_expr(arg_expr)
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, arg_expr.ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, arg_expr.ty,
                )
            args_ir.append(v)
        ret_ty = method_fn.function_type.return_type
        call_name = "" if isinstance(ret_ty, ir.VoidType) \
                       else self._fresh(f"{info.name}.{method_name}.ret")
        return self._call_user(method_fn, args_ir, call_name)

    def _maybe_emit_list_method(
        self, expr: Call, list_ty: ListType,
    ) -> Optional[ir.Value]:
        """Dispatch selected ``list`` methods directly to runtime helpers
        (libpython-free). Returns None when the method isn't in the fast
        path so callers can fall through to the generic dispatch."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs:
            return None  # generic path handles or errors
        name = attr.name
        recv = self._emit_expr(attr.obj)

        def _box(e: Expr) -> ir.Value:
            v = self._emit_expr(e)
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, e.ty,
            )

        if name == "append":
            if len(expr.args) != 1:
                return None
            self.builder.call(
                self.runtime["py_list_append"],
                [recv, _box(expr.args[0])],
            )
            return ir.Constant(_I1, 0)
        if name == "extend":
            if len(expr.args) != 1:
                return None
            self.builder.call(
                self.runtime["py_list_extend"],
                [recv, _box(expr.args[0])],
            )
            return ir.Constant(_I1, 0)
        if name == "insert":
            if len(expr.args) != 2:
                return None
            idx_val = self._emit_expr_as_i64(expr.args[0])
            self.builder.call(
                self.runtime["py_list_insert"],
                [recv, idx_val, _box(expr.args[1])],
            )
            return ir.Constant(_I1, 0)
        if name == "pop":
            if len(expr.args) == 0:
                idx_val = ir.Constant(_I64, -1)
            elif len(expr.args) == 1:
                idx_val = self._emit_expr_as_i64(expr.args[0])
            else:
                return None
            popped = self.builder.call(
                self.runtime["py_list_pop"], [recv, idx_val],
                name=self._fresh("list.pop"),
            )
            if not isinstance(list_ty.elem, DynType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime,
                    popped, list_ty.elem,
                )
            return popped
        if name == "remove":
            if len(expr.args) != 1:
                return None
            self.builder.call(
                self.runtime["py_list_remove"],
                [recv, _box(expr.args[0])],
            )
            return ir.Constant(_I1, 0)
        if name == "index":
            if len(expr.args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_list_index"],
                [recv, _box(expr.args[0])],
                name=self._fresh("list.index"),
            )
        return None

    _STR_METHOD_NATIVE = frozenset({
        "upper", "lower", "strip", "lstrip", "rstrip",
        "split", "join", "replace", "find", "count",
        "startswith", "endswith", "splitlines",
        "isdigit", "isalpha", "isspace", "isalnum",
    })

    def _extract_splitlines_keepends(self, expr: Call):
        """Return the ``keepends`` constant bool for a
        ``splitlines(keepends=…)`` call, or ``None`` if the caller
        didn't pass the keyword."""
        for key, v in (expr.kwargs or ()):
            if key == "keepends":
                if isinstance(v, BoolLit):
                    return bool(v.value)
                # Non-constant keepends — treat as ``True`` to be
                # safe; produced output preserves line endings.
                return True
        return None

    def _maybe_emit_str_method_via_dyn(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """DynType receiver whose method name matches one of the
        pcc-native str helpers — dispatch through the same runtime
        entries used by the StrType fast path. If the runtime value
        isn't actually a str, the helper crashes cleanly, matching
        Python's AttributeError behaviour in the spirit of 'no
        libpython'."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if attr.name not in self._STR_METHOD_NATIVE:
            return None
        # The only kwarg we recognise on a str method today is
        # ``splitlines(keepends=…)``. Everything else is routed via
        # the caller's fallback.
        if expr.kwargs and not (
            attr.name == "splitlines"
            and all(k == "keepends" for (k, _) in expr.kwargs)
        ):
            return None
        # Re-use the StrType fast path by recovering the StrType
        # marshal for the receiver. The dyn value is already a
        # PyObject*; marshal_to_object is a no-op when it already
        # is.
        # Build an expr clone whose obj.ty is StrType so the
        # existing helper's type checks line up. Because ``expr`` is
        # a frozen dataclass we go directly to the dispatch using
        # the same implementation inlined here.
        name = attr.name
        recv = self._emit_expr(attr.obj)

        def _str_arg(e: Expr) -> ir.Value:
            v = self._emit_expr(e)
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, e.ty,
            )

        if name in (
            "upper", "lower", "strip", "lstrip", "rstrip",
        ) and not expr.args:
            fn = {
                "upper": "py_str_upper", "lower": "py_str_lower",
                "strip": "py_str_strip",
                "lstrip": "py_str_lstrip", "rstrip": "py_str_rstrip",
            }[name]
            return self.builder.call(
                self.runtime[fn], [recv],
                name=self._fresh(f"dyn.str.{name}"),
            )
        if name in ("strip", "lstrip", "rstrip") and len(expr.args) == 1:
            fn = {
                "strip": "py_str_strip_chars",
                "lstrip": "py_str_lstrip_chars",
                "rstrip": "py_str_rstrip_chars",
            }[name]
            return self.builder.call(
                self.runtime[fn], [recv, _str_arg(expr.args[0])],
                name=self._fresh(f"dyn.str.{name}.chars"),
            )
        if name == "count" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_count"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("dyn.str.count"),
            )
        if name in (
            "isdigit", "isalpha", "isspace", "isalnum",
        ) and not expr.args:
            fn = {
                "isdigit": "py_str_isdigit",
                "isalpha": "py_str_isalpha",
                "isspace": "py_str_isspace",
                "isalnum": "py_str_isalnum",
            }[name]
            i32v = self.builder.call(
                self.runtime[fn], [recv],
                name=self._fresh(f"dyn.str.{name}"),
            )
            return self.builder.icmp_signed(
                "!=", i32v, ir.Constant(_I32, 0),
                name=self._fresh(f"dyn.str.{name}.i1"),
            )
        if name == "splitlines" and not expr.args:
            keepends = self._extract_splitlines_keepends(expr)
            if keepends is None:
                return self.builder.call(
                    self.runtime["py_str_splitlines"], [recv],
                    name=self._fresh("dyn.str.splitlines"),
                )
            return self.builder.call(
                self.runtime["py_str_splitlines_keepends"],
                [recv, ir.Constant(_I32, 1 if keepends else 0)],
                name=self._fresh("dyn.str.splitlines.keepends"),
            )
        if name == "split" and len(expr.args) <= 1:
            # ``split()`` with no args splits on whitespace — pass
            # NULL PyObject* to the runtime sep arg, which switches
            # py_str_split to the whitespace path.
            if expr.args:
                sep = _str_arg(expr.args[0])
            else:
                sep = ir.Constant(_CSTR, None)
            return self.builder.call(
                self.runtime["py_str_split"], [recv, sep],
                name=self._fresh("dyn.str.split"),
            )
        if name == "join" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_join"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("dyn.str.join"),
            )
        if name == "replace" and len(expr.args) == 2:
            return self.builder.call(
                self.runtime["py_str_replace"],
                [recv, _str_arg(expr.args[0]), _str_arg(expr.args[1])],
                name=self._fresh("dyn.str.replace"),
            )
        if name == "find" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_find"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("dyn.str.find"),
            )
        if name in ("startswith", "endswith") and len(expr.args) == 1:
            fn = {"startswith": "py_str_startswith",
                  "endswith": "py_str_endswith"}[name]
            i32v = self.builder.call(
                self.runtime[fn], [recv, _str_arg(expr.args[0])],
                name=self._fresh(f"dyn.str.{name}"),
            )
            return self.builder.icmp_signed(
                "!=", i32v, ir.Constant(_I32, 0),
                name=self._fresh(f"dyn.str.{name}.i1"),
            )
        return None

    def _maybe_emit_str_method(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """Dispatch selected ``str`` methods via the pcc str runtime."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs and not (
            attr.name == "splitlines"
            and all(k == "keepends" for (k, _) in expr.kwargs)
        ):
            return None
        name = attr.name
        recv = self._emit_expr(attr.obj)

        def _str_arg(e: Expr) -> ir.Value:
            v = self._emit_expr(e)
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, e.ty,
            )

        def _i32_to_i1(v: ir.Value, nm: str) -> ir.Value:
            return self.builder.icmp_signed(
                "!=", v, ir.Constant(_I32, 0),
                name=self._fresh(nm),
            )

        if name in (
            "upper", "lower", "strip", "lstrip", "rstrip",
        ) and not expr.args:
            fn = {
                "upper": "py_str_upper", "lower": "py_str_lower",
                "strip": "py_str_strip",
                "lstrip": "py_str_lstrip", "rstrip": "py_str_rstrip",
            }[name]
            return self.builder.call(
                self.runtime[fn], [recv],
                name=self._fresh(f"str.{name}"),
            )
        if name in ("strip", "lstrip", "rstrip") and len(expr.args) == 1:
            fn = {
                "strip": "py_str_strip_chars",
                "lstrip": "py_str_lstrip_chars",
                "rstrip": "py_str_rstrip_chars",
            }[name]
            return self.builder.call(
                self.runtime[fn], [recv, _str_arg(expr.args[0])],
                name=self._fresh(f"str.{name}.chars"),
            )
        if name == "count" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_count"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("str.count"),
            )
        if name in (
            "isdigit", "isalpha", "isspace", "isalnum",
        ) and not expr.args:
            fn = {
                "isdigit": "py_str_isdigit",
                "isalpha": "py_str_isalpha",
                "isspace": "py_str_isspace",
                "isalnum": "py_str_isalnum",
            }[name]
            i32v = self.builder.call(
                self.runtime[fn], [recv],
                name=self._fresh(f"str.{name}"),
            )
            return self.builder.icmp_signed(
                "!=", i32v, ir.Constant(_I32, 0),
                name=self._fresh(f"str.{name}.i1"),
            )
        if name == "splitlines" and not expr.args:
            keepends = self._extract_splitlines_keepends(expr)
            if keepends is None:
                return self.builder.call(
                    self.runtime["py_str_splitlines"], [recv],
                    name=self._fresh("str.splitlines"),
                )
            return self.builder.call(
                self.runtime["py_str_splitlines_keepends"],
                [recv, ir.Constant(_I32, 1 if keepends else 0)],
                name=self._fresh("str.splitlines.keepends"),
            )
        if name == "split":
            if len(expr.args) > 1:
                return None
            if expr.args:
                sep = _str_arg(expr.args[0])
            else:
                sep = ir.Constant(_CSTR, None)
            return self.builder.call(
                self.runtime["py_str_split"], [recv, sep],
                name=self._fresh("str.split"),
            )
        if name == "join":
            if len(expr.args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_str_join"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("str.join"),
            )
        if name == "replace":
            if len(expr.args) != 2:
                return None
            return self.builder.call(
                self.runtime["py_str_replace"],
                [recv, _str_arg(expr.args[0]), _str_arg(expr.args[1])],
                name=self._fresh("str.replace"),
            )
        if name == "find":
            if len(expr.args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_str_find"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("str.find"),
            )
        if name in ("startswith", "endswith"):
            if len(expr.args) != 1:
                return None
            fn = {"startswith": "py_str_startswith",
                  "endswith": "py_str_endswith"}[name]
            i32v = self.builder.call(
                self.runtime[fn], [recv, _str_arg(expr.args[0])],
                name=self._fresh(f"str.{name}"),
            )
            return _i32_to_i1(i32v, f"str.{name}.i1")
        return None

    def _maybe_emit_dict_method(
        self, expr: Call, dict_ty: DictType,
    ) -> Optional[ir.Value]:
        """Dispatch selected ``dict`` methods directly to runtime helpers."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs:
            return None
        name = attr.name
        recv = self._emit_expr(attr.obj)

        def _box(e: Expr) -> ir.Value:
            v = self._emit_expr(e)
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, e.ty,
            )

        if name == "get":
            if len(expr.args) == 1:
                return self.builder.call(
                    self.runtime["py_dict_get"],
                    [recv, _box(expr.args[0])],
                    name=self._fresh("dict.get"),
                )
            if len(expr.args) == 2:
                return self.builder.call(
                    self.runtime["py_dict_get_default"],
                    [recv, _box(expr.args[0]), _box(expr.args[1])],
                    name=self._fresh("dict.get.dflt"),
                )
            return None
        if name == "keys":
            if expr.args:
                return None
            return self.builder.call(
                self.runtime["py_dict_keys"], [recv],
                name=self._fresh("dict.keys"),
            )
        if name == "values":
            if expr.args:
                return None
            return self.builder.call(
                self.runtime["py_dict_values"], [recv],
                name=self._fresh("dict.values"),
            )
        if name == "items":
            if expr.args:
                return None
            return self.builder.call(
                self.runtime["py_dict_items"], [recv],
                name=self._fresh("dict.items"),
            )
        if name == "setdefault" and len(expr.args) == 2:
            # ``d.setdefault(k, default)`` — if ``k`` exists, return
            # its value; otherwise insert and return ``default``.
            # Compile to: existing = py_dict_get(d, k); if existing is
            # NULL then py_dict_set(d, k, default); existing = default;
            # return existing.
            k_obj = _box(expr.args[0])
            default_obj = _box(expr.args[1])
            fn = self.current_function
            existing = self.builder.call(
                self.runtime["py_dict_get"], [recv, k_obj],
                name=self._fresh("setdefault.get"),
            )
            null_p = ir.Constant(_CSTR, None)
            is_missing = self.builder.icmp_signed(
                "==", existing, null_p,
                name=self._fresh("setdefault.miss"),
            )
            miss_bb = fn.append_basic_block(
                name=self._fresh("setdefault.miss"),
            )
            join_bb = fn.append_basic_block(
                name=self._fresh("setdefault.join"),
            )
            cur_bb = self.builder.block
            self.builder.cbranch(is_missing, miss_bb, join_bb)
            self.builder.position_at_end(miss_bb)
            self.builder.call(
                self.runtime["py_dict_set"], [recv, k_obj, default_obj],
            )
            miss_exit = self.builder.block
            self.builder.branch(join_bb)
            self.builder.position_at_end(join_bb)
            phi = self.builder.phi(
                _CSTR, name=self._fresh("setdefault.result"),
            )
            phi.add_incoming(default_obj, miss_exit)
            phi.add_incoming(existing, cur_bb)
            return phi
        return None

    _BUILTIN_TYPE_MATCHERS = {
        "str": StrType,
        "int": IntType,
        "float": FloatType,
        "bool": BoolType,
        "list": ListType,
        "dict": DictType,
        "tuple": TupleType,
    }

    def _compile_time_isinstance(
        self, obj_expr: Expr, class_ident: str,
    ) -> Optional[ir.Value]:
        """Resolve ``isinstance(x, BuiltinType)`` at compile time when
        the operand's static type is known, returning a constant ``i1``.
        Returns None if class_ident isn't a known builtin or the
        operand's type is DynType (needs runtime check, which today
        doesn't have a per-builtin helper)."""
        matcher = self._BUILTIN_TYPE_MATCHERS.get(class_ident)
        if matcher is None:
            return None
        ty = obj_expr.ty
        if isinstance(ty, DynType):
            return None
        return ir.Constant(_I1, 1 if isinstance(ty, matcher) else 0)

    def _maybe_emit_dict_builtin(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """``dict()`` → empty dict. ``dict(k1=v1, k2=v2)`` → set
        each kwarg. ``dict(another_dict)`` where arg is DictType
        → shallow copy via iterator-over-keys.
        Iterable-of-pairs form isn't supported yet."""
        new_dict = self.builder.call(
            self.runtime["py_dict_new"], [],
            name=self._fresh("dict.new"),
        )
        # kwargs form
        if not expr.args and expr.kwargs:
            for kw_name, kw_expr in expr.kwargs:
                k_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    self._emit_str_literal(kw_name),
                    StrType(name="str"),
                )
                v = self._emit_expr(kw_expr)
                v_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, kw_expr.ty,
                )
                self.builder.call(
                    self.runtime["py_dict_set"],
                    [new_dict, k_obj, v_obj],
                )
            return new_dict
        if not expr.args:
            return new_dict
        arg = expr.args[0]
        arg_ty = arg.ty
        if isinstance(arg_ty, DictType) or isinstance(arg_ty, DynType):
            # Shallow copy of a dict — iterate keys, get values,
            # insert into the new dict.
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg_ty,
            )
            keys_list = self.builder.call(
                self.runtime["py_dict_keys"], [src_obj],
                name=self._fresh("dict.copy.keys"),
            )
            fn = self.current_function
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [keys_list],
                name=self._fresh("dict.copy.len"),
            )
            idx_slot = self._alloca_in_entry(
                _I64, name="dict.copy.idx.addr",
            )
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.cond"),
            )
            body_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.body"),
            )
            step_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.step"),
            )
            end_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.end"),
            )
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("idx"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            k_elem = self.builder.call(
                self.runtime["py_list_get"], [keys_list, cur],
                name=self._fresh("dict.copy.key"),
            )
            v_elem = self.builder.call(
                self.runtime["py_dict_get"], [src_obj, k_elem],
                name=self._fresh("dict.copy.val"),
            )
            self.builder.call(
                self.runtime["py_dict_set"],
                [new_dict, k_elem, v_elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return new_dict
        return None

    def _maybe_emit_tuple_builtin(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """``tuple()`` / ``tuple([a, b])`` — small subset that matches
        pcc's own usage. Literal lists/tuples fold inline into a new
        ``py_tuple_new`` + per-element ``py_tuple_set_item``. Other
        iterable shapes return None (caller surfaces the original
        unknown-builtin error)."""
        if not expr.args:
            n_val = ir.Constant(_I64, 0)
            return self.builder.call(
                self.runtime["py_tuple_new"], [n_val],
                name=self._fresh("tuple.new"),
            )
        arg = expr.args[0]
        if isinstance(arg, (ListExpr, TupleExpr)):
            n = len(arg.elems)
            n_val = ir.Constant(_I64, n)
            tup = self.builder.call(
                self.runtime["py_tuple_new"], [n_val],
                name=self._fresh("tuple.new"),
            )
            for i, el in enumerate(arg.elems):
                v = self._emit_expr(el)
                v_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, el.ty,
                )
                self.builder.call(
                    self.runtime["py_tuple_set_item"],
                    [tup, ir.Constant(_I64, i), v_obj],
                )
            return tup
        # DynType / ListType / generic iterable: get the length,
        # allocate a tuple of that size, fill via py_obj_getitem.
        arg_ty = arg.ty
        if isinstance(arg_ty, (ListType, TupleType, DynType)):
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg_ty,
            )
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [src_obj],
                name=self._fresh("tuple.src.len"),
            )
            tup = self.builder.call(
                self.runtime["py_tuple_new"], [n_val],
                name=self._fresh("tuple.new"),
            )
            fn = self.current_function
            idx_slot = self._alloca_in_entry(_I64, name="tuple.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("tuple.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("tuple.body"))
            step_bb = fn.append_basic_block(name=self._fresh("tuple.step"))
            end_bb = fn.append_basic_block(name=self._fresh("tuple.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("tuple.idx"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("tuple.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"], [cur],
                name=self._fresh("tuple.idx.box"),
            )
            elem = self.builder.call(
                self.runtime["py_obj_getitem"], [src_obj, idx_box],
                name=self._fresh("tuple.elem"),
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"], [tup, cur, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("tuple.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return tup
        return None

    def _emit_getattr_builtin(self, expr: Call) -> ir.Value:
        """``getattr(obj, name)`` / ``getattr(obj, name, default)``.
        Routes through the runtime ``py_obj_getattr`` helper. The
        three-arg form is treated as ``getattr(obj, name)`` — pcc
        doesn't yet handle default-on-AttributeError semantics
        (runtime would need a ``py_obj_getattr_default`` variant).
        """
        obj_val = self._emit_as_object(expr.args[0])
        name_expr = expr.args[1]
        if isinstance(name_expr, StrLit):
            name_ptr = self._attr_name_ptr(name_expr.value)
        else:
            # Dynamic name — marshal and use py_str_utf8 to grab
            # the C string.
            nv = self._emit_expr(name_expr)
            n_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                nv, name_expr.ty,
            )
            name_ptr = self.builder.call(
                self.runtime["py_str_utf8"], [n_obj],
                name=self._fresh("getattr.name"),
            )
        return self.builder.call(
            self.runtime["py_obj_getattr"], [obj_val, name_ptr],
            name=self._fresh("getattr"),
        )

    def _emit_type_builtin(self, expr: Call) -> ir.Value:
        """``type(obj)`` — returns the runtime class PyObject*.
        Uses ``py_obj_getattr(obj, "__class__")`` which the runtime
        resolves on any pcc-native object."""
        obj_val = self._emit_as_object(expr.args[0])
        name_ptr = self._attr_name_ptr("__class__")
        return self.builder.call(
            self.runtime["py_obj_getattr"], [obj_val, name_ptr],
            name=self._fresh("type"),
        )

    def _maybe_emit_list_builtin(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """``list()`` / ``list([a, b])`` / ``list((a, b))`` / ``list(dict_keys)``.

        - no args → empty ``py_list_new(0)``.
        - list/tuple literal → alloc + per-element ``py_list_append``.
        - list-typed arg → same (materialises a copy).
        - dict-typed arg → ``py_dict_keys(d)`` (already a list).
        """
        new_list = self.builder.call(
            self.runtime["py_list_new"], [ir.Constant(_I64, 0)],
            name=self._fresh("list.new"),
        )
        if not expr.args:
            return new_list
        arg = expr.args[0]
        if isinstance(arg, (ListExpr, TupleExpr)):
            for el in arg.elems:
                v = self._emit_expr(el)
                v_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, el.ty,
                )
                self.builder.call(
                    self.runtime["py_list_append"], [new_list, v_obj],
                )
            return new_list
        arg_ty = arg.ty
        if isinstance(arg_ty, DictType):
            obj = self._emit_expr(arg)
            return self.builder.call(
                self.runtime["py_dict_keys"], [obj],
                name=self._fresh("list.from_dict"),
            )
        if isinstance(arg_ty, ListType):
            # list(x) where x is already a list — evaluate and return
            # the same PyObject*. (No copy; downstream mutation would
            # leak to the source. Phase-1 acceptable.)
            return self._emit_expr(arg)
        if isinstance(arg_ty, (TupleType, DynType)):
            # Iterate source via py_obj_len + py_obj_getitem and
            # append to a fresh list. Works for any pcc-native
            # container that supports length + index access.
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg_ty,
            )
            fn = self.current_function
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [src_obj],
                name=self._fresh("list.src.len"),
            )
            idx_slot = self._alloca_in_entry(_I64, name="list.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("list.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("list.body"))
            step_bb = fn.append_basic_block(name=self._fresh("list.step"))
            end_bb = fn.append_basic_block(name=self._fresh("list.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("list.idx"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("list.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"], [cur],
                name=self._fresh("list.idx.box"),
            )
            elem = self.builder.call(
                self.runtime["py_obj_getitem"], [src_obj, idx_box],
                name=self._fresh("list.elem"),
            )
            self.builder.call(
                self.runtime["py_list_append"], [new_list, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("list.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return new_list
        return None

    def _maybe_emit_set_builtin(self, expr: Call) -> Optional[ir.Value]:
        """``set()`` / ``set([a, b])`` / ``set((a, b, c))`` / ``set(iterable)``.

        - no args → empty ``py_set_new``.
        - literal list/tuple → allocate + add each element.
        - any other iterable (ListType / TupleType / DictType /
          DynType) → materialise as PyObject*, iterate via the
          generic ``py_obj_len`` + ``py_obj_getitem``, and add
          each element to the set.
        """
        new_set = self.builder.call(
            self.runtime["py_set_new"], [],
            name=self._fresh("set.new"),
        )
        if not expr.args:
            return new_set
        arg = expr.args[0]
        if isinstance(arg, (ListExpr, TupleExpr)):
            for el in arg.elems:
                v = self._emit_expr(el)
                v_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, el.ty,
                )
                self.builder.call(
                    self.runtime["py_set_add"], [new_set, v_obj],
                )
            return new_set
        arg_ty = arg.ty
        if isinstance(arg_ty, (ListType, TupleType, DictType, DynType)):
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg_ty,
            )
            fn = self.current_function
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [src_obj],
                name=self._fresh("set.src.len"),
            )
            idx_slot = self._alloca_in_entry(_I64, name="set.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("set.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("set.body"))
            step_bb = fn.append_basic_block(name=self._fresh("set.step"))
            end_bb = fn.append_basic_block(name=self._fresh("set.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("set.idx"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("set.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"], [cur],
                name=self._fresh("set.idx.box"),
            )
            elem = self.builder.call(
                self.runtime["py_obj_getitem"], [src_obj, idx_box],
                name=self._fresh("set.elem"),
            )
            self.builder.call(
                self.runtime["py_set_add"], [new_set, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("set.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return new_set
        return None

    def _maybe_emit_int_builtin(self, expr: Call) -> Optional[ir.Value]:
        """``int(x)`` / ``int(s, base)``:

        - int argument → identity (already int).
        - bool argument → ``zext`` to i64.
        - float argument → ``fptosi``.
        - str argument → ``py_int_from_cstr(utf8, base)`` then unbox.
        Returns None for unsupported shapes so the caller errors.
        """
        arg = expr.args[0]
        arg_ty = arg.ty
        base_val: ir.Value
        if len(expr.args) == 2:
            base_val = self._emit_expr_as_i64(expr.args[1])
            base_val = self.builder.trunc(
                base_val, _I32, name=self._fresh("int.base"),
            )
        else:
            base_val = ir.Constant(_I32, 10)
        if isinstance(arg_ty, IntType):
            return self._emit_expr(arg)
        if isinstance(arg_ty, BoolType):
            v = self._emit_expr(arg)
            if v.type is _I1:
                return self.builder.zext(
                    v, _I64, name=self._fresh("int.from_bool"),
                )
            return v
        if isinstance(arg_ty, FloatType):
            v = self._emit_expr(arg)
            return self.builder.fptosi(
                v, _I64, name=self._fresh("int.from_float"),
            )
        if isinstance(arg_ty, StrType):
            s_obj = self._emit_expr(arg)
            cstr = self.builder.call(
                self.runtime["py_str_utf8"], [s_obj],
                name=self._fresh("int.cstr"),
            )
            boxed = self.builder.call(
                self.runtime["py_int_from_cstr"], [cstr, base_val],
                name=self._fresh("int.parse"),
            )
            # Unbox to native i64 via the existing marshal helper.
            return marshal.marshal_from_object(
                self.builder, self.module, self.runtime,
                boxed, IntType(name="int"),
            )
        if isinstance(arg_ty, DynType):
            # Dyn arg: treat as string if ``py_str_utf8`` returns a
            # non-NULL buffer, else fall through to ``py_int_to_i64``
            # for values that are already numeric PyObject*.
            obj = self._emit_expr(arg)
            cstr = self.builder.call(
                self.runtime["py_str_utf8"], [obj],
                name=self._fresh("int.dyn.cstr"),
            )
            boxed = self.builder.call(
                self.runtime["py_int_from_cstr"], [cstr, base_val],
                name=self._fresh("int.dyn.parse"),
            )
            return marshal.marshal_from_object(
                self.builder, self.module, self.runtime,
                boxed, IntType(name="int"),
            )
        return None

    def _maybe_emit_sum_literal(self, expr: Call) -> Optional[ir.Value]:
        """``sum([a, b, c])`` / ``sum((a, b), start)`` for numeric
        literal containers — fold element-wise add, seeded with the
        start value if given else 0.

        Also handles the runtime case ``sum(iterable)`` when the
        iterable's static type is ``ListType`` / ``TupleType`` /
        ``DynType`` — assumes int elements and uses the generic
        ``py_obj_len`` / ``py_obj_getitem`` loop.
        """
        arg = expr.args[0]
        start = expr.args[1] if len(expr.args) == 2 else None
        if not isinstance(arg, (TupleExpr, ListExpr)):
            if not isinstance(
                arg.ty, (ListType, TupleType, DynType),
            ):
                return None
            # Runtime iteration path — always int-result; float
            # sum(iterable) falls through to NotImplementedError.
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg.ty,
            )
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [src_obj],
                name=self._fresh("sum.src.len"),
            )
            fn_ = self.current_function
            idx_slot = self._alloca_in_entry(_I64, name="sum.idx.addr")
            acc_slot = self._alloca_in_entry(_I64, name="sum.acc.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            if start is not None:
                start_i64 = self._emit_expr_as_i64(start)
                self.builder.store(start_i64, acc_slot)
            else:
                self.builder.store(ir.Constant(_I64, 0), acc_slot)
            cond_bb = fn_.append_basic_block(name=self._fresh("sum.cond"))
            body_bb = fn_.append_basic_block(name=self._fresh("sum.body"))
            step_bb = fn_.append_basic_block(name=self._fresh("sum.step"))
            end_bb = fn_.append_basic_block(name=self._fresh("sum.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("sum.idx"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("sum.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"], [cur],
                name=self._fresh("sum.idx.box"),
            )
            elem_obj = self.builder.call(
                self.runtime["py_obj_getitem"], [src_obj, idx_box],
                name=self._fresh("sum.elem"),
            )
            elem_i64 = marshal.marshal_from_object(
                self.builder, self.module, self.runtime,
                elem_obj, IntType(name="int"),
            )
            acc_cur = self.builder.load(
                acc_slot, name=self._fresh("sum.acc"),
            )
            new_acc = self.builder.add(
                acc_cur, elem_i64, name=self._fresh("sum.acc.next"),
            )
            self.builder.store(new_acc, acc_slot)
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("sum.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return self.builder.load(
                acc_slot, name=self._fresh("sum.result"),
            )
        elems = arg.elems
        start = expr.args[1] if len(expr.args) == 2 else None
        any_float = any(isinstance(e.ty, FloatType) for e in elems)
        if start is not None and isinstance(start.ty, FloatType):
            any_float = True
        if not all(
            isinstance(e.ty, (IntType, FloatType, BoolType))
            for e in elems
        ):
            return None
        if start is not None and not isinstance(
            start.ty, (IntType, FloatType, BoolType),
        ):
            return None
        if any_float:
            if start is not None:
                acc = self._emit_expr(start)
                if not isinstance(start.ty, FloatType):
                    acc = self.builder.sitofp(
                        acc, _DOUBLE, name=self._fresh("promote"),
                    )
            else:
                acc = ir.Constant(_DOUBLE, 0.0)
            for e in elems:
                v = self._emit_expr(e)
                if not isinstance(e.ty, FloatType):
                    v = self.builder.sitofp(
                        v, _DOUBLE, name=self._fresh("promote"),
                    )
                acc = self.builder.fadd(
                    acc, v, name=self._fresh("sum"),
                )
            return acc
        # All-int path.
        if start is not None:
            acc = self._emit_expr_as_i64(start)
        else:
            acc = ir.Constant(_I64, 0)
        for e in elems:
            v = self._emit_expr_as_i64(e)
            acc = self.builder.add(acc, v, name=self._fresh("sum"))
        return acc

    def _maybe_emit_any_all_literal(
        self, expr: Call, name: str,
    ) -> Optional[ir.Value]:
        """``any((a, b, c))`` / ``all([a, b, c])`` over a literal tuple
        or list — lower via a short-circuit chain of ``or`` / ``and``
        over the elements' truthiness. For runtime iterables
        (ListType / TupleType / DictType / DynType) iterate via
        ``py_obj_len`` / ``py_obj_getitem`` with early exit."""
        arg = expr.args[0]
        if not isinstance(arg, (TupleExpr, ListExpr)):
            arg_ty = arg.ty
            if not isinstance(
                arg_ty, (ListType, TupleType, DictType, DynType),
            ):
                return None
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg_ty,
            )
            fn_ = self.current_function
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [src_obj],
                name=self._fresh(f"{name}.src.len"),
            )
            idx_slot = self._alloca_in_entry(_I64, name=f"{name}.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            # Result alloca — default identity (any=False, all=True).
            result_slot = self._alloca_in_entry(
                _I1, name=f"{name}.result.addr",
            )
            init = 0 if name == "any" else 1
            self.builder.store(
                ir.Constant(_I1, init), result_slot,
            )
            cond_bb = fn_.append_basic_block(
                name=self._fresh(f"{name}.cond"),
            )
            body_bb = fn_.append_basic_block(
                name=self._fresh(f"{name}.body"),
            )
            step_bb = fn_.append_basic_block(
                name=self._fresh(f"{name}.step"),
            )
            end_bb = fn_.append_basic_block(
                name=self._fresh(f"{name}.end"),
            )
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(
                idx_slot, name=self._fresh(f"{name}.idx"),
            )
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh(f"{name}.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"], [cur],
                name=self._fresh(f"{name}.idx.box"),
            )
            elem = self.builder.call(
                self.runtime["py_obj_getitem"], [src_obj, idx_box],
                name=self._fresh(f"{name}.elem"),
            )
            truthy_i32 = self.builder.call(
                self.runtime["py_obj_truthy"], [elem],
                name=self._fresh(f"{name}.truthy"),
            )
            truthy = self.builder.icmp_signed(
                "!=", truthy_i32, ir.Constant(_I32, 0),
                name=self._fresh(f"{name}.truthy.i1"),
            )
            if name == "any":
                # Truthy → result True, exit. Falsy → continue.
                exit_bb = fn_.append_basic_block(
                    name=self._fresh("any.hit"),
                )
                self.builder.cbranch(truthy, exit_bb, step_bb)
                self.builder.position_at_end(exit_bb)
                self.builder.store(ir.Constant(_I1, 1), result_slot)
                self.builder.branch(end_bb)
            else:  # all
                exit_bb = fn_.append_basic_block(
                    name=self._fresh("all.miss"),
                )
                self.builder.cbranch(truthy, step_bb, exit_bb)
                self.builder.position_at_end(exit_bb)
                self.builder.store(ir.Constant(_I1, 0), result_slot)
                self.builder.branch(end_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh(f"{name}.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return self.builder.load(
                result_slot, name=self._fresh(f"{name}.result"),
            )
        elems = arg.elems
        if not elems:
            return ir.Constant(_I1, 0 if name == "any" else 1)

        # Open the diamond per element to get a true short-circuit
        # chain; phi at the join carries either the accumulated result
        # or the per-element truthy value.
        fn = self.current_function
        join_bb = fn.append_basic_block(
            name=self._fresh(f"{name}.join"),
        )
        incoming: list[tuple[ir.Value, object]] = []
        for i, elem in enumerate(elems):
            v = self._emit_expr(elem)
            truthy = self._truthy(v, elem.ty)
            is_last = (i == len(elems) - 1)
            if is_last:
                incoming.append((truthy, self.builder.block))
                self.builder.branch(join_bb)
                break
            next_bb = fn.append_basic_block(
                name=self._fresh(f"{name}.next"),
            )
            if name == "any":
                # Truthy wins — branch to join with True.
                true_val = ir.Constant(_I1, 1)
                # Need to go through a small block so the incoming
                # value recorded at join is the constant rather than
                # a phi-dependent SSA mapping.
                true_bb = fn.append_basic_block(
                    name=self._fresh("any.true"),
                )
                self.builder.cbranch(truthy, true_bb, next_bb)
                self.builder.position_at_end(true_bb)
                incoming.append((true_val, true_bb))
                self.builder.branch(join_bb)
            else:  # all
                false_val = ir.Constant(_I1, 0)
                false_bb = fn.append_basic_block(
                    name=self._fresh("all.false"),
                )
                self.builder.cbranch(truthy, next_bb, false_bb)
                self.builder.position_at_end(false_bb)
                incoming.append((false_val, false_bb))
                self.builder.branch(join_bb)
            self.builder.position_at_end(next_bb)

        self.builder.position_at_end(join_bb)
        phi = self.builder.phi(_I1, name=self._fresh(name))
        for val, pred_bb in incoming:
            phi.add_incoming(val, pred_bb)
        return phi

    def _emit_walrus(self, expr: Call) -> ir.Value:
        """Lower ``x := value`` from the ``_walrus`` sentinel emitted
        by ``pcc.parse.py_lift._e_Assign`` — evaluate the value,
        store into ``x`` via the name-assign helper, return the value
        for use in the surrounding expression."""
        if len(expr.args) != 2:
            raise L1CodegenError(
                "_walrus sentinel expects (target, value) args"
            )
        target, value_expr = expr.args
        if not isinstance(target, Name):
            raise NotImplementedError(
                "walrus target must be a plain Name"
            )
        value = self._emit_expr(value_expr)
        self._store_value_at_name(target, value, value_expr.ty)
        return value

    def _maybe_emit_min_max_iter(
        self, expr: Call, name: str,
    ) -> Optional[ir.Value]:
        """``min(xs)`` / ``max(xs)`` on a ListType / TupleType /
        DynType iterable of ints. ``default`` kwarg is accepted via
        the resolver and seeds the accumulator on empty."""
        arg = expr.args[0]
        arg_ty = arg.ty
        if not isinstance(arg_ty, (ListType, TupleType, DynType)):
            return None
        # Optional ``default=`` kwarg.
        default_val = None
        for k, v in (expr.kwargs or ()):
            if k == "default":
                default_val = self._emit_expr_as_i64(v)
            else:
                return None  # unknown kwarg
        src_val = self._emit_expr(arg)
        src_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, src_val, arg_ty,
        )
        n_val = self.builder.call(
            self.runtime["py_obj_len"], [src_obj],
            name=self._fresh(f"{name}.src.len"),
        )
        fn = self.current_function
        idx_slot = self._alloca_in_entry(_I64, name=f"{name}.idx.addr")
        acc_slot = self._alloca_in_entry(_I64, name=f"{name}.acc.addr")
        # Initial fill: if empty and no default → runtime error (we'd
        # have to emit a trap). With default, seed. With non-empty,
        # seed from elem[0] below.
        is_empty = self.builder.icmp_signed(
            "==", n_val, ir.Constant(_I64, 0),
            name=self._fresh(f"{name}.empty"),
        )
        empty_bb = fn.append_basic_block(name=self._fresh(f"{name}.empty"))
        seed_bb = fn.append_basic_block(name=self._fresh(f"{name}.seed"))
        self.builder.cbranch(is_empty, empty_bb, seed_bb)
        self.builder.position_at_end(empty_bb)
        if default_val is not None:
            self.builder.store(default_val, acc_slot)
        else:
            # No default: store 0 as a fallback (Python would raise
            # ValueError; we don't have exception wiring here).
            self.builder.store(ir.Constant(_I64, 0), acc_slot)
        self.builder.store(ir.Constant(_I64, 1), idx_slot)
        end_bb = fn.append_basic_block(name=self._fresh(f"{name}.end"))
        self.builder.branch(end_bb)

        self.builder.position_at_end(seed_bb)
        # Seed accumulator from index 0.
        zero_box = self.builder.call(
            self.runtime["py_int_from_i64"], [ir.Constant(_I64, 0)],
            name=self._fresh(f"{name}.seed.box"),
        )
        first = self.builder.call(
            self.runtime["py_obj_getitem"], [src_obj, zero_box],
            name=self._fresh(f"{name}.first"),
        )
        first_i64 = marshal.marshal_from_object(
            self.builder, self.module, self.runtime,
            first, IntType(name="int"),
        )
        self.builder.store(first_i64, acc_slot)
        self.builder.store(ir.Constant(_I64, 1), idx_slot)

        cond_bb = fn.append_basic_block(name=self._fresh(f"{name}.cond"))
        body_bb = fn.append_basic_block(name=self._fresh(f"{name}.body"))
        step_bb = fn.append_basic_block(name=self._fresh(f"{name}.step"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh(f"{name}.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh(f"{name}.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh(f"{name}.idx.box"),
        )
        elem_obj = self.builder.call(
            self.runtime["py_obj_getitem"], [src_obj, idx_box],
            name=self._fresh(f"{name}.elem"),
        )
        elem_i64 = marshal.marshal_from_object(
            self.builder, self.module, self.runtime,
            elem_obj, IntType(name="int"),
        )
        acc_cur = self.builder.load(
            acc_slot, name=self._fresh(f"{name}.acc"),
        )
        cmp_op = "<" if name == "min" else ">"
        is_better = self.builder.icmp_signed(
            cmp_op, elem_i64, acc_cur,
            name=self._fresh(f"{name}.cmp"),
        )
        new_acc = self.builder.select(
            is_better, elem_i64, acc_cur,
            name=self._fresh(f"{name}.pick"),
        )
        self.builder.store(new_acc, acc_slot)
        self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        nxt = self.builder.add(
            cur, ir.Constant(_I64, 1),
            name=self._fresh(f"{name}.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)
        return self.builder.load(
            acc_slot, name=self._fresh(f"{name}.result"),
        )

    def _emit_min_max_builtin(self, expr: Call, name: str) -> ir.Value:
        """Lower ``min(a, b)`` / ``max(a, b)`` when both args are
        native int / float / bool (or DynType narrowed via
        ``_emit_expr_as_i64``). Non-numeric container forms fall
        through to NotImplementedError."""
        a_expr, b_expr = expr.args
        a_ty, b_ty = a_expr.ty, b_expr.ty
        numeric = (IntType, FloatType, BoolType, DynType)
        if not (isinstance(a_ty, numeric) and isinstance(b_ty, numeric)):
            raise NotImplementedError(
                f"Layer 1 {name}() with non-numeric args "
                f"({a_ty!r}, {b_ty!r}) needs runtime support"
            )
        if isinstance(a_ty, FloatType) or isinstance(b_ty, FloatType):
            av = self._emit_expr(a_expr)
            bv = self._emit_expr(b_expr)
            if not isinstance(a_ty, FloatType):
                av = self.builder.sitofp(
                    av, _DOUBLE, name=self._fresh("promote"),
                )
            if not isinstance(b_ty, FloatType):
                bv = self.builder.sitofp(
                    bv, _DOUBLE, name=self._fresh("promote"),
                )
            cmp = self.builder.fcmp_ordered(
                "<" if name == "min" else ">",
                av, bv, name=self._fresh(f"{name}.cmp"),
            )
        else:
            av = self._emit_expr_as_i64(a_expr)
            bv = self._emit_expr_as_i64(b_expr)
            cmp = self.builder.icmp_signed(
                "<" if name == "min" else ">",
                av, bv, name=self._fresh(f"{name}.cmp"),
            )
        return self.builder.select(
            cmp, av, bv, name=self._fresh(name),
        )

    def _emit_abs_builtin(self, expr: Call) -> ir.Value:
        """``abs(x)`` for native int / float / bool."""
        a_expr = expr.args[0]
        a_ty = a_expr.ty
        if isinstance(a_ty, (IntType, BoolType)):
            v = self._emit_expr_as_i64(a_expr)
            zero = ir.Constant(_I64, 0)
            neg = self.builder.icmp_signed(
                "<", v, zero, name=self._fresh("abs.neg"),
            )
            negated = self.builder.sub(
                zero, v, name=self._fresh("abs.negate"),
            )
            return self.builder.select(
                neg, negated, v, name=self._fresh("abs"),
            )
        if isinstance(a_ty, FloatType):
            v = self._emit_expr(a_expr)
            zero = ir.Constant(_DOUBLE, 0.0)
            neg = self.builder.fcmp_ordered(
                "<", v, zero, name=self._fresh("abs.neg"),
            )
            negated = self.builder.fsub(
                zero, v, name=self._fresh("abs.negate"),
            )
            return self.builder.select(
                neg, negated, v, name=self._fresh("abs"),
            )
        raise NotImplementedError(
            f"Layer 1 abs() with arg type {a_ty!r} needs runtime support"
        )

    def _emit_isinstance_call(self, expr: Call) -> ir.Value:
        if len(expr.args) != 2:
            raise L1CodegenError(
                "isinstance expects exactly two arguments"
            )
        class_arg = expr.args[1]

        # Tuple form ``isinstance(x, (A, B, C))`` → OR of per-class checks.
        if isinstance(class_arg, TupleExpr):
            if not class_arg.elems:
                # Still emit the operand for side-effect parity.
                self._emit_as_object(expr.args[0])
                return ir.Constant(_I1, 0)
            names: list[str] = []
            for e in class_arg.elems:
                if isinstance(e, Name):
                    names.append(e.ident)
                elif isinstance(e, Attr):
                    # ``mod.Class`` — use the tail token; the Name
                    # isn't in scope locally but may be a pcc-class
                    # name or a builtin (e.g. ``c_ast.Switch`` →
                    # ``Switch``). Unknown names fall through to
                    # compile-time False in the OR chain below.
                    names.append(e.name)
                else:
                    raise NotImplementedError(
                        "isinstance tuple form requires bare class names "
                        "or module.name chains; got "
                        f"{type(e).__name__}"
                    )
            acc: Optional[ir.Value] = None
            obj_val: Optional[ir.Value] = None
            for nm in names:
                ct = self._compile_time_isinstance(expr.args[0], nm)
                if ct is None:
                    if nm in self.class_lowering.classes:
                        if obj_val is None:
                            obj_val = self._emit_as_object(expr.args[0])
                        ct = self.class_lowering.emit_isinstance(obj_val, nm)
                    else:
                        # Unknown class (imported from an external module
                        # that pcc can't introspect). Assume False so
                        # the OR-chain still resolves correctly when
                        # one of the builtin/native branches matches.
                        ct = ir.Constant(_I1, 0)
                acc = ct if acc is None else self.builder.or_(
                    acc, ct, name=self._fresh("isinstance_or"),
                )
            assert acc is not None
            return acc

        # ``mod.Class`` second-arg: use tail token as the class name.
        if isinstance(class_arg, Attr):
            cls_ident = class_arg.name
        elif isinstance(class_arg, Name):
            cls_ident = class_arg.ident
        else:
            raise NotImplementedError(
                "isinstance second argument must be a bare class name, "
                "a tuple of bare class names, or a module.attr chain"
            )
        # Compile-time check for builtin types when operand type is known.
        ct = self._compile_time_isinstance(expr.args[0], cls_ident)
        if ct is not None:
            return ct
        if cls_ident not in self.class_lowering.classes:
            # Unknown/foreign class — treat as False (see tuple form).
            self._emit_as_object(expr.args[0])
            return ir.Constant(_I1, 0)
        obj_val = self._emit_as_object(expr.args[0])
        return self.class_lowering.emit_isinstance(obj_val, cls_ident)

    def _emit_len_call(self, expr: Call) -> ir.Value:
        """``len(x)`` → type-specialised runtime call.

        For typed containers we dispatch to the type-specific runtime
        helper (``py_list_len`` etc.); otherwise we go through the
        generic ``py_obj_len``.
        """
        if len(expr.args) != 1:
            raise L1CodegenError(f"len() takes exactly 1 arg, got {len(expr.args)}")
        arg = expr.args[0]
        # Class-based ``__len__`` fast path.
        dunder = self._try_dispatch_dunder_unary(arg, "__len__", ())
        if dunder is not None:
            return dunder
        obj = self._emit_expr(arg)
        # CPython-backed value: dispatch through py_cpy_len (PyObject_Length).
        if obj in getattr(self, "_cpy_values", ()):
            return self.builder.call(
                self.runtime["py_cpy_len"], [obj],
                name=self._fresh("cpy.len"),
            )
        aty = arg.ty
        if isinstance(aty, ListType):
            return self.builder.call(
                self.runtime["py_list_len"], [obj], name=self._fresh("list.len")
            )
        if isinstance(aty, StrType):
            return self.builder.call(
                self.runtime["py_str_len"], [obj], name=self._fresh("str.len")
            )
        if isinstance(aty, DictType):
            return self.builder.call(
                self.runtime["py_dict_len"], [obj], name=self._fresh("dict.len")
            )
        if isinstance(aty, TupleType):
            return self.builder.call(
                self.runtime["py_tuple_len"], [obj], name=self._fresh("tup.len")
            )
        # Fallback through the generic helper. Any object with a
        # __len__ gets the right answer; non-sized types raise via the
        # runtime.
        boxed = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, obj, aty
        )
        return self.builder.call(
            self.runtime["py_obj_len"], [boxed], name=self._fresh("obj.len")
        )

    def _emit_str_builtin(self, expr: Call) -> ir.Value:
        """``str(x)`` → ``py_obj_str``; pass-through on already-str."""
        if len(expr.args) != 1:
            raise NotImplementedError("str() with multi-arg not supported")
        arg = expr.args[0]
        v = self._emit_expr(arg)
        if isinstance(arg.ty, StrType):
            return v
        boxed = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, v, arg.ty
        )
        return self.builder.call(
            self.runtime["py_obj_str"], [boxed], name=self._fresh("obj.str")
        )

    def _find_user_funcdef(self, name: str) -> FuncDef:
        for stmt in self.ast_module.body:
            if isinstance(stmt, FuncDef) and stmt.name == name:
                return stmt
        # Cross-module: name was imported from a native sibling via
        # ``from .other import name`` during multi-file compile.
        cm = getattr(self, "_cross_module_func_defs", {})
        if name in cm and cm[name] is not None:
            return cm[name]
        raise L1CodegenError(f"no FuncDef for user function {name!r}")

    def _resolve_call_kwargs(
        self,
        positional: tuple,
        kwargs_pairs: tuple,
        formal_args: tuple,
        skip_self: bool = False,
    ) -> list:
        """Reorder positional + keyword call args to match formals.

        Returns an Expr list in formal-parameter order. Missing slots
        are filled from ``Arg.default``; an unbound slot without a
        default raises L1CodegenError, as do duplicate binds, unknown
        keywords, and surplus positionals.
        """
        formals = list(formal_args)
        if skip_self and formals and formals[0].name == "self":
            formals = formals[1:]
        # Filter out the bare ``*`` separator — a kw_only marker with
        # an empty name has no runtime param. ``*args`` / ``**kwargs``
        # with real names are separate kinds (rejected upstream in
        # _emit_user_function / _declare_user_function).
        formals = [f for f in formals if f.name != ""]
        n_formal = len(formals)
        resolved: list = [None] * n_formal

        if len(positional) > n_formal:
            raise L1CodegenError(
                f"too many positional args: got {len(positional)}, "
                f"expected at most {n_formal}"
            )
        for i, e in enumerate(positional):
            resolved[i] = e

        name_to_idx = {f.name: i for i, f in enumerate(formals)}
        for kw_name, kw_expr in kwargs_pairs:
            idx = name_to_idx.get(kw_name)
            if idx is None:
                raise L1CodegenError(
                    f"unexpected keyword argument {kw_name!r}"
                )
            if resolved[idx] is not None:
                raise L1CodegenError(
                    f"duplicate value for argument {kw_name!r}"
                )
            resolved[idx] = kw_expr

        for i, formal in enumerate(formals):
            if resolved[i] is None:
                if formal.default is None:
                    raise L1CodegenError(
                        f"missing required argument {formal.name!r}"
                    )
                resolved[i] = formal.default
        return resolved

    # -- Coercions / helpers ------------------------------------------

    def _to_int64(self, v: ir.Value, ty: Type) -> ir.Value:
        if isinstance(ty, IntType):
            if v.type is _I64:
                return v
            if isinstance(v.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v, ty
                )
            # Should not happen in L1 (always i64), but guard anyway.
            return self.builder.sext(v, _I64, name=self._fresh("sext"))
        if isinstance(ty, BoolType):
            if v.type is _I1:
                return self.builder.zext(v, _I64, name=self._fresh("b2i64"))
            if isinstance(v.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v,
                    IntType(name="int"),
                )
            return self.builder.zext(v, _I64, name=self._fresh("b2i64"))
        if isinstance(ty, FloatType):
            # Python semantic: ``int(3.7) == 3`` (truncate toward zero).
            return self.builder.fptosi(v, _I64, name=self._fresh("f2i"))
        if isinstance(ty, DynType):
            # Dynamic values: unbox via ``py_int_to_i64`` when we hold a
            # ``PyObject*``, or pass the native integer through if an
            # earlier coercion already produced one (common for chained
            # binops where the inner result is already ``i64``).
            if isinstance(v.type, ir.PointerType):
                # CPython-backed DynType values use a different unbox
                # path than pcc-native PyObject*.
                if v in getattr(self, "_cpy_values", ()):
                    return self.builder.call(
                        self.runtime["py_cpy_to_i64"], [v],
                        name=self._fresh("cpy.to_i64"),
                    )
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v,
                    IntType(name="int"),
                )
            if isinstance(v.type, ir.IntType):
                if v.type.width == 64:
                    return v
                if v.type.width == 1:
                    return self.builder.zext(v, _I64, name=self._fresh("b2i64"))
                return self.builder.sext(v, _I64, name=self._fresh("sext"))
            if isinstance(v.type, (ir.FloatType, ir.DoubleType)):
                return self.builder.fptosi(v, _I64, name=self._fresh("f2i"))
        raise NotImplementedError(
            f"Layer 1 cannot coerce {type(ty).__name__} to int"
        )

    def _to_double(self, v: ir.Value, ty: Type) -> ir.Value:
        if isinstance(ty, FloatType):
            return v
        if isinstance(ty, IntType):
            if isinstance(v.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v,
                    FloatType(name="float"),
                )
            return self.builder.sitofp(v, _DOUBLE, name=self._fresh("i2f"))
        if isinstance(ty, BoolType):
            if v.type is _I1:
                return self.builder.uitofp(v, _DOUBLE, name=self._fresh("b2f"))
            if isinstance(v.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v,
                    FloatType(name="float"),
                )
            return self.builder.uitofp(v, _DOUBLE, name=self._fresh("b2f"))
        if isinstance(ty, DynType) and isinstance(v.type, ir.PointerType):
            if v in getattr(self, "_cpy_values", ()):
                return self.builder.call(
                    self.runtime["py_cpy_to_f64"], [v],
                    name=self._fresh("cpy.to_f64"),
                )
            return marshal.marshal_from_object(
                self.builder, self.module, self.runtime, v,
                FloatType(name="float"),
            )
        raise NotImplementedError(
            f"Layer 1 cannot coerce {type(ty).__name__} to float"
        )

    def _truthy(self, v: ir.Value, ty: Type) -> ir.Value:
        if isinstance(ty, BoolType):
            if v.type is _I1:
                return v
            if isinstance(v.type, ir.PointerType):
                i32 = self.builder.call(
                    self.runtime["py_obj_truthy"], [v],
                    name=self._fresh("truthy_obj"),
                )
                return self.builder.trunc(i32, _I1,
                                            name=self._fresh("truthy_obj_i1"))
            return self.builder.icmp_signed("!=", v, ir.Constant(v.type, 0),
                                              name=self._fresh("truthy_int"))
        if isinstance(ty, IntType):
            if isinstance(v.type, ir.PointerType):
                i64 = marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v, ty
                )
                zero = ir.Constant(_I64, 0)
                return self.builder.icmp_signed("!=", i64, zero,
                                                  name=self._fresh("truthy_i"))
            zero = ir.Constant(_I64, 0)
            return self.builder.icmp_signed("!=", v, zero,
                                              name=self._fresh("truthy_i"))
        if isinstance(ty, FloatType):
            zero = ir.Constant(_DOUBLE, 0.0)
            return self.builder.fcmp_ordered("!=", v, zero,
                                               name=self._fresh("truthy_f"))
        if self._is_object(ty) or isinstance(ty, DynType):
            # CPython-backed values must go through py_cpy_truthy
            # (PyObject_IsTrue) — the pcc py_obj_truthy only knows
            # about pcc's own PyObject layout.
            if v in getattr(self, "_cpy_values", ()):
                i32 = self.builder.call(
                    self.runtime["py_cpy_truthy"], [v],
                    name=self._fresh("cpy.truthy"),
                )
                return self.builder.trunc(i32, _I1,
                                            name=self._fresh("cpy.truthy.i1"))
            # Any object: route through py_obj_truthy, which honours
            # container emptiness, None == False, etc.
            obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, ty
            )
            i32 = self.builder.call(
                self.runtime["py_obj_truthy"], [obj],
                name=self._fresh("truthy_obj"),
            )
            return self.builder.trunc(i32, _I1,
                                        name=self._fresh("truthy_obj_i1"))
        raise NotImplementedError(
            f"Layer 1 cannot compute truthiness of {type(ty).__name__}"
        )

    def _coerce(self, v: ir.Value, from_ty: Type, to_ty: Type) -> ir.Value:
        """Coerce ``v`` (typed ``from_ty``) to ``to_ty``.

        Covers the L1 scalar matrix plus the L2 object-pass-through and
        native-↔-object marshalling cases.
        """
        if from_ty is None or to_ty is None:
            return v
        if type(from_ty) is type(to_ty):
            # Same pcc_py type class. IR-level representations are
            # already identical in L1/L2.
            return v
        # Native -> object marshal.
        if self._is_object(to_ty) and self._is_native_scalar_type(from_ty):
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, from_ty
            )
        # Object -> native unbox.
        if self._is_native_scalar_type(to_ty) and self._is_object(from_ty):
            # A ``DynType`` value may already carry a native scalar at
            # the IR level (e.g. a BinOp that unboxed its operands);
            # only go through the runtime if we actually hold a
            # ``PyObject*``.
            if isinstance(v.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v, to_ty
                )
            if isinstance(to_ty, IntType):
                return self._to_int64(v, from_ty)
            if isinstance(to_ty, BoolType):
                return self._truthy(v, from_ty)
            if isinstance(to_ty, FloatType):
                return self._to_double(v, from_ty)
            return v
        # Object -> object (e.g. list -> dyn): ptr pass-through.
        if self._is_object(to_ty) and self._is_object(from_ty):
            return v
        if isinstance(to_ty, FloatType):
            return self._to_double(v, from_ty)
        if isinstance(to_ty, IntType):
            return self._to_int64(v, from_ty)
        if isinstance(to_ty, BoolType):
            return self._truthy(v, from_ty)
        if isinstance(to_ty, NoneType):
            # Caller is expected to discard; leave value intact.
            return v
        if isinstance(to_ty, DynType):
            # Dyn accepts anything; upcast scalars to PyObject* so the
            # generic runtime helpers can handle them uniformly.
            if self._is_native_scalar_type(from_ty):
                return marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, v, from_ty
                )
            return v
        raise NotImplementedError(
            f"Layer 1/2 cannot coerce {type(from_ty).__name__} -> "
            f"{type(to_ty).__name__}"
        )

    def _is_native_scalar_type(self, ty: Type) -> bool:
        return isinstance(ty, (IntType, FloatType, BoolType))

    def _coerce_from_object(self, pyobj: ir.Value, target_ty: Type) -> ir.Value:
        """Unwrap ``pyobj`` into the representation of ``target_ty``.

        Object-typed targets stay as PyObject*; native targets go
        through :func:`marshal.marshal_from_object`.
        """
        if self._is_object(target_ty) or isinstance(target_ty, DynType):
            return pyobj
        if self._is_native_scalar_type(target_ty):
            return marshal.marshal_from_object(
                self.builder, self.module, self.runtime, pyobj, target_ty
            )
        # Unknown target — return the boxed form untouched.
        return pyobj


__all__ = ["L1CodeGen", "L1CodegenError"]
