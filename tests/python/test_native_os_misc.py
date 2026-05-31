"""Native dispatch for low-volume `os.X` (getcwd, access) + `os.{F,R,W,X}_OK`
constants.

These each have one or two callsites in the bootstrap closure, but the
chained-call savings (no ``import os`` cpy import per call site, no
``cpy.get.<name>``, no ``cpy.call*``) add up to ~10 cpy_* per use. The
dispatch lives in ``_emit_native_os_call`` (for the methods) and
``_emit_attr`` (for the access(2) mode constants).
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).absolute().parents[2]
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)


def _compile_to_ll(source: str, name: str, *, mode: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source)
    compile_python(
        str(src), str(out),
        emit_llvm_only=True,
        ir_scaffold_mode=mode,
    )
    return out.read_text()


def _function_body(ir_text: str, fn_name_suffix: str) -> str | None:
    pattern = re.compile(
        r"define\s+[^\n]*?@[A-Za-z0-9_]*"
        + re.escape(fn_name_suffix)
        + r"\s*\([^)]*\)[^{]*\{(.+?)\n\}",
        re.DOTALL,
    )
    m = pattern.search(ir_text)
    return m.group(1) if m else None


@pytest.mark.parametrize("mode", ["off", "on"])
def test_getcwd_dispatches_to_native(mode):
    program = textwrap.dedent(
        """
        import os

        def f() -> str:
            return os.getcwd()
        """
    )
    ir = _compile_to_ll(program, f"getcwd_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_getcwd_str" in body, body
    assert "cpy.get.getcwd" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_access_dispatches_to_native(mode):
    program = textwrap.dedent(
        """
        import os

        def f(p: str) -> bool:
            return os.access(p, os.X_OK)
        """
    )
    ir = _compile_to_ll(program, f"access_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_access" in body, body
    assert "cpy.get.access" not in body, body
    assert "cpy.get.X_OK" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_getcwd_result_stays_native_inside_os_path_join(mode):
    program = textwrap.dedent(
        """
        import os

        def f() -> str:
            return str(os.path.join(os.getcwd(), "pcc", "py_runtime"))
        """
    )
    ir = _compile_to_ll(program, f"getcwd_join_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_getcwd_str" in body, body
    assert "@py_os_path_join" in body, body
    assert "cpy.fn.join" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_join_accepts_object_attribute_args(mode):
    program = textwrap.dedent(
        """
        import os

        class Box:
            root: str

            def __init__(self, root: str):
                self.root = root

        def f(box: Box) -> str:
            return os.path.join(box.root, "pkg", "module.py")
        """
    )
    ir = _compile_to_ll(program, f"path_join_attr_arg_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_join" in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.fn.join" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_dirname_accepts_object_attribute_args(mode):
    program = textwrap.dedent(
        """
        import os

        class Box:
            path: str

            def __init__(self, path: str):
                self.path = path

        def f(box: Box) -> str:
            return os.path.dirname(box.path)
        """
    )
    ir = _compile_to_ll(program, f"path_dirname_attr_arg_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_dirname" in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.fn.dirname" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_split_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f(path: str):
            return os.path.split(path)
        """
    )
    ir = _compile_to_ll(program, f"path_split_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_split" in body, body
    assert "cpy.fn.split" not in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_expanduser_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f(path: str):
            return os.path.expanduser(path)
        """
    )
    ir = _compile_to_ll(program, f"path_expanduser_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_expanduser" in body, body
    assert "cpy.fn.expanduser" not in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_expandvars_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f(path: str):
            return os.path.expandvars(path)
        """
    )
    ir = _compile_to_ll(program, f"path_expandvars_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_expandvars" in body, body
    assert "cpy.fn.expandvars" not in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_realpath_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f(path: str):
            return os.path.realpath(path)
        """
    )
    ir = _compile_to_ll(program, f"path_realpath_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_realpath" in body, body
    assert "cpy.fn.realpath" not in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_join_accepts_sys_prefix_args(mode):
    program = textwrap.dedent(
        """
        import os
        import sys

        def f() -> str:
            return os.path.join(sys.prefix, sys.base_prefix, "lib")
        """
    )
    ir = _compile_to_ll(program, f"path_join_sys_prefix_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_sys_prefix_str" in body, body
    assert "@py_os_path_join" in body, body
    assert "cpy.get.prefix" not in body, body
    assert "cpy.get.base_prefix" not in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.fn.join" not in body, body
    assert "cpy.import.sys" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_imported_sys_prefix_stays_native_in_os_path_join(mode):
    program = textwrap.dedent(
        """
        import os
        from sys import prefix

        def f() -> str:
            return os.path.join(prefix, "include")
        """
    )
    ir = _compile_to_ll(program, f"path_join_imported_sys_prefix_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_sys_prefix_str" in body, body
    assert "@py_os_path_join" in body, body
    assert "cpy.get.prefix" not in body, body
    assert "cpy.get.path" not in body, body
    assert "@.cpy." not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_basename_accepts_sys_argv_subscript(mode):
    program = textwrap.dedent(
        """
        import os
        import sys

        def f() -> str:
            return os.path.basename(sys.argv[0])
        """
    )
    ir = _compile_to_ll(program, f"path_basename_sys_argv_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_basename" in body, body
    assert "@py_program_argc" in body, body
    assert "cpy.get.argv" not in body, body
    assert "cpy.fn.basename" not in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.import.sys" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_join_accepts_container_subscript_args(mode):
    program = textwrap.dedent(
        """
        import os

        def f(options, vrd) -> str:
            return os.path.join(options["root"], vrd["name"])
        """
    )
    ir = _compile_to_ll(program, f"path_join_subscript_args_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_join" in body, body
    assert "cpy.fn.join" not in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_commonprefix_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f(paths: list) -> str:
            return os.path.commonprefix(paths)
        """
    )
    ir = _compile_to_ll(program, f"path_commonprefix_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_commonprefix" in body, body
    assert "cpy.fn.commonprefix" not in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_join_starred_split_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f(name: str) -> str:
            parts = name.split(".")
            return os.path.join(*parts)
        """
    )
    ir = _compile_to_ll(program, f"path_join_starred_split_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_str_split" in body, body
    assert "@py_os_path_join" in body, body
    assert "cpy.fn.join" not in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_join_starred_list_concat_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f(root: str, name: str) -> str:
            parts = [root] + name.split(".")[:-1]
            return os.path.join(*parts)
        """
    )
    ir = _compile_to_ll(program, f"path_join_starred_concat_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_list_extend" in body, body
    assert "@py_os_path_join" in body, body
    assert "cpy.fn.join" not in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_join_accepts_str_slice_args(mode):
    program = textwrap.dedent(
        """
        import os

        def f(absprefix: str, path: str, d: str) -> bool:
            return (
                os.path.join(absprefix[:len(d)], absprefix[len(d):]) == absprefix
                and os.path.join(path[:len(d)], path[len(d):]) == path
            )
        """
    )
    ir = _compile_to_ll(program, f"path_join_str_slice_args_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert body.count("@py_os_path_join") == 2, body
    assert "cpy.fn.join" not in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_shlex_split_posix_true_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import shlex

        def f(text: str):
            return shlex.split(text, posix=True)
        """
    )
    ir = _compile_to_ll(program, f"shlex_split_posix_true_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_shlex_split" in body, body
    assert "cpy.fn.split" not in body, body
    assert "cpy.import.shlex" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_basename_accepts_function_call_arg(mode):
    program = textwrap.dedent(
        """
        import os

        def make_path(name):
            return "pkg/" + name

        def f(name: str) -> str:
            return os.path.basename(make_path(name))
        """
    )
    ir = _compile_to_ll(program, f"path_basename_call_arg_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_basename" in body, body
    assert "cpy.get.path" not in body, body
    assert "cpy.fn.basename" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_isabs_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f(path: str) -> bool:
            return os.path.isabs(path)
        """
    )
    ir = _compile_to_ll(program, f"path_isabs_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_isabs" in body, body
    assert "cpy.fn.isabs" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_normcase_feeds_splitext_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f(src_name: str):
            return os.path.splitext(os.path.normcase(src_name))
        """
    )
    ir = _compile_to_ll(program, f"path_normcase_splitext_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_normcase" in body, body
    assert "@py_os_path_splitext" in body, body
    assert "cpy.fn.normcase" not in body, body
    assert "cpy.fn.splitext" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_normpath_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f(path: str):
            return os.path.normpath(path)
        """
    )
    ir = _compile_to_ll(program, f"path_normpath_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_normpath" in body, body
    assert "cpy.fn.normpath" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_sep_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f() -> str:
            return os.path.sep
        """
    )
    ir = _compile_to_ll(program, f"path_sep_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "cpy.get.sep" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_path_splitdrive_lowers_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f(src_name: str):
            return os.path.splitdrive(src_name)
        """
    )
    ir = _compile_to_ll(program, f"path_splitdrive_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_path_splitdrive" in body, body
    assert "cpy.fn.splitdrive" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("const", ["F_OK", "R_OK", "W_OK", "X_OK"])
@pytest.mark.parametrize("mode", ["off", "on"])
def test_access_mode_constants_dispatch(mode, const):
    """Each access(2) mode constant must go through py_int_from_i64
    (literal) instead of py_cpy_getattr — the value is fixed by POSIX
    so it's always known at compile time."""
    program = textwrap.dedent(
        f"""
        import os

        def f() -> int:
            return os.{const}
        """
    )
    ir = _compile_to_ll(program, f"const_{const}_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert f"cpy.get.{const}" not in body, body
    assert "@py_int_from_i64" in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_pathsep_join_dispatches_without_libpython(mode):
    program = textwrap.dedent(
        """
        import os

        def f() -> str:
            parts = ["a", "b", "c"]
            return os.pathsep.join(parts)
        """
    )
    ir = _compile_to_ll(program, f"pathsep_join_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "cpy.get.pathsep" not in body, body
    assert "@.cpy." not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_imported_pathsep_join_dispatches_without_libpython(mode):
    program = textwrap.dedent(
        """
        from os import pathsep

        def f() -> str:
            parts = ["a", "b", "c"]
            return pathsep.join(parts)
        """
    )
    ir = _compile_to_ll(program, f"imported_pathsep_join_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "cpy.get.pathsep" not in body, body
    assert "@.cpy." not in body, body
