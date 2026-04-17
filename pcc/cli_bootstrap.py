import os
import sys

from .py_frontend import pipeline as _py_pipeline


_DEFAULT_EMIT_LL = "__PCC_DEFAULT_LL__"

_HELP_TEXT = """Usage: pcc [OPTIONS] PATH

Bootstrap-oriented Python entry for pcc self-hosting.

PATH must be a .py file.

Options:
  -h, --help                Show this help message and exit.
  --backend BACKEND         Native emission backend: llvm or self.
  --python-libpython MODE   auto, on, or off for Python fallback linkage.
  --ir-scaffold MODE        off (default), on, or auto. Enables Path A
                            closed-world IR-builder lowering (Issue 1).
  --emit-llvm[=PATH]        Emit LLVM IR instead of linking a native binary.
  -o PATH                   Output path for the compiled Python input.
  --verbose                 Print Python pipeline timing.
"""


def _write_text(text: str, *, err: bool = False, nl: bool = True) -> None:
    if nl:
        if text.endswith("\n"):
            if err:
                sys.stderr.write(text)
            else:
                sys.stdout.write(text)
        else:
            if err:
                sys.stderr.write(text + "\n")
            else:
                sys.stdout.write(text + "\n")
    else:
        if err:
            sys.stderr.write(text)
        else:
            sys.stdout.write(text)


def _normalized_sys_argv():
    argv = []
    i = 1
    while i < len(sys.argv):
        argv.append((sys.argv[i] or "") + "")
        i += 1
    return argv


def _copy_seq(values):
    out = []
    if values is None:
        return out
    i = 0
    while i < len(values):
        out.append(values[i])
        i += 1
    return out


def _option_value(arg):
    idx = arg.find("=")
    if idx >= 0:
        return arg[idx + 1 :]
    return ""


def _parse_python_libpython(value):
    lowered = (value or "").strip().lower()
    if lowered not in ("auto", "on", "off"):
        raise ValueError(
            "invalid --python-libpython "
            f"{value!r}; expected auto, on, or off"
        )
    return lowered


def _parse_ir_scaffold(value):
    lowered = (value or "").strip().lower()
    if lowered not in ("auto", "on", "off"):
        raise ValueError(
            "invalid --ir-scaffold "
            f"{value!r}; expected off, on, or auto"
        )
    return lowered


def _should_consume_emit_llvm_value(argv, index, path):
    next_index = index + 1
    if next_index >= len(argv):
        return False
    candidate = argv[next_index]
    if candidate == "--" or candidate.startswith("-"):
        return False
    if candidate.endswith(".ll") or candidate.endswith(".bc"):
        return True
    if path is not None:
        return True
    if next_index + 1 < len(argv):
        return True
    return False


def parse_bootstrap_cli_args(argv=None):
    if argv is None:
        argv = _normalized_sys_argv()
    else:
        argv = _copy_seq(argv)

    normalized_argv = []
    i = 0
    while i < len(argv):
        normalized_argv.append((argv[i] or "") + "")
        i += 1
    argv = normalized_argv

    if len(argv) == 1:
        arg0 = argv[0]
        if arg0 == "-h" or arg0 == "--help":
            return None, 0, None

    path = None
    output_path = None
    emit_llvm = None
    verbose = False
    python_libpython = None
    ir_scaffold = None
    backend = None

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            return None, 0, None
        if arg in ("-v", "--verbose"):
            verbose = True
            i += 1
            continue
        if arg.startswith("--backend="):
            backend = _option_value(arg)
            i += 1
            continue
        if arg == "--backend":
            if i + 1 >= len(argv):
                return None, 2, "--backend requires a value"
            backend = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--python-libpython="):
            try:
                python_libpython = _parse_python_libpython(_option_value(arg))
            except ValueError as exc:
                return None, 2, str(exc)
            i += 1
            continue
        if arg == "--python-libpython":
            if i + 1 >= len(argv):
                return None, 2, "--python-libpython requires a value"
            try:
                python_libpython = _parse_python_libpython(argv[i + 1])
            except ValueError as exc:
                return None, 2, str(exc)
            i += 2
            continue
        if arg.startswith("--ir-scaffold="):
            try:
                ir_scaffold = _parse_ir_scaffold(_option_value(arg))
            except ValueError as exc:
                return None, 2, str(exc)
            i += 1
            continue
        if arg == "--ir-scaffold":
            if i + 1 >= len(argv):
                return None, 2, "--ir-scaffold requires a value"
            try:
                ir_scaffold = _parse_ir_scaffold(argv[i + 1])
            except ValueError as exc:
                return None, 2, str(exc)
            i += 2
            continue
        if arg.startswith("--emit-llvm="):
            emit_llvm = _option_value(arg) or _DEFAULT_EMIT_LL
            i += 1
            continue
        if arg == "--emit-llvm":
            if _should_consume_emit_llvm_value(argv, i, path):
                emit_llvm = argv[i + 1]
                i += 2
            else:
                emit_llvm = _DEFAULT_EMIT_LL
                i += 1
            continue
        if arg.startswith("-o") and arg != "-o":
            output_path = arg[2:]
            i += 1
            continue
        if arg == "-o":
            if i + 1 >= len(argv):
                return None, 2, "-o requires a value"
            output_path = argv[i + 1]
            i += 2
            continue
        if arg.startswith("-"):
            return None, 2, f"unknown option: {arg}"
        if path is None:
            path = arg
        else:
            return None, 2, "bootstrap entry does not support program args"
        i += 1

    if path is None:
        return None, 2, "missing required PATH"

    return (
        path,
        output_path,
        emit_llvm,
        verbose,
        python_libpython,
        ir_scaffold,
        backend,
    ), 0, None


def bootstrap_cli_main(argv=None) -> int:
    parsed, status, err = parse_bootstrap_cli_args(argv)
    if parsed is None:
        if err is None:
            _write_text(_HELP_TEXT, nl=False)
        else:
            _write_text("Error: " + str(err), err=True)
            _write_text(_HELP_TEXT, err=True, nl=False)
        return status

    (
        path,
        output_path,
        emit_llvm,
        verbose,
        python_libpython,
        ir_scaffold,
        backend,
    ) = parsed
    path = (path or "") + ""
    output_path = None if output_path is None else (output_path or "") + ""
    emit_llvm = None if emit_llvm is None else (emit_llvm or "") + ""
    verbose = True if verbose else False
    python_libpython = (
        None if python_libpython is None else (python_libpython or "") + ""
    )
    ir_scaffold = None if ir_scaffold is None else (ir_scaffold or "") + ""
    backend = None if backend is None else (backend or "") + ""

    if not path.endswith(".py"):
        _write_text(
            "Error: bootstrap entry only supports Python inputs; "
            "use the full `pcc` CLI for C inputs",
            err=True,
        )
        return 1

    if not os.path.exists(path):
        _write_text("Error: input file not found: " + path, err=True)
        return 1

    if output_path is None and emit_llvm is None:
        _write_text(
            "Error: bootstrap entry requires -o PATH or --emit-llvm for Python inputs",
            err=True,
        )
        return 1

    if emit_llvm is not None:
        if emit_llvm == _DEFAULT_EMIT_LL:
            ll_out = output_path if output_path else path[:-3] + ".ll"
        else:
            ll_out = output_path if output_path else emit_llvm
        _py_pipeline.compile_python(
            path,
            ll_out,
            verbose=verbose,
            emit_llvm_only=True,
            libpython_mode=python_libpython,
            ir_scaffold_mode=ir_scaffold,
            backend=backend,
            recursive_stdlib=False,
        )
    else:
        _py_pipeline.compile_python(
            path,
            output_path,
            verbose=verbose,
            emit_llvm_only=False,
            libpython_mode=python_libpython,
            ir_scaffold_mode=ir_scaffold,
            backend=backend,
            recursive_stdlib=False,
        )
    return 0


def bootstrap_cli_sys_argv_exit() -> None:
    code = bootstrap_cli_main()
    if code != 0:
        from sys import exit as _exit

        _exit(code)
