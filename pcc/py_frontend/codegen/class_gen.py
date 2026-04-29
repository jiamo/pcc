"""Phase 3 class + method + super() lowering for pcc_py.

This module lives next to :mod:`layer1` and :mod:`layer2` and is invoked
by the top-level :class:`L1CodeGen` when it encounters a
:class:`~pcc.py_frontend.py_ast.ClassDef` at module scope.

Codegen strategy (Phase 3 scope — see python-frontend-plan.md §3):

* Each ClassDef is lowered to:
    1. A **global variable** that holds a ``PyClassObject*``. Name:
       ``.class.<module>.<name>``. Initialized to NULL; populated by the
       module-init function.
    2. A set of LLVM **method functions** — one per ``def`` in the class
       body. Signature: ``PyObject* user_<module>_<Class>_<method>(
         PyObject* self, <unboxed args...>)``.
    3. Contributions to the **module-init function** that:
       - Collect base class pointers from their own globals
       - Collect field names (a global const-char-pointer array)
       - Call ``py_class_new`` with the above plus ``name``
       - Call ``py_class_add_method`` for each method
       - Store the result in the class global.

* ``self.field`` reads:
    - If the field name is declared on the class, we emit
      ``py_instance_get_field(self, <idx>)``.
    - Otherwise fall back to ``py_obj_getattr``.

* ``self.field = value`` stores similarly via
  ``py_instance_set_field`` or ``py_obj_setattr``.

* ``super().method(args)`` lowers to
  ``py_super_lookup(cls_global, <enclosing_class>, "method")`` —
  currently we materialise the method PyObject* and then dispatch via
  a generic call; for Phase 3 single-dispatch single-class hierarchies
  this path is exercised but not fully tested end-to-end. Calling a
  super-resolved method is done through ``py_obj_call`` after wrapping
  self+args into a tuple.

* ``isinstance(x, Cls)`` uses ``py_isinstance`` on the class global.

* ``MyClass(args)`` is lowered by the caller in :mod:`layer1` via the
  Call-handler, which delegates here for construction.

The module exports a single entry point :class:`ClassLowering` that the
L1 codegen instantiates and calls into. Construction is stateful
because it needs access to the enclosing :class:`L1CodeGen`'s builder
and module.
"""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..export_meta import decode_type
from ..py_ast import (
    Arg,
    Assign,
    Attr,
    BoolLit,
    BoolType,
    Call,
    ClassDef,
    DynType,
    Expr,
    FloatLit,
    FloatType,
    FuncDef,
    IntLit,
    IntType,
    Module as AstModule,
    Name,
    NoneLit,
    NoneType,
    Pass,
    Return,
    SourceSpan,
    StrLit,
    StrType,
    Type,
)
from . import marshal
from .runtime_abi import declare_runtime_global


_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_VOID = ir.VoidType()
_CSTR = _I8.as_pointer()   # i8*
_PTR = _I8.as_pointer()    # also i8* (opaque)


class ClassLoweringError(Exception):
    """Raised when a ClassDef shape is malformed or unsupported."""


class ClassInfo:
    """Per-class metadata gathered at declaration time.

    Attributes:
        name: Simple class name (no module prefix).
        global_var: ``i8*`` global variable holding the runtime
            PyClassObject* pointer. Initialised to NULL at emit-time.
        bases_ast: Base-class AST :class:`Name` nodes. Phase 3 only
            supports base names that resolve to ``ClassInfo`` entries in
            the enclosing module.
        field_names: Ordered list of declared instance-field names. The
            field index exposed to ``py_instance_get_field`` equals the
            index into this list.
        methods: Mapping ``method_name -> ir.Function``. Method functions
            are declared here but their bodies are emitted by the parent
            :class:`L1CodeGen` via its normal FuncDef path.
    """

    def __init__(self, name: str, global_var: ir.GlobalVariable,
                 bases_ast: tuple[Expr, ...]):
        self.name = name
        self.global_var = global_var
        self.bases_ast = bases_ast
        self.field_names: list[str] = []
        self.class_attrs: dict[str, tuple[ir.GlobalVariable, Type]] = {}
        self.class_attr_values: dict[str, Expr] = {}
        self.methods: dict[str, ir.Function] = {}
        # Method kind map: 'instance' (default), 'static', 'classmethod',
        # 'property_getter'. Drives argument marshalling and call-site
        # dispatch. Populated by :meth:`_declare_method`.
        self.method_kinds: dict[str, str] = {}
        # For @property methods, track the getter function separately
        # from the stored-name slot so attribute access fires the
        # getter rather than a field lookup.
        self.properties: dict[str, ir.Function] = {}
        # Matching @<name>.setter functions so ``obj.<name> = value``
        # dispatches to the right function.
        self.property_setters: dict[str, ir.Function] = {}
        # The (possibly @dataclass-expanded) ClassDef AST — needed so
        # call-site lookups (e.g. ``_find_method_def``) see synthetic
        # methods, not just what the user wrote.
        self.expanded_cd: "ClassDef | None" = None
        # Cross-module extern classes synthesize FuncDef stubs from export
        # metadata so kwargs/default resolution can use normal method
        # lowering. Keep this as a declared pcc field; dynamic post-init
        # attributes are not reliable in the native object layout.
        self.extern_method_defs: dict[str, FuncDef] = {}


class ClassLowering:
    """Codegen helper bound to an :class:`L1CodeGen` instance.

    The parent codegen creates one :class:`ClassLowering` per module
    (shared across the two compile passes), then calls
    :meth:`declare_class`, :meth:`emit_methods`, and finally
    :meth:`emit_module_init` in sequence.
    """

    def __init__(self, parent: L1CodeGen):
        # parent: L1CodeGen — avoid importing to sidestep a circular
        # dependency. We only use .module, .ast_module, .runtime,
        # .functions, ._user_symbol, etc.
        self.parent = parent
        # class_name -> ClassInfo
        self.classes: dict[str, ClassInfo] = {}
        # Counter for unique global names.
        self._uniq = 0
        # Pool of interned const-char*[] globals for field-name arrays.
        self._field_arr_pool: dict[tuple[str, ...], ir.GlobalVariable] = {}
        # Pool of interned const-char* globals for class/attr names.
        self._cname_pool: dict[str, ir.GlobalVariable] = {}
        # Pool for bases pointer arrays keyed by the tuple of global
        # variable names.
        self._base_arr_pool: dict[tuple[str, ...], ir.GlobalVariable] = {}

    # ------------------------------------------------------ declaration

    def _fresh(self, hint: str) -> str:
        self._uniq += 1
        return f"{hint}.{self._uniq}"

    def declare_class(self, cd: ClassDef) -> ClassInfo:
        """First-pass: register the class and declare all its methods.

        Populates ``self.classes[cd.name]`` and declares the module-level
        global + each method function. Returns the :class:`ClassInfo`.
        """
        if cd.name in self.classes:
            raise ClassLoweringError(
                f"duplicate class definition for {cd.name!r}"
            )
        # ``@dataclass`` is supported by synthesizing ``__init__`` /
        # ``__repr__`` / ``__eq__`` into the class body. Other class
        # decorators remain unsupported for now.
        original_cd = cd
        cd = self._maybe_expand_dataclass(cd)
        expanded = cd is not original_cd
        if cd.decorators:
            raise NotImplementedError(
                f"Layer 1 does not handle class decorators on {cd.name!r}"
            )
        if cd.keywords and any(k != "metaclass" for k, _ in cd.keywords):
            raise NotImplementedError(
                f"Layer 1 does not handle class keyword arguments on {cd.name!r} "
                "(metaclass / kw-based class bases are out of scope)"
            )

        module = self.parent.module
        g_name = self._class_global_name(cd.name)
        existing = module.globals.get(g_name)
        if isinstance(existing, ir.GlobalVariable):
            raise ClassLoweringError(
                f"class global {g_name!r} already exists — duplicate class?"
            )
        gv = ir.GlobalVariable(module, _PTR, name=g_name)
        # In multi-file compile mode, other modules may reference this
        # class via ``declare_extern_class`` — leave linkage as the
        # default (external) so the linker can resolve the reference.
        # Use ``internal`` only when this module is compiled solo.
        if getattr(self.parent, "_native_module_exports", None) is None:
            gv.linkage = "internal"
        gv.initializer = ir.Constant(_PTR, None)

        info = ClassInfo(name=cd.name, global_var=gv, bases_ast=cd.bases)
        if expanded:
            info.expanded_cd = cd
        self.classes[cd.name] = info

        # Seed field_names with the parents' declared fields so that
        # inherited ``self.<field>`` accesses use the same slot index
        # the parent class lowered them into. Without this, a child
        # that calls ``super().__init__()`` writes into a slot the
        # parent never allocated, or vice versa.
        for base_expr in cd.bases:
            if not isinstance(base_expr, Name) or base_expr.ident == "object":
                continue
            parent_info = self.classes.get(base_expr.ident)
            if parent_info is None:
                continue
            for pf in parent_info.field_names:
                if pf not in info.field_names:
                    info.field_names.append(pf)

        # Walk the class body: collect fields from __init__ self-writes
        # and class-level annotations, and declare method functions.
        self._collect_fields_and_declare_methods(cd, info)
        return info

    # ------------------------------------------------------ @dataclass

    def _maybe_expand_dataclass(self, cd: ClassDef) -> ClassDef:
        """If ``cd`` carries a ``@dataclass`` decorator, synthesize
        ``__init__`` (and keep the other runtime-expected methods as
        TODO) and return a rewritten ClassDef with the decorator
        stripped. Otherwise return ``cd`` unchanged."""
        if not _class_has_dataclass_decorator(cd):
            return cd

        # Inherited @dataclass fields: walk each base that's itself an
        # expanded @dataclass in this module and prepend its fields
        # (fields declared on the base come FIRST in the synthetic
        # __init__, matching CPython's dataclass inheritance MRO).
        inherited_fields: list[tuple[str, Optional[Type], Optional[Expr]]] = []
        for base_expr in cd.bases:
            if not isinstance(base_expr, Name):
                continue
            base_info = self.classes.get(base_expr.ident)
            if base_info is None or base_info.expanded_cd is None:
                continue
            for s in base_info.expanded_cd.body:
                if (
                    isinstance(s, FuncDef)
                    and s.name == "__init__"
                ):
                    for a in s.args:
                        if a.name in ("", "self"):
                            continue
                        inherited_fields.append(
                            (a.name, a.annotation, a.default)
                        )
                    break

        # Collect ``name: annotation [= default]`` class-body entries.
        fields: list[tuple[str, Optional[Type], Optional[Expr]]] = list(
            inherited_fields
        )
        remaining_body: list = []
        for stmt in cd.body:
            if (
                isinstance(stmt, Assign)
                and stmt.annotation is not None
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], Name)
            ):
                name = stmt.targets[0].ident
                ann = stmt.annotation
                default = stmt.value
                # pcc's parser lowers a bare ``x: int`` annotation to
                # ``Assign(targets=(Name,), value=NoneLit, annotation=ann)``.
                # It also lowers ``x: int = None`` the same way, so
                # the two cases are indistinguishable at AST level.
                # Pre-2026-04-22 pcc treated all NoneLit-valued
                # annotations as "no default", which made
                # ``MemoryAccess.__init__(self, kind, id, block,
                # pointer=None, ...)`` fail ``missing required
                # argument 'pointer'`` for any caller that relied on
                # the Optional default. Keep the NoneLit so the
                # dataclass-generated ``__init__`` has the default,
                # matching the more-permissive interpretation.
                fields.append((name, ann, default))
                continue
            remaining_body.append(stmt)

        if not fields:
            # Decorator present but no fields — still a valid class,
            # just strip the decorator and return.
            return ClassDef(
                span=cd.span,
                name=cd.name, bases=cd.bases, keywords=cd.keywords,
                decorators=(),
                body=cd.body,
            )

        # Build synthetic __init__(self, <fields>): self.<f> = <f>.
        span = cd.span
        init_args: list = [
            Arg(
                name="self",
                annotation=None,
                default=None,
                kind="pos",
                has_default=False,
            )
        ]
        init_stmts: list = []
        for fname, ann, default in fields:
            init_args.append(
                Arg(
                    name=fname,
                    annotation=ann,
                    default=default,
                    kind="pos",
                    has_default=default is not None,
                )
            )
            init_stmts.append(Assign(
                span=span,
                targets=(Attr(
                    span=span, ty=DynType(name="dyn"),
                    obj=Name(span=span, ty=DynType(name="dyn"), ident="self"),
                    name=fname,
                ),),
                value=Name(span=span, ty=(ann or DynType(name="dyn")), ident=fname),
                annotation=None,
            ))
        if not init_stmts:
            init_stmts = [Pass(span=span)]

        synthetic_init = FuncDef(
            span=span,
            name="__init__",
            args=tuple(init_args),
            return_ty=NoneType(name="None"),
            body=tuple(init_stmts),
            decorators=(),
            is_method=True,
            is_async=False,
        )

        # Only add a synthetic __init__ if the user didn't define one
        # themselves.
        user_has_init = any(
            isinstance(s, FuncDef) and s.name == "__init__"
            for s in remaining_body
        )
        new_body = list(remaining_body)
        if not user_has_init:
            new_body.insert(0, synthetic_init)

        return ClassDef(
            span=cd.span,
            name=cd.name, bases=cd.bases, keywords=cd.keywords,
            decorators=(),
            body=tuple(new_body),
        )

    def _class_global_name(self, cname: str) -> str:
        mod = self.parent.ast_module.name or "mod"
        sanitised_mod = mod.replace(".", "_").replace("-", "_")
        return f".class.{sanitised_mod}.{cname}"

    def _method_symbol(self, class_name: str, method_name: str) -> str:
        mod = self.parent.ast_module.name or "mod"
        sanitised_mod = mod.replace(".", "_").replace("-", "_")
        return f"user_{sanitised_mod}_{class_name}_{method_name}"

    def _collect_fields_and_declare_methods(
        self, cd: ClassDef, info: ClassInfo
    ) -> None:
        """Scan the class body to find declared fields and methods."""
        for stmt in cd.body:
            if isinstance(stmt, Pass):
                continue
            if isinstance(stmt, Assign):
                # Class-level assignments are class attributes. Instance
                # fields come from dataclass expansion or ``self.x`` writes.
                for t in stmt.targets:
                    if isinstance(t, Name):
                        self._declare_class_attr(info, t.ident, stmt.value)
                continue
            if isinstance(stmt, FuncDef):
                self._declare_method(cd, stmt, info)
                # Scan direct ``self.field = ...`` assignments in all
                # methods. Python permits fields to first appear outside
                # ``__init__`` (for example iterator state in __iter__).
                for s in stmt.body:
                    if isinstance(s, Assign):
                        for target in s.targets:
                            if isinstance(target, Attr) \
                               and isinstance(target.obj, Name) \
                               and target.obj.ident == "self":
                                field_name = _mangle_private_name(
                                    cd.name, target.name,
                                )
                                if field_name not in info.field_names:
                                    info.field_names.append(field_name)
                continue
            # Ignore anything else (nested classes etc.) until a later
            # phase picks them up.
            # Docstrings at the top of the body are expression statements
            # with a StrLit — fine to drop silently.

    def _declare_method(
        self, cd: ClassDef, fd: FuncDef, info: ClassInfo
    ) -> None:
        """Declare one method function. Body is emitted separately."""
        if fd.is_async:
            raise NotImplementedError(
                f"Layer 1 does not handle 'async def' method "
                f"{cd.name}.{fd.name}"
            )
        kind = "instance"
        for dec in fd.decorators:
            dname = _simple_decorator_name(dec)
            if dname == "staticmethod":
                kind = "static"
            elif dname == "classmethod":
                kind = "classmethod"
            elif dname == "property":
                kind = "property_getter"
            elif dname is not None and dname.endswith(".setter"):
                # ``@<name>.setter`` — Phase-3 follow-up: pair with the
                # existing property entry.
                kind = "property_setter"
            elif dname in (
                "abstractmethod", "abc.abstractmethod",
                "abstractclassmethod", "abc.abstractclassmethod",
                "abstractstaticmethod", "abc.abstractstaticmethod",
                "abstractproperty", "abc.abstractproperty",
            ):
                # pcc doesn't enforce abstractness at compile time;
                # treat as a no-op decorator so the method is declared
                # as a regular instance method and the class lowering
                # proceeds. Runtime instantiation of a class with an
                # unimplemented abstract method will happily run the
                # (usually ``raise NotImplementedError``) body — pcc
                # matches Python's non-strict behaviour here.
                continue
            elif (
                isinstance(dec, Call)
                and _simple_decorator_name(dec.func) in ("TOKEN", "lex.TOKEN")
            ):
                # PLY regex decorators only annotate token regexes for
                # runtime reflection. They do not change the method's
                # call shape, so a compile-time no-op is sufficient.
                continue
            else:
                raise NotImplementedError(
                    f"Layer 1 does not handle decorator {dname!r} on method "
                    f"{cd.name}.{fd.name}"
                )
        # For @<name>.setter we keep the getter's "property_getter"
        # entry intact — the setter is looked up separately by scanning
        # the AST decorators at emit time.
        if kind != "property_setter":
            info.method_kinds[fd.name] = kind

        box_int_abi = self.parent._should_box_python_ints()
        if kind == "static":
            # No receiver prepended. All params are declared-only.
            decl_args = fd.args
        elif not fd.args:
            raise NotImplementedError(
                f"method {cd.name}.{fd.name} must have at least one argument "
                "(the 'self' receiver)"
            )
        else:
            decl_args = fd.args

        # Build LLVM signature: static methods omit the receiver, all
        # other kinds carry an i8* (PyInstance / PyClass pointer) as the
        # first param. Subsequent params go through the parent's type
        # logic.
        if kind == "static":
            param_types: list[ir.Type] = []
            iter_args = decl_args
        else:
            param_types = [_PTR]
            iter_args = decl_args[1:]
        for arg in iter_args:
            # Bare ``*`` separator: no runtime slot — matches the
            # top-level ``_declare_user_function`` filter so the
            # generated method signature matches what the caller +
            # ``_resolve_call_kwargs`` produce.
            if arg.name == "":
                continue
            ir_ty, _ = self.parent._param_ir_and_bind_type(
                arg, require_annotation=False,
                owner_name=f"{cd.name}.{fd.name}",
                box_int_params=box_int_abi,
            )
            param_types.append(ir_ty)

        if fd.return_ty is None or isinstance(fd.return_ty, NoneType):
            ret_ty = _VOID
        elif box_int_abi and isinstance(fd.return_ty, IntType):
            ret_ty = _PTR
        else:
            ret_ty = self.parent._map_type(fd.return_ty)

        fnty = ir.FunctionType(ret_ty, param_types, var_arg=False)
        sym = self._method_symbol(cd.name, fd.name)
        if kind == "property_setter":
            sym = sym + ".setter"
        existing = self.parent.module.globals.get(sym)
        if isinstance(existing, ir.Function):
            fn = existing
        else:
            fn = ir.Function(self.parent.module, fnty, name=sym)
            fn.linkage = "external"
        runtime_decl_args = [a for a in decl_args if a.name != ""]
        if kind == "static":
            for ir_arg, ast_arg in zip(fn.args, runtime_decl_args):
                ir_arg.name = ast_arg.name
        else:
            fn.args[0].name = runtime_decl_args[0].name  # "self" or "cls"
            for ir_arg, ast_arg in zip(fn.args[1:], runtime_decl_args[1:]):
                ir_arg.name = ast_arg.name
        # Methods named via ``@<name>.setter`` re-declare a previously
        # emitted FuncDef — their ``fd.name`` is the property name, so
        # we register them as setters rather than overwriting the
        # getter in ``info.methods``.
        if kind == "property_setter":
            info.property_setters[fd.name] = fn
        else:
            info.methods[fd.name] = fn
            if kind == "property_getter":
                info.properties[fd.name] = fn

    def _class_attr_global_name(self, class_name: str, attr_name: str) -> str:
        mod = self.parent.ast_module.name or "mod"
        sanitised_mod = mod.replace(".", "_").replace("-", "_")
        return f".classattr.{sanitised_mod}.{class_name}.{attr_name}"

    def _declare_class_attr(
        self, info: ClassInfo, attr_name: str, value_expr: Expr,
    ) -> None:
        if attr_name in info.class_attrs:
            info.class_attr_values[attr_name] = value_expr
            return
        g_name = self._class_attr_global_name(info.name, attr_name)
        existing = self.parent.module.globals.get(g_name)
        if isinstance(existing, ir.GlobalVariable):
            gv = existing
        else:
            gv = ir.GlobalVariable(self.parent.module, _PTR, name=g_name)
            gv.linkage = "internal"
            gv.initializer = ir.Constant(_PTR, None)
        info.class_attrs[attr_name] = (gv, value_expr.ty)
        info.class_attr_values[attr_name] = value_expr

    # ------------------------------------------------------ extern class

    def declare_extern_class(
        self,
        owning_module: str,
        class_name: str,
        field_names: tuple,
        methods: tuple,
        local_name: str = None,
    ) -> ClassInfo:
        """Declare a class imported from a sibling multi-file module.

        Creates an ``external`` class global and declares each method
        function with a matching signature. Registers the resulting
        :class:`ClassInfo` under ``local_name`` (or ``class_name``)
        in ``self.classes`` so ``MyClass(args)`` / ``obj.method()``
        dispatch through the normal pcc-native path.

        ``methods`` is a tuple of dicts with keys ``name``, ``kind``,
        ``param_types`` (tuple of ``Type`` including a leading
        placeholder for self/cls when kind != 'static'), ``return_ty``.
        """
        local = local_name or class_name
        if local in self.classes:
            # Already declared — return existing.
            return self.classes[local]

        mod_module = self.parent.module
        sanitised_mod = owning_module.replace(".", "_").replace("-", "_")
        g_name = f".class.{sanitised_mod}.{class_name}"
        existing_g = mod_module.globals.get(g_name)
        if isinstance(existing_g, ir.GlobalVariable):
            gv = existing_g
        else:
            gv = ir.GlobalVariable(mod_module, _PTR, name=g_name)
            gv.linkage = "external"
        info = ClassInfo(name=local, global_var=gv, bases_ast=())
        info.field_names = list(field_names)
        # Synthesise FuncDef-like stubs for each method so the class
        # instantiation / direct-method-call paths that grub for the
        # AST annotations via ``_find_method_def`` still work. We
        # only need ``args`` (with ``annotation`` set) and ``name``
        # / ``return_ty`` — body stays empty.
        from pcc.py_frontend.py_ast import (
            FuncDef as _FuncDef,
            Arg as _Arg,
            SourceSpan as _Span,
        )
        _span = _Span(
            file="<extern>", line=0, col=0, end_line=0, end_col=0,
        )
        synth_defs = {}
        for mdesc in methods:
            call_sig = mdesc.get("call_sig")
            synth_args = []
            if call_sig is not None:
                for arg in call_sig:
                    synth_args.append(_Arg(
                        name=arg["name"],
                        annotation=decode_type(arg.get("annotation")),
                        default=arg.get("default"),
                        kind=arg.get("kind", "pos"),
                        has_default=arg.get(
                            "has_default", arg.get("default") is not None,
                        ),
                    ))
            else:
                for i, ty in enumerate(mdesc["param_types"]):
                    synth_args.append(_Arg(
                        name=f"arg{i}" if i > 0 else "self",
                        annotation=decode_type(ty),
                        default=None,
                        kind="pos",
                        has_default=False,
                    ))
            synth_defs[mdesc["name"]] = _FuncDef(
                span=_span,
                name=mdesc["name"],
                args=tuple(synth_args),
                return_ty=decode_type(mdesc["return_ty"]),
                body=(),
                decorators=(),
            )
        info.extern_method_defs = synth_defs
        self.classes[local] = info

        for mdesc in methods:
            mname = mdesc["name"]
            kind = mdesc["kind"]
            info.method_kinds[mname] = kind
            box_int_abi = bool(
                mdesc.get(
                    "box_int_abi",
                    self.parent._should_box_python_ints(),
                )
            )
            if kind == "static":
                param_types = [
                    self.parent._abi_ir_type(
                        decode_type(t), box_int_abi=box_int_abi,
                    )
                    for t in mdesc["param_types"]
                ]
            else:
                param_types = [_PTR] + [
                    self.parent._abi_ir_type(
                        decode_type(t), box_int_abi=box_int_abi,
                    )
                    for t in mdesc["param_types"][1:]
                ]
            ret = decode_type(mdesc["return_ty"])
            ret_ir = (
                _VOID if ret is None
                else self.parent._abi_ir_type(ret, box_int_abi=box_int_abi)
            )
            fnty = ir.FunctionType(ret_ir, param_types, var_arg=False)
            sym = f"user_{sanitised_mod}_{class_name}_{mname}"
            existing = mod_module.globals.get(sym)
            if isinstance(existing, ir.Function):
                fn = existing
            else:
                fn = ir.Function(mod_module, fnty, name=sym)
                fn.linkage = "external"
            info.methods[mname] = fn
            if kind == "property_getter":
                info.properties[mname] = fn
        return info

    # ------------------------------------------------------ method bodies

    def emit_methods(self, cd: ClassDef) -> None:
        """Second-pass: emit each method's body."""
        info = self.classes[cd.name]
        # If we rewrote the class (e.g. @dataclass), iterate over the
        # expanded body so synthetic methods get their bodies emitted.
        if info.expanded_cd is not None:
            cd = info.expanded_cd
        for stmt in cd.body:
            if isinstance(stmt, FuncDef):
                if _funcdef_is_property_setter(stmt):
                    fn = info.property_setters.get(stmt.name)
                else:
                    fn = info.methods.get(stmt.name)
                if fn is None:
                    continue
                self._emit_method_body(cd, stmt, fn, info)

    def _emit_method_body(
        self, cd: ClassDef, fd: FuncDef,
        fn: ir.Function, info: ClassInfo,
    ) -> None:
        """Lower a method body. Reuses the parent codegen's statement
        machinery by temporarily rebinding its state.
        """
        parent = self.parent
        # Preserve outer state — we may be called mid-module-init
        # bookkeeping and we must not clobber a function-in-progress.
        saved_builder = parent.builder
        saved_fn = parent.current_function
        saved_fd = parent.current_func_def
        saved_env = parent.env
        saved_loops = parent.loop_stack
        saved_box_int_locals = parent._box_int_locals
        saved_exact_int_flags = parent._exact_int_env_flags
        saved_ir_builder_flags = getattr(parent, "_ir_builder_env_flags", {})
        saved_class = getattr(parent, "current_class", None)
        saved_global_names = getattr(parent, "_current_global_names", set())

        # Pick an entry-block name that doesn't collide with any
        # parameter. ``entry`` is the default, but methods like
        # ``__init__(self, entry, ...)`` would trip LLVM's label-vs-
        # SSA-name shared namespace.
        param_names = {a.name for a in fd.args if a.name != ""}
        entry_label = "entry"
        while entry_label in param_names:
            entry_label = entry_label + "_"
        entry = fn.append_basic_block(name=entry_label)
        parent.builder = ir.IRBuilder(entry)
        parent.current_function = fn
        parent.current_func_def = fd
        parent._current_global_names = parent._collect_explicit_global_names(
            fd.body
        )
        parent.env = {}
        parent.env_class_hint = {}
        parent._ir_builder_env_flags = {}
        parent.loop_stack = []
        box_int_abi = parent._should_box_python_ints()
        parent._box_int_locals = box_int_abi
        parent._exact_int_env_flags = {}
        parent.current_class = info  # type: ignore[attr-defined]
        saved_kind = getattr(parent, "current_method_kind", None)
        kind = info.method_kinds.get(fd.name, "instance")
        parent.current_method_kind = kind  # type: ignore[attr-defined]
        # Filter the bare ``*`` kw-only separator — it has no IR slot.
        runtime_args = [a for a in fd.args if a.name != ""]
        if kind == "static":
            # No implicit receiver. Walk declared args directly.
            for ir_arg, ast_arg in zip(fn.args, runtime_args):
                ir_ty = ir_arg.type
                slot = parent.builder.alloca(ir_ty, name=f"{ast_arg.name}.addr")
                parent.builder.store(ir_arg, slot)
                _decl_ir_ty, bind_ty = parent._param_ir_and_bind_type(
                    ast_arg, require_annotation=False,
                    owner_name=f"{cd.name}.{fd.name}",
                    box_int_params=box_int_abi,
                )
                parent.env[ast_arg.name] = (slot, ir_ty, bind_ty)
        else:
            # First argument is the receiver (``self`` or ``cls``).
            recv_name = runtime_args[0].name if runtime_args else "self"
            self_slot = parent.builder.alloca(_PTR, name=f"{recv_name}.addr")
            parent.builder.store(fn.args[0], self_slot)
            parent.env[recv_name] = (self_slot, _PTR, DynType(name="dyn"))

            for ir_arg, ast_arg in zip(fn.args[1:], runtime_args[1:]):
                ir_ty = ir_arg.type
                slot = parent.builder.alloca(ir_ty, name=f"{ast_arg.name}.addr")
                parent.builder.store(ir_arg, slot)
                _decl_ir_ty, bind_ty = parent._param_ir_and_bind_type(
                    ast_arg, require_annotation=False,
                    owner_name=f"{cd.name}.{fd.name}",
                    box_int_params=box_int_abi,
                )
                parent.env[ast_arg.name] = (slot, ir_ty, bind_ty)

        # Emit statements via the parent's normal emitter.
        parent._emit_stmts(fd.body)

        # Default-return for missing terminator.
        if not parent._builder_block_is_terminated():
            if isinstance(fn.function_type.return_type, ir.VoidType):
                parent.builder.ret_void()
            else:
                parent.builder.ret(
                    parent._zero_of(fn.function_type.return_type)
                )

        # Restore outer state.
        parent.builder = saved_builder
        parent.current_function = saved_fn
        parent.current_func_def = saved_fd
        parent._current_global_names = saved_global_names
        parent.env = saved_env
        parent.loop_stack = saved_loops
        parent._box_int_locals = saved_box_int_locals
        parent._exact_int_env_flags = saved_exact_int_flags
        parent._ir_builder_env_flags = saved_ir_builder_flags
        parent.current_class = saved_class  # type: ignore[attr-defined]
        parent.current_method_kind = saved_kind  # type: ignore[attr-defined]

    # ------------------------------------------------------ module init

    def emit_module_init(self) -> None:
        """Emit the one-shot ``_pcc_py_module_init`` function that
        populates every class global.

        The generated entrypoints call module-init explicitly in a
        deterministic order. Avoid also registering it as a global ctor:
        in multi-module executables ctor order is linker-defined, so a
        child class module can run before its base-class module and feed
        NULL bases into ``py_class_new`` / ``c3_linearize``.
        """
        if not self.classes:
            return  # nothing to do

        module = self.parent.module
        mod_name = self.parent.ast_module.name or "mod"
        sanitised_mod = mod_name.replace(".", "_").replace("-", "_")
        fn_name = f"_pcc_py_module_init_{sanitised_mod}"
        existing = module.globals.get(fn_name)
        if isinstance(existing, ir.Function):
            # Already emitted — leave it.
            return
        fnty = ir.FunctionType(_VOID, [], var_arg=False)
        init_fn = ir.Function(module, fnty, name=fn_name)
        init_fn.linkage = "external"

        # Save parent state so we can re-use its builder abstraction.
        saved_builder = self.parent.builder
        saved_fn = self.parent.current_function
        entry = init_fn.append_basic_block(name="entry")
        self.parent.builder = ir.IRBuilder(entry)
        self.parent.current_function = init_fn

        # Emit per-class init in declaration order.
        for cd in self._iter_class_defs():
            info = self.classes[cd.name]
            self._emit_class_init(cd, info)

        self.parent.builder.ret_void()
        self.parent.builder = saved_builder
        self.parent.current_function = saved_fn

    def _iter_class_defs(self):
        """Return every top-level ``ClassDef`` in the module body."""
        return [
            stmt for stmt in self.parent.ast_module.body
            if isinstance(stmt, ClassDef)
        ]

    def _emit_class_init(self, cd: ClassDef, info: ClassInfo) -> None:
        builder = self.parent.builder
        runtime = self.parent.runtime

        # 1. Class name C-string.
        name_ptr = self._cname_ptr(cd.name)

        # 2. Field names array — const char*[]. For n_fields == 0 pass NULL.
        n_fields = len(info.field_names)
        if n_fields == 0:
            field_arr = ir.Constant(_PTR, None)
        else:
            field_arr = self._field_names_global(tuple(info.field_names))

        # 3. Base classes array.
        base_values: list[ir.Value] = []
        for b in info.bases_ast:
            if not isinstance(b, Name):
                # Foreign / qualified bases such as
                # ``ctypes.Structure`` are left out of the native
                # base array. The derived class still compiles and can
                # use attribute fallback paths, but native layout/MRO
                # does not attempt to model the external base.
                continue
            if b.ident == "object":
                # Skip — implicit object root is added by py_class_new
                # when n_bases == 0. A mixed "explicit object + other
                # bases" case would add object twice; unsupported here.
                continue
            base_info = self.classes.get(b.ident)
            if base_info is not None:
                base_values.append(
                    builder.load(
                        base_info.global_var,
                        name=self._fresh(f".base.{b.ident}"),
                    )
                )
                continue
            exc_tag = getattr(self.parent, "_BUILTIN_EXC_TAG", {}).get(b.ident)
            if exc_tag is not None:
                base_values.append(
                    builder.call(
                        runtime["py_exc_builtin_class"],
                        [ir.Constant(_I64, exc_tag)],
                        name=self._fresh(f".base.exc.{b.ident}"),
                    )
                )
                continue
            if base_info is None:
                # Foreign base class (imported from CPython-backed module
                # such as ``llvmlite.ir.ModulePass``). We cannot model its
                # layout on the pcc side, so skip it: the derived class
                # stays structurally compatible with the pcc runtime while
                # method calls through ``self`` fall through to the
                # CPython dispatch path.
                continue

        n_bases = len(base_values)
        if n_bases == 0:
            bases_ptr = ir.Constant(_PTR, None)
        else:
            bases_ptr = self._load_bases_array(cd.name, base_values)

        # 4. py_class_new(name, bases_ptr, n_bases, field_arr, n_fields).
        # The runtime-ABI declaration uses the opaque ``i8*`` pointer for
        # both pointer-array arguments; coerce our typed pointers before
        # the call so llvmlite's type checker is happy.
        bases_arg = bases_ptr
        if bases_arg.type != _PTR:
            bases_arg = builder.bitcast(bases_arg, _PTR,
                                          name=self._fresh(".bases.i8p"))
        field_arg = field_arr
        if field_arg.type != _PTR:
            field_arg = builder.bitcast(field_arg, _PTR,
                                          name=self._fresh(".fnames.i8p"))
        cls_ptr = builder.call(
            runtime["py_class_new"],
            [
                name_ptr,
                bases_arg,
                ir.Constant(_I32, n_bases),
                field_arg,
                ir.Constant(_I32, n_fields),
            ],
            name=self._fresh(f"class.{cd.name}"),
        )

        # 5. py_class_add_method(cls, "method", func_as_PyObject_ptr) for each.
        for mname, mfunc in info.methods.items():
            mname_ptr = self._cname_ptr(mname)
            func_as_obj = builder.bitcast(mfunc, _PTR,
                                            name=self._fresh(f"m.{mname}"))
            builder.call(runtime["py_class_add_method"],
                           [cls_ptr, mname_ptr, func_as_obj])

        # 6. Initialize class-attribute storage.
        for attr_name, value_expr in info.class_attr_values.items():
            gv, _attr_ty = info.class_attrs[attr_name]
            raw = self.parent._emit_expr(value_expr)
            obj = marshal.marshal_to_object(
                builder, self.parent.module, runtime, raw, value_expr.ty,
            )
            builder.store(obj, gv)

        # 7. Store into the class global.
        builder.store(cls_ptr, info.global_var)

    # -- small-object globals ------------------------------------------

    def _cname_ptr(self, s: str) -> ir.Value:
        """Return an i8* pointing at a NUL-terminated UTF-8 C string.

        Interned in ``_cname_pool``.
        """
        existing = self._cname_pool.get(s)
        if existing is None:
            data = self.parent._utf8_byte_values(s) + [0]
            arr_ty = ir.ArrayType(_I8, len(data))
            base = self._fresh(f".class_name.{s}")
            # Make sure we don't collide.
            if base in self.parent.module.globals:
                base = self._fresh(".class_name")
            gv = ir.GlobalVariable(self.parent.module, arr_ty, name=base)
            gv.linkage = "internal"
            gv.global_constant = True
            gv.initializer = ir.Constant(arr_ty, data)
            self._cname_pool[s] = gv
            existing = gv
        zero = ir.Constant(_I32, 0)
        return self.parent.builder.gep(existing, [zero, zero], inbounds=True)

    def _field_names_global(self, names: tuple[str, ...]) -> ir.Value:
        """Return an i8** pointing at field-name C string pointers.

        ``py_class_new`` copies this pointer array immediately, so a
        module-init local array is enough. Building it with regular
        builder operations also avoids the non-scaffolded
        ``GlobalVariable.gep`` constant helper in the self-host path.
        """
        if not names:
            return ir.Constant(_PTR, None)
        arr_ty = ir.ArrayType(_PTR, len(names))
        slot = self.parent.builder.alloca(
            arr_ty, name=self._fresh(".field_names.local"),
        )
        zero = ir.Constant(_I32, 0)
        for i, s in enumerate(names):
            ptr = self.parent.builder.gep(
                slot, [zero, ir.Constant(_I32, i)], inbounds=True,
                name=self._fresh(".fname.slot"),
            )
            self.parent.builder.store(self._cname_ptr(s), ptr)
        return self.parent.builder.gep(
            slot, [zero, zero], inbounds=True, name=self._fresh(".fnames"),
        )

    def _global_cstring(self, s: str) -> ir.GlobalVariable:
        """Intern a ``const char[N]`` global (not just a pointer).

        Distinct from :meth:`_cname_ptr` in that it returns the *GV*
        rather than an i8* — suitable for building constant initializer
        arrays of pointers.
        """
        # Reuse the _cname_pool storage (they hold the same shape).
        existing = self._cname_pool.get(s)
        if existing is None:
            data = self.parent._utf8_byte_values(s) + [0]
            arr_ty = ir.ArrayType(_I8, len(data))
            base = self._fresh(f".cstr.{s}")
            if base in self.parent.module.globals:
                base = self._fresh(".cstr")
            gv = ir.GlobalVariable(self.parent.module, arr_ty, name=base)
            gv.linkage = "internal"
            gv.global_constant = True
            gv.initializer = ir.Constant(arr_ty, data)
            self._cname_pool[s] = gv
            existing = gv
        return existing

    def _load_bases_array(
        self, class_name: str, base_values: list[ir.Value]
    ) -> ir.Value:
        """Emit code that builds a transient ``i8**`` pointing at the
        base-class pointer values.
        """
        builder = self.parent.builder
        # Allocate a stack array of PyObject* pointers. We allocate on
        # stack because the array is only needed for the duration of
        # the py_class_new call.
        arr_ty = ir.ArrayType(_PTR, len(base_values))
        slot = builder.alloca(arr_ty, name=self._fresh(f".bases.{class_name}"))
        for i, base_val in enumerate(base_values):
            zero = ir.Constant(_I32, 0)
            idx = ir.Constant(_I32, i)
            elem_ptr = builder.gep(slot, [zero, idx], inbounds=True,
                                     name=self._fresh(f".baseslot.{i}"))
            stored = base_val
            if stored.type != _PTR:
                stored = builder.bitcast(
                    stored, _PTR, name=self._fresh(f".base.i8p.{i}")
                )
            builder.store(stored, elem_ptr)
        zero = ir.Constant(_I32, 0)
        return builder.gep(slot, [zero, zero], inbounds=True,
                             name=self._fresh(f".baseptr.{class_name}"))

    # ------------------------------------------------------ call/attr helpers

    def lookup_field_index(
        self, info: ClassInfo, name: str
    ) -> Optional[int]:
        """Return the slot index for ``name`` on the class, or None."""
        name = _mangle_private_name(info.name, name)
        if name in info.field_names:
            return info.field_names.index(name)
        # Phase 3: also check direct bases for inherited field slots so
        # a sub-class can see them as if they were its own. Classes
        # declared outside the enclosing module aren't visible here.
        for b in info.bases_ast:
            if isinstance(b, Name):
                base_info = self.classes.get(b.ident)
                if base_info is not None:
                    idx = self.lookup_field_index(base_info, name)
                    if idx is not None:
                        return idx
        return None

    def lookup_class_attr(
        self, info: ClassInfo, name: str,
    ) -> Optional[tuple[ir.GlobalVariable, Type]]:
        if name in info.class_attrs:
            return info.class_attrs[name]
        for b in info.bases_ast:
            if isinstance(b, Name):
                base_info = self.classes.get(b.ident)
                if base_info is not None:
                    found = self.lookup_class_attr(base_info, name)
                    if found is not None:
                        return found
        return None

    def emit_class_attr_load(
        self, info: ClassInfo, attr_name: str,
    ) -> Optional[ir.Value]:
        found = self.lookup_class_attr(info, attr_name)
        if found is None:
            return None
        gv, _ty = found
        return self.parent.builder.load(
            gv, name=self._fresh(f"classattr.{info.name}.{attr_name}"),
        )

    def emit_class_attr_store(
        self, info: ClassInfo, attr_name: str,
        value: ir.Value, value_ty: Type,
    ) -> bool:
        found = self.lookup_class_attr(info, attr_name)
        if found is None:
            return False
        gv, _ty = found
        obj = marshal.marshal_to_object(
            self.parent.builder, self.parent.module, self.parent.runtime,
            value, value_ty,
        )
        self.parent.builder.store(obj, gv)
        return True

    def class_global(self, class_name: str) -> Optional[ir.GlobalVariable]:
        info = self.classes.get(class_name)
        if info is None:
            return None
        return info.global_var

    # ------------------------------------------------------ self.attr emit

    def emit_self_attr_load(
        self, info: ClassInfo, attr_name: str, self_val: ir.Value
    ) -> ir.Value:
        """Emit a ``self.<attr>`` load inside a method body.

        If the attribute name matches a declared field we go through
        ``py_instance_get_field``. Otherwise fall back to
        ``py_obj_getattr``.
        """
        builder = self.parent.builder
        runtime = self.parent.runtime
        idx = self.lookup_field_index(info, attr_name)
        if idx is not None:
            return builder.call(
                runtime["py_instance_get_field"],
                [self_val, ir.Constant(_I32, idx)],
                name=self._fresh(f"self.{attr_name}"),
            )
        class_attr = self.emit_class_attr_load(info, attr_name)
        if class_attr is not None:
            return class_attr
        name_ptr = self._cname_ptr(attr_name)
        return builder.call(
            runtime["py_obj_getattr"], [self_val, name_ptr],
            name=self._fresh(f"self.attr.{attr_name}"),
        )

    def emit_self_attr_store(
        self, info: ClassInfo, attr_name: str,
        self_val: ir.Value, value: ir.Value,
    ) -> None:
        """Emit ``self.<attr> = value``."""
        builder = self.parent.builder
        runtime = self.parent.runtime
        idx = self.lookup_field_index(info, attr_name)
        if idx is not None:
            builder.call(
                runtime["py_instance_set_field"],
                [self_val, ir.Constant(_I32, idx), value],
            )
            return
        name_ptr = self._cname_ptr(attr_name)
        builder.call(
            runtime["py_obj_setattr"], [self_val, name_ptr, value]
        )

    # ------------------------------------------------------ isinstance

    def emit_isinstance(
        self, obj_val: ir.Value, class_name: str
    ) -> ir.Value:
        """Emit ``py_isinstance(obj, class_global)`` returning i1."""
        builder = self.parent.builder
        runtime = self.parent.runtime
        info = self.classes.get(class_name)
        if info is None:
            raise NotImplementedError(
                f"isinstance: class {class_name!r} not found in module"
            )
        cls_ptr = builder.load(info.global_var,
                                 name=self._fresh(f".cls.{class_name}"))
        res_i64 = builder.call(runtime["py_isinstance"],
                                 [obj_val, cls_ptr],
                                 name=self._fresh(f"isinstance.{class_name}"))
        return builder.icmp_signed("!=", res_i64, ir.Constant(_I64, 0),
                                     name=self._fresh("isinstance.i1"))

    # ------------------------------------------------------ super()

    def emit_super_lookup(
        self, enclosing: ClassInfo, self_val: ir.Value, method_name: str,
    ) -> ir.Value:
        """Emit ``py_super_lookup(start_cls, from_cls, method_name)``.

        ``from_cls`` is the enclosing class at codegen time.
        ``start_cls`` is the instance's actual class — we read it off
        the instance's header, which is at offset 0 of the instance's
        memory area, but for Phase 3 simplicity we take the slow path
        and use the enclosing class's global as start_cls too. This is
        only correct when ``self`` is actually an instance of the
        enclosing class (and not a subclass that overrides); a future
        phase should emit a load of ``inst->cls``.
        """
        builder = self.parent.builder
        runtime = self.parent.runtime
        cls_name_ptr = self._cname_ptr("__class__")
        start_cls = builder.call(
            runtime["py_obj_getattr"], [self_val, cls_name_ptr],
            name=self._fresh(".super.start"),
        )
        from_cls = builder.load(enclosing.global_var,
                                  name=self._fresh(".super.from"))
        name_ptr = self._cname_ptr(method_name)
        return builder.call(
            runtime["py_super_lookup"],
            [start_cls, from_cls, name_ptr],
            name=self._fresh(f"super.{method_name}"),
        )

    # ------------------------------------------------------ instantiation

    def emit_instantiate(self, class_name: str, arg_exprs, parent) -> ir.Value:
        """Emit ``MyClass(args)``.

        Allocates an instance via ``py_instance_new`` and invokes
        ``__init__`` if declared.
        """
        builder = parent.builder
        runtime = parent.runtime
        info = self.classes.get(class_name)
        if info is None:
            raise NotImplementedError(
                f"instantiation: class {class_name!r} not found in module"
            )
        cls_ptr = builder.load(info.global_var,
                                 name=self._fresh(f".cls.{class_name}"))
        inst = builder.call(runtime["py_instance_new"], [cls_ptr],
                              name=self._fresh(f"inst.{class_name}"))
        init_fn = info.methods.get("__init__")
        if init_fn is not None:
            # Marshal args to expected param types.
            init_args: list[ir.Value] = [inst]
            # Find the AST FuncDef for __init__ to get param annotations.
            init_ast_fd = self._find_method_def(class_name, "__init__")
            # Skip `self` and the bare ``*`` kw-only separator when
            # walking declared params.
            declared = [
                a for a in (init_ast_fd.args[1:] if init_ast_fd else ())
                if a.name != ""
            ]
            for i, arg_expr in enumerate(arg_exprs):
                v = parent._emit_expr(arg_expr)
                if i < len(declared) and declared[i].annotation is not None:
                    v = parent._coerce(v, arg_expr.ty, declared[i].annotation)
                else:
                    # Untyped init param -> marshal to PyObject*.
                    v = marshal.marshal_to_object(
                        builder, parent.module, runtime, v, arg_expr.ty
                    )
                init_args.append(v)
            # Fill in declared defaults for any positional parameters
            # the caller omitted (e.g. ``Config()`` with
            # ``__init__(self, x: int = 10, y: int = 20)``).
            for j in range(len(arg_exprs), len(declared)):
                arg = declared[j]
                if not getattr(arg, "has_default", False):
                    raise NotImplementedError(
                        f"instantiation: {class_name}.__init__ missing "
                        f"argument {arg.name!r} and has no default"
                    )
                v = parent._emit_expr(arg.default)
                if arg.annotation is not None:
                    v = parent._coerce(v, arg.default.ty, arg.annotation)
                else:
                    v = marshal.marshal_to_object(
                        builder, parent.module, runtime, v, arg.default.ty
                    )
                init_args.append(v)
            builder.call(init_fn, init_args)
        return inst

    def _find_method_def(self, class_name: str, method_name: str):
        # Prefer the expanded ClassDef when we synthesized extras via
        # @dataclass etc., so callers see the synthetic methods.
        info = self.classes.get(class_name)
        if info is not None and info.expanded_cd is not None:
            for s in info.expanded_cd.body:
                if isinstance(s, FuncDef) and s.name == method_name:
                    return s
        # Cross-module extern class: consult the synthetic FuncDef
        # stubs registered by ``declare_extern_class`` when the class
        # isn't part of this module's AST.
        if info is not None and getattr(info, "extern_method_defs", None):
            synth = info.extern_method_defs.get(method_name)
            if synth is not None:
                return synth
        for stmt in self.parent.ast_module.body:
            if isinstance(stmt, ClassDef) and stmt.name == class_name:
                for s in stmt.body:
                    if isinstance(s, FuncDef) and s.name == method_name:
                        return s
        return None


def _class_has_dataclass_decorator(cd: ClassDef) -> bool:
    for dec in cd.decorators:
        name = _simple_decorator_name(dec)
        if name in ("dataclass", "dataclasses.dataclass"):
            return True
        # ``@dataclass(...)`` with args: decorator is a Call whose
        # func is Name("dataclass") or Attr(Name("dataclasses"),"dataclass").
        if isinstance(dec, Call):
            inner = _simple_decorator_name(dec.func)
            if inner in ("dataclass", "dataclasses.dataclass"):
                return True
    return False


def _mangle_private_name(class_name: str, name: str) -> str:
    if not name.startswith("__"):
        return name
    if name.endswith("__"):
        return name
    cls = class_name.lstrip("_")
    if not cls:
        return name
    return f"_{cls}{name}"


def _simple_decorator_name(dec) -> Optional[str]:
    """Return a bare decorator name (``"staticmethod"``, ``"property"``,
    ``"<name>.setter"``) or ``None`` if the decorator shape is out of
    the narrow Phase-3 subset."""
    if isinstance(dec, Name):
        return dec.ident
    if isinstance(dec, Attr) and isinstance(dec.obj, Name):
        return f"{dec.obj.ident}.{dec.name}"
    return None


def _funcdef_is_property_setter(fd: FuncDef) -> bool:
    for dec in fd.decorators:
        dname = _simple_decorator_name(dec)
        if dname is not None and dname.endswith(".setter"):
            return True
    return False


__all__ = ["ClassLowering", "ClassLoweringError", "ClassInfo"]
