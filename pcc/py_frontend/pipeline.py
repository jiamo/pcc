"""Python frontend compilation pipeline.

Orchestrates the full pipeline for a single ``.py`` file:

    source(.py)
      -> pcc.py_frontend.parser.parse()        -> Module AST
      -> pcc.py_frontend.type_infer.infer_module() -> typed Module
      -> pcc.py_frontend.codegen.layer1.L1CodeGen().generate() -> LLVM IR text
      -> write .ll to a temp file
      -> clang .ll + pcc/py_runtime/libpy_runtime.a -> native exe

This is the Phase 1 MVP dispatcher. See
``docs/plans/python-frontend-interfaces.md`` for the frozen v0.1
interface contract and ``docs/plans/python-frontend-plan.md`` for the
Phase 1 scope.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Optional


# Resolve pcc/py_runtime/ at import time (this file lives under
# pcc/py_frontend/, so the runtime is one directory up and sideways).
_PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
_PCC_DIR = os.path.dirname(_PIPELINE_DIR)
_PY_RUNTIME_DIR = os.path.join(_PCC_DIR, "py_runtime")
_PY_RUNTIME_ARCHIVE = os.path.join(_PY_RUNTIME_DIR, "libpy_runtime.a")


class PyPipelineError(RuntimeError):
    """Raised when the Python pipeline fails in a user-visible way."""


def _log(verbose: bool, msg: str) -> None:
    if verbose:
        print(f"[pcc.py] {msg}", file=sys.stderr)


def _module_name_from_src(src_path: str) -> str:
    base = os.path.basename(src_path)
    if base.endswith(".py"):
        base = base[:-3]
    return base or "<module>"


def _timed(verbose: bool, label: str, fn):
    """Run ``fn`` and, when verbose, print how long it took."""
    if not verbose:
        return fn()
    t0 = time.perf_counter()
    try:
        return fn()
    finally:
        dt = (time.perf_counter() - t0) * 1000.0
        print(f"[pcc.py] {label} took {dt:.1f} ms", file=sys.stderr)


def _ensure_runtime(verbose: bool) -> Optional[str]:
    """Locate (and optionally build) ``libpy_runtime.a``.

    Returns the path if available, otherwise ``None`` (with a warning
    printed). Phase 1 MVP: linking without the runtime will fail on
    undefined symbols, but we don't fail the Python pipeline just
    because the runtime hasn't been built yet.
    """
    if os.path.isfile(_PY_RUNTIME_ARCHIVE):
        _log(verbose, f"runtime archive: {_PY_RUNTIME_ARCHIVE}")
        return _PY_RUNTIME_ARCHIVE

    makefile = os.path.join(_PY_RUNTIME_DIR, "Makefile")
    if os.path.isfile(makefile):
        # Always build with the CPython C-API fallback enabled so that
        # programs that use ``import`` have py_cpy_* wired through to
        # libpython. Programs that don't use import pay zero runtime
        # cost for these symbols at startup.
        _log(verbose, f"building runtime: make -C {_PY_RUNTIME_DIR}")
        try:
            subprocess.run(
                ["make", "-C", _PY_RUNTIME_DIR, "PCC_WITH_LIBPYTHON=1"],
                check=True,
                capture_output=not verbose,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(
                f"warning: failed to build py_runtime ({e}); "
                "final link may fail on undefined py_* symbols",
                file=sys.stderr,
            )
            return None
        if os.path.isfile(_PY_RUNTIME_ARCHIVE):
            return _PY_RUNTIME_ARCHIVE

    print(
        f"warning: {_PY_RUNTIME_ARCHIVE} not found; "
        "final link may fail on undefined py_* symbols",
        file=sys.stderr,
    )
    return None


def _emit_ll(ir_text: str, out_ll_path: str, verbose: bool) -> None:
    _log(verbose, f"writing LLVM IR to {out_ll_path} ({len(ir_text)} bytes)")
    with open(out_ll_path, "w", encoding="utf-8") as f:
        f.write(ir_text)


def _link_with_clang(
    ll_path,
    out_path: str,
    runtime_archive: Optional[str],
    verbose: bool,
    *,
    needs_libpython: bool = False,
) -> None:
    """Link one or more ``.ll`` files into a native executable.

    ``ll_path`` accepts a single path (original single-file
    contract) or a list of paths (multi-file compile). Each entry
    is passed in the order given; clang resolves cross-file
    references at link time.
    """
    clang = shutil.which("clang")
    if clang is None:
        raise PyPipelineError(
            "clang not found on PATH; cannot link Python frontend output"
        )
    if isinstance(ll_path, (list, tuple)):
        ll_paths = list(ll_path)
    else:
        ll_paths = [ll_path]
    # ``-lc++`` is required because the exception runtime uses the
    # Itanium C++ ABI (__cxa_throw / __cxa_begin_catch / ...).
    cmd = [clang, *ll_paths, "-o", out_path, "-lm", "-lc++"]
    if runtime_archive is not None:
        # Put the archive after the .ll inputs so the linker pulls
        # its symbols in once the user objects have declared them.
        insert_at = 1 + len(ll_paths)
        cmd.insert(insert_at, runtime_archive)
    if needs_libpython:
        # ``python3-config --ldflags --embed`` returns the right
        # -L / -l / framework flags for the active Python install.
        import subprocess as _sp
        try:
            out = _sp.check_output(
                ["python3-config", "--ldflags", "--embed"],
                text=True,
            ).strip()
            cmd.extend(out.split())
        except (FileNotFoundError, _sp.CalledProcessError) as e:
            raise PyPipelineError(
                f"python3-config required for import-using programs: {e}"
            ) from e
    _log(verbose, f"link: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise PyPipelineError(f"clang link failed (exit {e.returncode})") from e


def _module_needs_libpython(ast_module, native_modules=None) -> bool:
    """Scan the parsed AST for any ``import`` statement; if present the
    link step must pull in libpython because codegen will emit
    ``py_cpy_*`` calls. Walks both module scope and function bodies.

    ``native_modules`` is an optional iterable of dotted names that
    are being compiled natively in the same multi-file invocation —
    imports of these are routed to extern symbols (no CPython path).
    """
    from .py_ast import (
        Import as _Import,
        ImportFrom as _ImportFrom,
    )

    _SCAFFOLD_MODULES = {"pcc.extern", "pcc.llvm_capi"}
    # Kept in sync with ``layer1._COMPILE_TIME_ONLY_MODULES``: imports
    # of these are folded away by codegen, so the link step must not
    # pretend libpython is required on their account.
    _COMPILE_TIME_ONLY = {"__future__", "typing", "click"}
    native_set = set(native_modules or ())
    cur_mod = getattr(ast_module, "name", "") or ""
    cur_parts = cur_mod.split(".") if cur_mod else []

    def _resolve_relative(module, level):
        if not level:
            return module or ""
        if level > len(cur_parts):
            return module or ""
        base = cur_parts[: len(cur_parts) - level]
        if module:
            return ".".join(base + [module])
        return ".".join(base)

    def _walk(stmts) -> bool:
        for stmt in stmts:
            if isinstance(stmt, _ImportFrom):
                if stmt.module in _SCAFFOLD_MODULES:
                    continue
                if (
                    stmt.module is not None
                    and stmt.module.split(".")[0] in _COMPILE_TIME_ONLY
                ):
                    continue
                resolved = _resolve_relative(
                    stmt.module, getattr(stmt, "level", 0) or 0,
                )
                if resolved in native_set:
                    continue
                return True
            if isinstance(stmt, _Import):
                remaining = [
                    m for (m, _) in stmt.names
                    if m.split(".")[0] not in _COMPILE_TIME_ONLY
                    and m not in native_set
                ]
                if not remaining:
                    continue
                return True
            # Only descend into the body / handler / else branches of
            # statements we know carry a list of sub-stmts. Using
            # Explicit attribute access — each AST node has a known
            # shape; no need for dynamic getattr. Drop through when a
            # field is missing.
            body = stmt.body if hasattr(stmt, "body") else None
            if body and _walk(body):
                return True
            else_body = stmt.else_body if hasattr(stmt, "else_body") else None
            if else_body and _walk(else_body):
                return True
            finally_body = stmt.finally_body if hasattr(stmt, "finally_body") else None
            if finally_body and _walk(finally_body):
                return True
            handlers = stmt.handlers if hasattr(stmt, "handlers") else None
            if handlers:
                for h in handlers:
                    h_body = h.body if hasattr(h, "body") else ()
                    if _walk(h_body):
                        return True
        return False

    return _walk(ast_module.body)


def compile_python(
    src_path: str,
    out_path: str,
    *,
    verbose: bool = False,
    emit_llvm_only: bool = False,
) -> None:
    """Compile a single ``.py`` file to a native executable.

    Parameters
    ----------
    src_path:
        Path to the input ``.py`` source file.
    out_path:
        Output path. When ``emit_llvm_only`` is False this is the
        native executable; when True it is the ``.ll`` file.
    verbose:
        If True, print each pipeline step and timing info to stderr.
    emit_llvm_only:
        If True, stop after writing LLVM IR (used by ``--emit-llvm``).
    """
    # Imports are deferred so that modules still under construction by
    # sibling agents don't break ``pcc --help`` or ``.c`` compilation.
    try:
        from pcc.py_frontend import parser as _parser
        from pcc.py_frontend import type_infer as _type_infer
        from pcc.py_frontend.codegen.layer1 import L1CodeGen
    except ImportError as e:
        raise PyPipelineError(
            f"Python frontend module not available: {e}. "
            "The Python pipeline is currently Phase 1 MVP and some "
            "components may still be under construction."
        ) from e

    if not os.path.isfile(src_path):
        raise PyPipelineError(f"input file not found: {src_path}")

    _log(verbose, f"reading {src_path}")
    with open(src_path, "r", encoding="utf-8") as f:
        source = f.read()

    _log(verbose, "parse")
    # Default path: pcc.parse.py_parse + pcc.parse.py_lift (native,
    # self-host-compatible). PCC_USE_CPYTHON_AST=1 is a reverse-opt-out
    # escape hatch for the legacy CPython-ast-backed parser — kept
    # until P6C.6 three-stage bootstrap is byte-identical-verified.
    # Scope of this flip is the Python source closure only; Strategy C
    # (zero libpython) still requires PLY retirement and llvm_capi
    # wire-up before pcc can build as a pure native binary.
    if os.environ.get("PCC_USE_CPYTHON_AST") == "1":
        ast_mod = _timed(
            verbose, "parse (cpython-ast fallback)",
            lambda: _parser.parse(source, filename=src_path),
        )
    else:
        from pcc.parse.py_lift import parse_and_lift as _native_pl
        ast_mod = _timed(
            verbose, "parse",
            lambda: _native_pl(source, src_path, _module_name_from_src(src_path)),
        )

    _log(verbose, "type_infer")
    typed_mod = _timed(
        verbose, "type_infer", lambda: _type_infer.infer_module(ast_mod)
    )

    _log(verbose, "codegen (layer1)")
    codegen = L1CodeGen(typed_mod)
    ir_text = _timed(verbose, "codegen", lambda: codegen.generate(typed_mod))
    if not isinstance(ir_text, str):
        # Some codegen implementations may return a module-like object
        # with __str__; coerce defensively.
        ir_text = str(ir_text)

    if emit_llvm_only:
        # out_path is a .ll path; just write it and return.
        _emit_ll(ir_text, out_path, verbose)
        return

    needs_libpython = _module_needs_libpython(ast_mod)
    # Fallback: scan the generated IR for direct call sites into the
    # libpython shim (``py_cpy_*``). Codegen emits these for DynType
    # method dispatch, ``hasattr`` fallback, ``x.__copy__()`` and
    # similar even when the source has no explicit ``import``. Using
    # ``\bcall`` rather than a plain text search avoids triggering on
    # the ``declare external`` stubs emitted unconditionally for all
    # runtime helpers.
    if not needs_libpython and "@py_cpy_" in ir_text:
        import re as _re
        if _re.search(r"\bcall [^\n]*@py_cpy_", ir_text):
            needs_libpython = True

    # Write IR to a temp .ll, link with clang + runtime, produce exe.
    with tempfile.TemporaryDirectory(prefix="pcc_py_") as tmp:
        ll_path = os.path.join(tmp, os.path.basename(out_path) + ".ll")
        _emit_ll(ir_text, ll_path, verbose)
        runtime = _ensure_runtime(verbose)
        _timed(
            verbose,
            "link",
            lambda: _link_with_clang(
                ll_path, out_path, runtime, verbose,
                needs_libpython=needs_libpython,
            ),
        )
    _log(verbose, f"wrote executable: {out_path}")


def compile_python_multi(
    src_paths,
    out_path: str,
    *,
    verbose: bool = False,
    emit_llvm_only: bool = False,
    entry_module: Optional[str] = None,
    module_names=None,
) -> None:
    """Compile multiple ``.py`` files into a single native executable.

    This is the infrastructure step for #138.5 three-stage
    bootstrap. Each source file is parsed, type-inferred, and
    lowered to LLVM IR independently; the resulting ``.ll`` files
    are handed to clang together so that cross-module symbol
    references — declared as ``external`` by each module's codegen
    — are resolved at link time.

    Parameters
    ----------
    src_paths:
        Ordered list of ``.py`` files. The first entry provides
        the native executable's ``main`` entry point (the one
        that pcc synthesises to call top-level module code).
    module_names:
        Optional parallel list of dotted module names. Defaults
        to the filename stem. The names influence the
        ``user_<module>_<fn>`` symbol mangling so two files can
        define unrelated ``main`` functions without colliding.
    entry_module:
        Dotted module name whose top-level ``main()`` is the
        executable entry. Defaults to the first file's module.

    The multi-compile API **does not** yet rewrite cross-module
    imports to extern references — step 2 of the spike plan
    (``docs/plans/multi-file-compile-spike.md``). Until that
    lands, imports between passed source files still route through
    ``py_cpy_import`` and the link pulls libpython. Single-file
    callers should keep using :func:`compile_python`.
    """
    if not src_paths:
        raise PyPipelineError(
            "compile_python_multi requires at least one source file"
        )
    src_paths = list(src_paths)
    if module_names is None:
        module_names = [_module_name_from_src(p) for p in src_paths]
    if len(module_names) != len(src_paths):
        raise PyPipelineError(
            "module_names length must match src_paths length"
        )

    try:
        from pcc.py_frontend import type_infer as _type_infer
        from pcc.py_frontend.codegen.layer1 import L1CodeGen
    except ImportError as e:
        raise PyPipelineError(
            f"Python frontend module not available: {e}"
        ) from e

    any_needs_libpython = False
    module_ir_texts = []

    # Decide which module is the entry (emits ``@main``). Default:
    # first source file in the list.
    if entry_module is None:
        entry_module = module_names[0]
    if entry_module not in module_names:
        raise PyPipelineError(
            f"entry_module {entry_module!r} not among module_names "
            f"{module_names!r}"
        )
    # Sibling modules whose top-level code the entry must run before
    # its own body (in ``src_paths`` order, entry itself excluded).
    sibling_inits = [m for m in module_names if m != entry_module]

    # Pre-pass: parse every module and collect exported top-level
    # FuncDefs per module. This table is given to each codegen so
    # ``from .sibling import fn`` can declare ``fn`` as an extern
    # with the correct signature instead of routing through the
    # py_cpy_import path (which would pull libpython).
    from pcc.py_frontend.py_ast import (
        FuncDef as _FuncDef,
        ClassDef as _ClassDef,
        Assign as _Assign,
        Name as _Name,
        Attr as _Attr,
    )
    ast_mods = {}  # mod_name -> (src, ast_mod)
    native_exports = {}
    # Per-mod: {name: {kind: 'function'|'class', ...}}
    # Pre-pass 1: parse all modules and extract exports from top-level
    # FuncDef/ClassDef annotations without running type inference.
    # This way each module's inference (pre-pass 2) sees the
    # cross-module export table built from the raw annotations.
    for src, mod_name in zip(src_paths, module_names):
        if not os.path.isfile(src):
            raise PyPipelineError(f"input file not found: {src}")
        with open(src, "r", encoding="utf-8") as f:
            source = f.read()
        if os.environ.get("PCC_USE_CPYTHON_AST") == "1":
            from pcc.py_frontend import parser as _parser
            ast_mod = _parser.parse(source, filename=src)
        else:
            from pcc.parse.py_lift import parse_and_lift as _native_pl
            ast_mod = _native_pl(source, src, mod_name)
        ast_mods[mod_name] = (src, ast_mod)
        exports = {}
        for s in ast_mod.body:
            if isinstance(s, _FuncDef):
                exports[s.name] = {
                    "kind": "function",
                    "return_ty": s.return_ty,
                    "param_types": [a.annotation for a in s.args],
                    "func_def": s,
                }
            elif isinstance(s, _ClassDef):
                # Collect ``field: ann[= default]`` class-body entries
                # plus any ``self.<name> = …`` assignments in __init__.
                field_names = []
                methods = []
                for body_stmt in s.body:
                    if isinstance(body_stmt, _Assign):
                        for t in body_stmt.targets:
                            if (
                                isinstance(t, _Name)
                                and t.ident not in field_names
                            ):
                                field_names.append(t.ident)
                    elif isinstance(body_stmt, _FuncDef):
                        kind = "instance"
                        for dec in body_stmt.decorators:
                            if isinstance(dec, _Name):
                                if dec.ident == "staticmethod":
                                    kind = "static"
                                elif dec.ident == "classmethod":
                                    kind = "classmethod"
                                elif dec.ident == "property":
                                    kind = "property_getter"
                        methods.append({
                            "name": body_stmt.name,
                            "kind": kind,
                            "return_ty": body_stmt.return_ty,
                            "param_types": [
                                a.annotation for a in body_stmt.args
                            ],
                        })
                        if body_stmt.name == "__init__":
                            for init_stmt in body_stmt.body:
                                if isinstance(init_stmt, _Assign):
                                    for t in init_stmt.targets:
                                        if (
                                            isinstance(t, _Attr)
                                            and isinstance(t.obj, _Name)
                                            and t.obj.ident == "self"
                                            and t.name not in field_names
                                        ):
                                            field_names.append(t.name)
                exports[s.name] = {
                    "kind": "class",
                    "class_name": s.name,
                    "field_names": tuple(field_names),
                    "methods": tuple(methods),
                }
        native_exports[mod_name] = exports

    # Pre-pass 2: run type inference on each module, now with the
    # cross-module export table populated.
    typed_mods = {}
    for _src, mod_name in zip(src_paths, module_names):
        src, ast_mod = ast_mods[mod_name]
        # Exclude the current module from its own external-exports
        # view — ``from .self import ...`` is a self-reference.
        external_for_this = {
            k: v for k, v in native_exports.items() if k != mod_name
        }
        typed_mod = _timed(
            verbose, f"type_infer[{mod_name}]",
            lambda: _type_infer.infer_module(
                ast_mod, external_exports=external_for_this,
            ),
        )
        typed_mods[mod_name] = (src, ast_mod, typed_mod)

    for _src, mod_name in zip(src_paths, module_names):
        _log(verbose, f"codegen {mod_name}")
        src, ast_mod, typed_mod = typed_mods[mod_name]
        codegen = L1CodeGen(typed_mod)
        is_entry = (mod_name == entry_module)
        codegen._skip_program_main = not is_entry
        if is_entry:
            codegen._sibling_module_inits = tuple(sibling_inits)
        # Exclude the current module from the cross-module registry so
        # ``from .sibling import`` within the sibling itself isn't a
        # self-reference.
        codegen._native_module_exports = {
            k: v for k, v in native_exports.items() if k != mod_name
        }
        ir_text = _timed(
            verbose, f"codegen[{mod_name}]",
            lambda: codegen.generate(typed_mod),
        )
        if not isinstance(ir_text, str):
            ir_text = str(ir_text)
        module_ir_texts.append((mod_name, ir_text))
        if _module_needs_libpython(ast_mod, native_modules=module_names):
            any_needs_libpython = True
        elif "@py_cpy_" in ir_text:
            # IR-level fallback — see ``compile_python`` above for the
            # same pattern.
            import re as _re
            if _re.search(r"\bcall [^\n]*@py_cpy_", ir_text):
                any_needs_libpython = True

    if emit_llvm_only:
        # Concatenate all IR texts with a separator comment so the
        # output is still valid LLVM IR (each module's header lines
        # are duplicated but ``llvm-as`` tolerates redundant
        # target-triple / datalayout directives).
        combined = "\n\n".join(
            f"; ---- module: {name} ----\n{text}"
            for name, text in module_ir_texts
        )
        _emit_ll(combined, out_path, verbose)
        return

    with tempfile.TemporaryDirectory(prefix="pcc_py_multi_") as tmp:
        ll_paths = []
        for mod_name, text in module_ir_texts:
            safe = mod_name.replace(".", "_").replace("-", "_")
            p = os.path.join(tmp, f"{safe}.ll")
            _emit_ll(text, p, verbose)
            ll_paths.append(p)
        runtime = _ensure_runtime(verbose)
        _timed(
            verbose,
            "link",
            lambda: _link_with_clang(
                ll_paths, out_path, runtime, verbose,
                needs_libpython=any_needs_libpython,
            ),
        )
    _log(verbose, f"wrote executable: {out_path}")
