"""CPython parity for generator functions, ported from
``Lib/test/test_generators.py``.

Phase D2 in ``docs/issues/python-data-model-gaps.md`` originally flagged the
generator state machine as mostly absent. These tests are now live regression
coverage for the implemented generator/async subset.

All tests use ``libpython_mode="off"``.

Reference contract (from CPython):
  * ``def f(): ... yield x`` returns a generator that implements
    ``__iter__`` / ``__next__`` and preserves locals across yields
  * ``yield from`` (PEP 380) delegates to a sub-iterator and
    propagates ``StopIteration.value``
  * generator function bodies that mix ``yield`` and ``return value``
    raise ``StopIteration(value)`` at exit
  * ``contextlib.contextmanager`` is the load-bearing user of the
    generator protocol
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path


def _compile(monkeypatch, src: Path, exe: Path) -> None:
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "c")
    from pcc.py_frontend.pipeline import compile_python

    compile_python(
        str(src), str(exe),
        ir_scaffold_mode="on", libpython_mode="off",
    )


def _run(exe: Path, timeout: float = 30.0) -> str:
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=timeout,
    )
    assert result.returncode == 0, (
        f"{exe.name} exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout


def test_generator_simple_yield(tmp_path, monkeypatch):
    src = tmp_path / "gen_simple.py"
    exe = tmp_path / "gen_simple.out"
    src.write_text(textwrap.dedent("""
        def gen():
            yield 1
            yield 2
            yield 3

        def main() -> None:
            for v in gen():
                print(v)

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip().splitlines() == ["1", "2", "3"]

def test_generator_state_persists_across_yields(tmp_path, monkeypatch):
    src = tmp_path / "gen_state.py"
    exe = tmp_path / "gen_state.out"
    src.write_text(textwrap.dedent("""
        def fibs(n: int):
            a = 0
            b = 1
            i = 0
            while i < n:
                yield a
                t = a + b
                a = b
                b = t
                i = i + 1

        def main() -> None:
            for v in fibs(7):
                print(v)

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip().splitlines() == [
        "0", "1", "1", "2", "3", "5", "8",
    ]

def test_generator_yield_from(tmp_path, monkeypatch):
    src = tmp_path / "gen_yieldfrom.py"
    exe = tmp_path / "gen_yieldfrom.out"
    src.write_text(textwrap.dedent("""
        def inner():
            yield 1
            yield 2

        def outer():
            yield 0
            yield from inner()
            yield 3

        def main() -> None:
            for v in outer():
                print(v)

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip().splitlines() == ["0", "1", "2", "3"]

def test_generator_explicit_next_calls(tmp_path, monkeypatch):
    src = tmp_path / "gen_next.py"
    exe = tmp_path / "gen_next.out"
    src.write_text(textwrap.dedent("""
        def gen():
            yield 10
            yield 20
            yield 30

        def main() -> None:
            g = gen()
            print(next(g))
            print(next(g))
            print(next(g))

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip().splitlines() == ["10", "20", "30"]


def test_generator_yields_implicit_tuple(tmp_path, monkeypatch):
    # Regression: ``yield a, b`` must yield the tuple ``(a, b)`` (CPython
    # semantics), not parse as ``(yield a), b``.  The mis-parse left the
    # second element in an enclosing tuple whose ``_yield`` sentinel leaked
    # to runtime as ``NameError: name '_yield' is not defined``.  This shape
    # is used by real generators such as numpy's
    # ``distutils.misc_util.general_source_directories_files`` (``yield
    # rpath, files``).
    src = tmp_path / "gen_tuple.py"
    exe = tmp_path / "gen_tuple.out"
    src.write_text(textwrap.dedent("""
        def gen():
            yield 1, 2
            yield 3, 4

        def pairs(items):
            for a, b in items:
                yield a, b

        def main() -> None:
            for x in gen():
                print(x)
            for y in pairs([(5, 6), (7, 8)]):
                print(y)

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip().splitlines() == [
        "(1, 2)", "(3, 4)", "(5, 6)", "(7, 8)",
    ]


def test_generator_range_loop_resumes(tmp_path, monkeypatch):
    # Regression: ``for i in range(n): ... yield`` inside a generator must
    # resume across yields and produce every item.  The inline range
    # fast path kept its loop counter in a raw entry-block alloca that is
    # NOT part of the persisted generator frame, so after the first yield
    # the resume re-entered with a reset counter and the generator stopped
    # after one item.  range() inside a generator now materialises a list
    # and drives the resumable object-iterator path.  range-driven
    # generators are extremely common (and on the numpy generator path).
    src = tmp_path / "gen_range.py"
    exe = tmp_path / "gen_range.out"
    src.write_text(textwrap.dedent("""
        def count(n: int):
            for i in range(n):
                yield i + 100

        def pairs(n: int):
            for i in range(n):
                yield i, i * i

        def stepped():
            for j in range(2, 11, 3):
                yield j

        def main() -> None:
            for v in count(3):
                print(v)
            for a, b in pairs(3):
                print(a, b)
            for k in stepped():
                print(k)

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip().splitlines() == [
        "100", "101", "102",
        "0 0", "1 1", "2 4",
        "2", "5", "8",
    ]


def test_generator_enumerate_resumes(tmp_path, monkeypatch):
    # Regression: ``for idx, v in enumerate(xs): ... yield`` inside a
    # generator.  enumerate desugars to ``for v in xs`` (resumable) plus a
    # synthetic running counter ``__enum_i``.  That counter was a raw
    # entry-block alloca created during _emit_for, AFTER generator frame
    # collection walked the original AST, so it was not persisted: in a
    # boxed-int generator the slot reloaded as NULL on resume and the index
    # came back as ``<null>`` after the first item (values resumed, index did
    # not).  The counter now gets a deterministic span-keyed frame slot
    # (__pcc_enum_cnt_<line>_<col>) reserved at frame-collection time.
    # (String elements: int-list iteration is independently broken in the
    # current worktree by unrelated uncommitted edits.)
    src = tmp_path / "gen_enum.py"
    exe = tmp_path / "gen_enum.out"
    src.write_text(textwrap.dedent("""
        def walk(xs):
            for idx, v in enumerate(xs):
                yield idx, v

        def walk_start(xs):
            for idx, v in enumerate(xs, 1):
                yield idx, v

        def walk_pair(xs):
            for pair in enumerate(xs):
                yield pair

        def main() -> None:
            for i, val in walk(["a", "b", "c"]):
                print(i, val)
            for i, val in walk_start(["x", "y"]):
                print(i, val)
            for p in walk_pair(["m", "n"]):
                print(p)

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip().splitlines() == [
        "0 a", "1 b", "2 c",
        "1 x", "2 y",
        "(0, 'm')", "(1, 'n')",
    ]


def test_generator_sibling_owned_flag_isolation(tmp_path, monkeypatch):
    # Regression: two sibling generator functions that share a local name
    # whose owned-reference flag is reassigned (a PyStr local repeatedly
    # rebound, here ``pruned``) must each emit their OWN flag alloca.  The
    # ``_owned_local_flag_slots`` cache in L1CodeGen leaked across user
    # functions only on the generator path because user_function_lowering's
    # generator branch returned BEFORE the normal-function reset; the cache
    # was restored by reference in ``finally`` but the dict had been mutated
    # in place, so each generator emission inherited the previous one's
    # entries.  The second generator's resume function then referenced the
    # first generator's flag alloca (undefined in the second function),
    # producing IR that the self backend rejected with
    # ``BackendUnavailable: self backend expected pointer value
    # 'pruned.owned.<N>'``.  This was the actual cap on the numpy auto-mode
    # diagnostic at 149 IR modules
    # (numpy.distutils.misc_util.general_source_files /
    # general_source_directories_files both carry a ``pruned_directories``
    # local).
    src = tmp_path / "gen_sib.py"
    exe = tmp_path / "gen_sib.out"
    src.write_text(textwrap.dedent("""
        def gen_a():
            pruned = "a-init"
            for i in range(2):
                pruned = "a" + str(i)
                yield pruned

        def gen_b():
            pruned = "b-init"
            for i in range(2):
                pruned = "b" + str(i)
                yield pruned

        def main() -> None:
            for v in gen_a():
                print(v)
            for v in gen_b():
                print(v)

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip().splitlines() == [
        "a0", "a1",
        "b0", "b1",
    ]


def test_async_def_basic(tmp_path, monkeypatch):
    src = tmp_path / "async_basic.py"
    exe = tmp_path / "async_basic.out"
    src.write_text(textwrap.dedent("""
        import asyncio

        async def hello() -> str:
            return "hi"

        def main() -> None:
            print(asyncio.run(hello()))

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip() == "hi"
