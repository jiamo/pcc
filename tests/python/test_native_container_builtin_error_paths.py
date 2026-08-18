"""Container-builtin walks must fail closed on their exception edges.

Three positional walks (``dict(d)`` copy, ``any()``/``all()``, ``zip()``)
acquire owned temporaries — a ``py_dict_keys`` view, per-iteration
element/index refs, the result under construction — and then call runtime
helpers that can raise for DynType sources.  Two defect classes, found by
external review plus reading the runtime contracts:

1. dict-copy's existing ``py_err_occurred`` checks jumped to the error exit
   WITHOUT releasing the owned keys view / loop temps: a leak on every
   raising edge.
2. any/all and zip had NO checks inside their walks at all.  A mid-loop
   raise (``py_obj_getitem`` KeyError on a dyn-typed dict) left the error
   in TLS, kept looping over NULL elements, and returned a silently wrong
   result.  Worse, ``py_dict_keys`` on a non-dict returns NULL WITHOUT
   raising (py_dict.c), so ``dict(x)`` over a dyn non-dict silently
   produced ``{}`` where CPython raises TypeError.

CPython-truth note: ``any({"a": 1})`` is ``True`` in CPython (iterating a
mapping yields keys).  Making DynType-held mappings iterate by keys is the
separate ``PY-P1-SET-FROM-DYN-MAPPING`` row (iterator protocol); until that
lands, the contract pinned here for dyn-held mappings is FAIL CLOSED — a
clean catchable exception, never a silent wrong answer.  The dyn non-dict
``dict(x)`` case IS CPython-correct here (TypeError).

Static-shape cost guard: sources whose static type proves the walk cannot
raise (``ListType``/``TupleType``/``DictType``/``SetType``) must gain NO new
``py_err_occurred`` checks — the fail-closed edges are emitted only for
DynType sources, so pcc1's own hot shapes keep byte-stable IR.
"""
from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).absolute().parents[2]
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)


def _compile_to_ll(source: str, name: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    compile_python(
        str(src),
        str(out),
        emit_llvm_only=True,
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    return out.read_text(encoding="utf-8")


def _function_body(ir_text: str, fn_name_suffix: str) -> str | None:
    pattern = re.compile(
        r"define\s+[^\n]*?@[A-Za-z0-9_]*"
        + re.escape(fn_name_suffix)
        + r"\s*\([^)]*\)[^{]*\{(.+?)\n\}",
        re.DOTALL,
    )
    match = pattern.search(ir_text)
    return match.group(1) if match else None


def _run_native(tmp_path: Path, source: str) -> subprocess.CompletedProcess:
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
        backend="llvm",
    )
    return subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=60
    )


# ---------------------------------------------------------------- semantic


def test_dict_of_non_dict_dyn_raises_typeerror(tmp_path):
    """CPython-correct: ``dict(5)`` is a TypeError.  The silent path was
    py_dict_keys(non-dict) -> NULL without raising -> empty dict."""
    native = _run_native(
        tmp_path,
        """
        def check(x: object) -> None:
            try:
                d2 = dict(x)
                print("returned")
                print(len(d2))
            except Exception:
                print("caught")

        def main() -> None:
            check(5)

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split() == ["caught"], native.stdout


def test_any_over_dyn_mapping_fails_closed(tmp_path):
    """Interim fail-closed contract (see module docstring): the mid-walk
    KeyError must exit the walk cleanly, not keep looping over NULLs and
    return False with a stale TLS error."""
    native = _run_native(
        tmp_path,
        """
        def check(x: object) -> None:
            try:
                r = any(x)
                print("returned")
                print(r)
            except Exception:
                print("caught")

        def main() -> None:
            check({"a": 1})

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split() == ["caught"], native.stdout


def test_zip_over_dyn_mapping_fails_closed(tmp_path):
    """Interim fail-closed contract: the dyn arg's mid-walk KeyError must
    not produce a result list holding NULL-slotted tuples."""
    native = _run_native(
        tmp_path,
        """
        def check(x: object) -> None:
            try:
                pairs = list(zip(("q",), x))
                print("returned")
                print(len(pairs))
            except Exception:
                print("caught")

        def main() -> None:
            check({"a": 1})

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split() == ["caught"], native.stdout


def test_dict_of_dyn_mapping_copies_like_cpython(tmp_path):
    """dict(x) where x dynamically holds a dict is a shallow copy in
    CPython.  The old pairs walk indexed the mapping positionally and
    silently corrupted; the tag dispatch routes it to py_dict_update."""
    native = _run_native(
        tmp_path,
        """
        def check(x: object) -> None:
            d2 = dict(x)
            print(len(d2))
            print(d2["a"])
            print(d2["b"])

        def main() -> None:
            check({"a": 1, "b": 2})

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split() == ["2", "1", "2"], native.stdout


def test_dict_of_dyn_pair_list_still_builds(tmp_path):
    """The pairs walk must keep working for a dyn-held list of pairs."""
    native = _run_native(
        tmp_path,
        """
        def check(x: object) -> None:
            d2 = dict(x)
            print(len(d2))
            print(d2["b"])

        def main() -> None:
            check([("a", 1), ("b", 2)])

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split() == ["2", "2"], native.stdout


def test_dict_of_dyn_set_fails_closed(tmp_path):
    """CPython accepts any iterable of pairs; the pcc pairs walk is
    positional and cannot index a set, so a dyn-held set fails closed
    with TypeError instead of silently returning an empty dict."""
    native = _run_native(
        tmp_path,
        """
        def check(x: object) -> None:
            try:
                d2 = dict(x)
                print("returned")
                print(len(d2))
            except Exception:
                print("caught")

        def main() -> None:
            check({1, 2})

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split() == ["caught"], native.stdout


def test_dict_of_dyn_non_pair_element_fails_closed(tmp_path):
    """py_obj_getitem returns NULL without raising for a non-subscriptable
    pair element; the walk previously fed NULL key/value into py_dict_set."""
    native = _run_native(
        tmp_path,
        """
        def check(x: object) -> None:
            try:
                d2 = dict(x)
                print("returned")
                print(len(d2))
            except Exception:
                print("caught")

        def main() -> None:
            check([("a", 1), 3])

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split() == ["caught"], native.stdout


def test_any_over_dyn_scalar_raises_typeerror(tmp_path):
    """CPython: any(5) is TypeError('int' object is not iterable).  The
    silent path was py_obj_len(int) -> 0 -> loop never entered -> False."""
    native = _run_native(
        tmp_path,
        """
        def check(x: object) -> None:
            try:
                r = any(x)
                print("returned")
                print(r)
            except Exception:
                print("caught")

        def main() -> None:
            check(5)

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split() == ["caught"], native.stdout


def test_zip_over_dyn_scalar_raises_typeerror(tmp_path):
    """CPython: zip(t, 5) is TypeError.  The silent path was
    py_obj_len(int) -> 0 -> min_len 0 -> empty result."""
    native = _run_native(
        tmp_path,
        """
        def check(x: object) -> None:
            try:
                pairs = list(zip((1, 2), x))
                print("returned")
                print(len(pairs))
            except Exception:
                print("caught")

        def main() -> None:
            check(5)

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split() == ["caught"], native.stdout


def test_zip_strict_unequal_raises_valueerror(tmp_path):
    """CPython: zip(strict=True) raises ValueError on unequal lengths.
    Both pcc paths silently truncated to the shortest input."""
    native = _run_native(
        tmp_path,
        """
        def main() -> None:
            try:
                pairs = list(zip((1, 2), (3,), strict=True))
                print("returned")
                print(len(pairs))
            except Exception as exc:
                print("caught")
                print(str(exc))

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    lines = native.stdout.split("\n")
    assert lines[0] == "caught", native.stdout
    assert "different lengths" in native.stdout, native.stdout


def test_for_zip_strict_unequal_raises_valueerror(tmp_path):
    """The for-loop zip rewrite dropped strict entirely; it must decline
    and route to the builtin, which enforces it."""
    native = _run_native(
        tmp_path,
        """
        def main() -> None:
            total = 0
            try:
                for a, b in zip((1, 2), (3,), strict=True):
                    total = total + a + b
                print("returned")
                print(total)
            except Exception as exc:
                print("caught")
                print(str(exc))

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split("\n")[0] == "caught", native.stdout


def test_zip_strict_equal_lengths_builds_all_pairs(tmp_path):
    native = _run_native(
        tmp_path,
        """
        def main() -> None:
            total = 0
            for a, b in zip((1, 2), (30, 40), strict=True):
                total = total + a + b
            print(total)

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split() == ["73"], native.stdout


def test_zip_without_strict_still_truncates(tmp_path):
    native = _run_native(
        tmp_path,
        """
        def main() -> None:
            pairs = list(zip((1, 2), (3,)))
            print(len(pairs))

        main()
        """,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.split() == ["1"], native.stdout


# ---------------------------------------------------------------- IR shape


def test_dict_pairs_walk_releases_loop_temps():
    """The pairs walk owns six NEW refs per iteration (pair, key, value,
    two index boxes, the loop index box); py_dict_set retains, so all six
    must be released on the normal path."""
    ir_text = _compile_to_ll(
        """
        def f(pairs: list) -> dict:
            return dict(pairs)
        """,
        "dict_pairs_walk_releases",
    )
    body = _function_body(ir_text, "f")
    assert body is not None
    assert body.count("@pcc_gc_release") >= 6, body.count("@pcc_gc_release")


def test_dict_copy_error_edges_release_owned_temps():
    """The two existing err checks in the dict-copy walk must release the
    owned keys view (and the loop temps on the first edge) before jumping
    to the error exit — i.e. they carry ``call.err.cleanup`` blocks."""
    ir_text = _compile_to_ll(
        """
        def f(d: dict) -> dict:
            return dict(d)
        """,
        "dict_copy_err_cleanup",
    )
    body = _function_body(ir_text, "f")
    assert body is not None
    assert "call.err.cleanup" in body, body
    # 3 normal-path releases (v, k, keys) + first-edge (v, k, keys) +
    # second-edge (keys) = at least 7 release sites in the walk.
    assert body.count("@pcc_gc_release") >= 7, body.count("@pcc_gc_release")


def test_zip_dyn_source_edges_check_and_release():
    """zip(static_dict, dyn): the dyn arg's len and getitem calls get
    fail-closed checks whose cleanup releases the owned keys view."""
    ir_text = _compile_to_ll(
        """
        def f(d: dict, x: object) -> object:
            return zip(d, x)
        """,
        "zip_dyn_err_cleanup",
    )
    body = _function_body(ir_text, "f")
    assert body is not None
    assert body.count("@py_err_occurred") >= 2, body
    assert "call.err.cleanup" in body, body
    assert "@pcc_gc_release" in body, body


def test_any_over_static_dict_gains_no_err_checks():
    """Cost guard: a source whose static type proves the walk cannot raise
    must not pay for the dyn fail-closed edges."""
    ir_text = _compile_to_ll(
        """
        def f(d: dict) -> bool:
            return any(d)
        """,
        "any_static_no_checks",
    )
    body = _function_body(ir_text, "f")
    assert body is not None
    assert "@py_err_occurred" not in body, body


def test_any_over_dyn_source_checks_len_and_getitem():
    """any(dyn) walks with fail-closed checks after len and getitem."""
    ir_text = _compile_to_ll(
        """
        def f(x: object) -> bool:
            return any(x)
        """,
        "any_dyn_checks",
    )
    body = _function_body(ir_text, "f")
    assert body is not None
    assert body.count("@py_err_occurred") >= 2, body
