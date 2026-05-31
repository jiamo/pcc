"""Self-host smoke: invoke the compiled ``pcc1`` binary to compile and
then run small Python programs.

The other ``test_python_*_parity.py`` files import
``pcc.py_frontend.pipeline.compile_python`` and call it from host CPython.
That tests "CPython hosting pcc the library". This file tests the
stricter contract: **the bootstrapped ``pcc1`` binary** (a
no-libpython native executable) compiles ``.py`` programs into native
binaries, and those binaries produce the expected output.

Failure to run any of these means the pcc1 binary itself is broken or
its native subset doesn't cover the language feature exercised. That
is a regression different from anything the host-pcc parity tests
catch.

This file is skipped when no pcc1 binary is on disk; it never
triggers a heavy bootstrap rebuild.
"""

from __future__ import annotations

import os
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).absolute().parents[2]
_PCC1_CANDIDATES = [
    REPO / "build" / "bootstrap-pytest-self" / "pcc1",
    REPO / "build" / "bootstrap" / "pcc1",
    REPO / "build" / "bootstrap-self-claude" / "pcc1",
    REPO / "build" / "bootstrap-llvm-claude" / "pcc1",
    REPO / "build" / "bootstrap-strict-self" / "pcc1",
    REPO / "build" / "bootstrap-self-darwin_arm64" / "pcc1",
    REPO / "build" / "bootstrap-llvm-darwin_arm64" / "pcc1",
]


def _find_pcc1() -> Path | None:
    env_path = os.environ.get("PCC1_BINARY")
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file():
            return p
    for p in _PCC1_CANDIDATES:
        if p.exists() and p.is_file():
            return p
    return None


def _pcc1_supports_pytest() -> bool:
    if PCC1 is None:
        return False
    result = subprocess.run(
        [str(PCC1), "--help"],
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    return result.returncode == 0 and "--pytest" in result.stdout


PCC1 = _find_pcc1()
pytestmark = pytest.mark.skipif(
    PCC1 is None,
    reason=(
        "No pcc1 binary found on disk; skipping self-host smoke. "
        "Run scripts/bootstrap.sh to build one."
    ),
)

@pytest.fixture(scope="module", autouse=True)
def _smoke_pcc_py_runtime(pcc_py_runtime_archive):
    """Build the pcc-Python runtime archive pcc1 links before the smoke tests.

    The build/check lives in the shared ``pcc_py_runtime_archive`` fixture
    (tests/python/conftest.py). Without it, a tree missing
    ``libpy_runtime_pcc_py.a`` makes every smoke test fail the final link with
    ``Undefined symbols: _py_list_append, ...`` — a build-environment artifact,
    not a pcc1 codegen regression.
    """
    return pcc_py_runtime_archive


def _compile_and_run(
    tmp_path: Path,
    src_text: str,
    *,
    timeout: float = 30.0,
    libpython_mode: str = "off",
    compile_env: dict[str, str] | None = None,
) -> str:
    """Use the compiled pcc1 binary to compile ``src_text`` to a native
    executable, run it, and return its stdout.

    Asserts that the compile succeeded and that the run exited 0.
    """
    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent(src_text).lstrip())

    compile_cmd = [
        str(PCC1),
        str(src),
        "-o",
        str(exe),
        f"--python-libpython={libpython_mode}",
        "--ir-scaffold=on",
    ]
    compile_proc = subprocess.run(
        compile_cmd,
        capture_output=True,
        text=True,
        timeout=120.0,
        env={**os.environ, **(compile_env or {})},
    )
    assert compile_proc.returncode == 0, (
        f"pcc1 compile failed (exit {compile_proc.returncode}):\n"
        f"cmd: {' '.join(compile_cmd)}\n"
        f"stdout:\n{compile_proc.stdout}\n"
        f"stderr:\n{compile_proc.stderr}"
    )
    assert exe.exists(), f"pcc1 produced no binary at {exe}"

    run_proc = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert run_proc.returncode == 0, (
        f"pcc1-built binary exited {run_proc.returncode}\n"
        f"stdout:\n{run_proc.stdout}\nstderr:\n{run_proc.stderr}"
    )
    return run_proc.stdout


def test_pcc1_help_lists_bootstrap_cli_options():
    result = subprocess.run(
        [str(PCC1), "--help"],
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    assert result.returncode == 0, result.stderr
    for option in (
        "--backend",
        "--python-libpython",
        "--python-library",
        "--ir-scaffold",
        "--emit-llvm",
        "--profile-json",
        "--pass",
        "--disable-pass",
        "--diagnostic-format",
        "--pytest",
    ):
        assert option in result.stdout
    assert "C/project inputs are" in result.stdout


def test_pcc1_accepts_host_pass_cli_for_python_inputs(tmp_path):
    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(
        textwrap.dedent("""
            def main() -> None:
                print("pass cli")

            if __name__ == "__main__":
                main()
            """).lstrip(),
        encoding="utf-8",
    )

    compile_cmd = [
        str(PCC1),
        "--backend",
        "self",
        "--python-libpython=off",
        "--ir-scaffold=on",
        "--pass",
        "called-value-prop",
        "--disable-pass=called-value-prop",
        str(src),
        "-o",
        str(exe),
    ]
    compile_proc = subprocess.run(
        compile_cmd,
        capture_output=True,
        text=True,
        timeout=120.0,
    )
    assert compile_proc.returncode == 0, (
        f"pcc1 compile failed (exit {compile_proc.returncode}):\n"
        f"cmd: {' '.join(compile_cmd)}\n"
        f"stdout:\n{compile_proc.stdout}\n"
        f"stderr:\n{compile_proc.stderr}"
    )
    run_proc = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    assert run_proc.returncode == 0, run_proc.stderr
    assert run_proc.stdout == "pass cli\n"


def test_pcc1_python_library_emit_llvm_cli(tmp_path):
    src = tmp_path / "libmod.py"
    ll = tmp_path / "libmod.ll"
    src.write_text(
        textwrap.dedent("""
            def exported() -> int:
                return 7
            """).lstrip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(PCC1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            "--python-library",
            "--emit-llvm=" + str(ll),
            str(src),
        ],
        capture_output=True,
        text=True,
        timeout=120.0,
    )
    assert result.returncode == 0, result.stderr
    ir_text = ll.read_text(encoding="utf-8")
    assert "define i32 @main(" not in ir_text
    assert "@_pcc_py_module_top_libmod" in ir_text


def test_pcc1_delegates_c_input_to_host_pcc(tmp_path):
    src = tmp_path / "hello.c"
    ll = tmp_path / "hello.ll"
    src.write_text(
        "int main(void) { return PCC1_DELEGATED_VALUE == 7 ? 0 : 1; }\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(PCC1),
            "--cpp-arg=-DPCC1_DELEGATED_VALUE=7",
            "--emit-llvm=" + str(ll),
            str(src),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120.0,
    )
    assert (
        result.returncode == 0
    ), f"pcc1 C delegation failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert ll.exists()


def test_pcc1_delegates_c_directory_input_to_host_pcc(tmp_path):
    project = tmp_path / "c_project"
    project.mkdir()
    ll = tmp_path / "project.ll"
    (project / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

    result = subprocess.run(
        [
            str(PCC1),
            "--no-cache",
            "--emit-llvm=" + str(ll),
            str(project),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120.0,
    )
    assert (
        result.returncode == 0
    ), f"pcc1 C project delegation failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert ll.exists()


def test_pcc1_emit_llvm_and_profile_json_cli(tmp_path):
    src = tmp_path / "prog.py"
    ll = tmp_path / "prog.ll"
    profile = tmp_path / "profile.json"
    src.write_text(
        textwrap.dedent("""
            def main() -> None:
                print("profile")

            if __name__ == "__main__":
                main()
            """).lstrip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(PCC1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            "--emit-llvm=" + str(ll),
            "--profile-json",
            str(profile),
            str(src),
        ],
        capture_output=True,
        text=True,
        timeout=120.0,
    )
    assert result.returncode == 0, result.stderr
    assert ll.exists()
    assert profile.exists()
    data = json.loads(profile.read_text(encoding="utf-8"))
    assert data["schema"] == "pcc.profile.v1"
    assert data["metadata"]["emit_llvm"] is True
    assert data["metadata"]["time_unit"] == "seconds"
    assert "phase_totals_s" in data
    assert data["total_ms"] > 0
    assert any(v > 0 for v in data["phase_totals_ms"].values())


def test_pcc1_diagnostic_format_json_on_compile_error(tmp_path):
    # Use a SYNTAX error (which pcc always rejects at the parser
    # stage) rather than the historical undefined-name probe
    # (``print(missing_name)``).  pcc since stopped statically
    # rejecting undefined references and now defers them to
    # runtime NameError — matching CPython's Python-semantic
    # behavior.  The JSON-diagnostic contract is still exercised
    # by any compile-time-rejected input; a malformed funcdef
    # gives a stable PCC-PY-COMPILE-001 + python-frontend phase.
    src = tmp_path / "bad.py"
    out = tmp_path / "bad.out"
    src.write_text(
        textwrap.dedent("""
            def main(:
                pass
            """).lstrip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(PCC1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            "--diagnostic-format=json",
            str(src),
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=120.0,
    )
    assert result.returncode != 0
    data = json.loads(result.stderr)
    assert data["schema"] == "pcc.diagnostics.v1"
    assert data["has_errors"] is True
    diag = data["diagnostics"][0]
    assert diag["code"] == "PCC-PY-COMPILE-001"
    assert diag["phase"] == "python-frontend"
    assert "ParseError" in diag["message"]
    assert not out.exists()


def test_pcc1_smoke_hello_arithmetic(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        def main() -> None:
            print("hello from pcc1")
            x = 0
            i = 0
            while i < 5:
                x = x + i
                i = i + 1
            print(x)

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip().splitlines() == ["hello from pcc1", "10"]


def test_pcc1_accepts_bare_return_from_none_nested_function(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        def outer() -> None:
            def visit(name: str) -> None:
                if name == "":
                    return
                print(name)

            visit("")
            visit("ok")

        if __name__ == "__main__":
            outer()
        """,
    )
    assert out.strip() == "ok"


def test_pcc1_formats_string_fields_inside_instance_method(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        class Namer:
            def __init__(self) -> None:
                self.index = 0

            def fresh(self, hint: str) -> str:
                self.index += 1
                return f"{hint}.{self.index}"

        def main() -> None:
            n = Namer()
            print(n.fresh("inst.ObservabilityOptions"))

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip() == "inst.ObservabilityOptions.1"


def test_pcc1_compiles_keyword_class_ctor_inside_try(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        class Options:
            def __init__(
                self,
                diagnostic_format: str = "text",
                profile_json=None,
                explain_fallback: bool = False,
                phase: str = "compile",
                entry: str = "pcc",
            ) -> None:
                self.diagnostic_format = diagnostic_format
                self.profile_json = profile_json
                self.explain_fallback = explain_fallback
                self.phase = phase
                self.entry = entry

        def main() -> None:
            try:
                options = Options(
                    diagnostic_format="json",
                    profile_json=None,
                    explain_fallback=False,
                    phase="python-frontend",
                    entry="cli_bootstrap",
                )
            except ValueError as exc:
                print(str(exc))
                return
            print(options.phase)
            print(options.entry)

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip().splitlines() == ["python-frontend", "cli_bootstrap"]


def test_pcc1_pcc_python_runtime_keeps_none_ctor_arg_visible_to_generic_attr(
    tmp_path,
):
    out = _compile_and_run(
        tmp_path,
        """
        class Options:
            def __init__(self, profile_json=None, phase: str = "compile") -> None:
                self.profile_json = profile_json
                self.phase = phase

        def parse():
            path = "x"
            profile_json = None
            explain = False
            return ((path, profile_json, explain), 0, None)

        def check(options) -> None:
            if options.profile_json:
                print("profile")
            else:
                print("none")
            print(options.phase)

        def main() -> None:
            parsed, exit_code, error = parse()
            (path, profile_json, explain) = parsed
            profile_json = None if profile_json is None else (profile_json or "") + ""
            options = Options(profile_json=profile_json, phase="python-frontend")
            check(options)

        if __name__ == "__main__":
            main()
        """,
        compile_env={"PCC_RUNTIME_CC": "pcc", "PCC_RUNTIME_HIGH": "py"},
    )
    assert out.strip().splitlines() == ["none", "python-frontend"]


def test_pcc1_pcc_python_runtime_keeps_local_attr_constructor_args(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        class Token:
            def __init__(self, text: str, line: int) -> None:
                self.text = text
                self.line = line

        class Name:
            def __init__(self, ident: str, line: int) -> None:
                self.ident = ident
                self.line = line

        class Parser:
            def _peek(self) -> Token:
                return Token("int", 1)

            def make(self) -> Name:
                t = self._peek()
                return Name(ident=t.text, line=t.line)

        def main() -> None:
            n = Parser().make()
            print(n.ident)
            print(n.line)

        if __name__ == "__main__":
            main()
        """,
        compile_env={"PCC_RUNTIME_CC": "pcc", "PCC_RUNTIME_HIGH": "py"},
    )
    assert out.strip().splitlines() == ["int", "1"]


def test_pcc1_pcc_python_runtime_keeps_py_lex_token_constructor_args(tmp_path):
    ll_out = tmp_path / "py_lex.ll"
    compile_cmd = [
        str(PCC1),
        "--emit-llvm",
        "--python-libpython=off",
        "--ir-scaffold=on",
        str(REPO / "pcc" / "parse" / "py_lex.py"),
        "-o",
        str(ll_out),
    ]
    compile_proc = subprocess.run(
        compile_cmd,
        capture_output=True,
        text=True,
        timeout=120.0,
        env={
            **os.environ,
            "PCC_RUNTIME_CC": "pcc",
            "PCC_RUNTIME_HIGH": "py",
        },
    )
    assert compile_proc.returncode == 0, (
        f"pcc1 py_lex compile failed:\nstdout:\n{compile_proc.stdout}\n"
        f"stderr:\n{compile_proc.stderr}"
    )
    ir_text = ll_out.read_text()
    for line in ir_text.splitlines():
        if "Token___init__" in line:
            assert "ptr null" not in line
            assert "i64 null" not in line


def test_pcc1_generator_expression_fstring_join(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        class Arg:
            def __init__(self, type: str, text: str) -> None:
                self.type = type
                self.text = text

            def __str__(self) -> str:
                return self.text

        def main() -> None:
            args = [Arg("i64", "%a"), Arg("ptr", "%b")]
            print(", ".join(f"{a.type} {a}" for a in args))

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip() == "i64 %a, ptr %b"


def test_pcc1_getattr_default_present_and_missing(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        class Info:
            def __init__(self) -> None:
                self.init_fn = "ready"

        def main() -> None:
            info = Info()
            missing = getattr(info, "missing", None)
            if missing is None:
                print("missing")
            else:
                print("bad")
            print(getattr(info, "init_fn", None))

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip().splitlines() == ["missing", "ready"]


def test_pcc1_accepts_optional_str_return_none(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        from typing import Optional

        def maybe(flag: bool) -> Optional[str]:
            if flag:
                return "value"
            return None

        def main() -> None:
            value = maybe(False)
            if value is None:
                print("none")
            else:
                print(value)

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip() == "none"


def test_pcc1_short_circuit_keeps_operand_values(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        def classify(args, kwargs) -> None:
            if args or kwargs:
                print("nonempty")
            else:
                print("empty")

        def main() -> None:
            classify([], {})
            classify(["x"], {})
            classify([], {"k": "v"})
            print("" or "right")
            print("left" and "right")

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip().splitlines() == [
        "empty",
        "nonempty",
        "nonempty",
        "right",
        "right",
    ]


def test_pcc1_string_concat_and_false_bool_ctor_arg(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        class Flag:
            def __init__(self, enabled: bool = False) -> None:
                self.enabled = enabled

        def main() -> None:
            disabled = Flag(False)
            enabled = Flag(True)
            print("flag:" + ("on" if enabled.enabled else "off"))
            print("flag:" + ("on" if disabled.enabled else "off"))

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip().splitlines() == ["flag:on", "flag:off"]


def test_pcc1_calls_positional_constructor_before_method_use(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        class Parser:
            def __init__(self, src: str, filename: str = "<input>") -> None:
                self.src = src
                self.filename = filename
                self.pos = 0

            def peek(self, off: int = 0) -> int:
                return self.pos + off

        def parse(src: str, filename: str) -> int:
            parser = Parser(src, filename)
            return parser.peek(2)

        def main() -> None:
            print(parse("x", "y"))

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip() == "2"


def test_pcc1_can_launch_pytest_for_test_directory(tmp_path):
    if not _pcc1_supports_pytest():
        pytest.skip("pcc1 binary predates --pytest launcher support")

    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_sample.py").write_text(
        "def test_sample():\n" "    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(PCC1), "--pytest", str(test_dir), "-q", "-n0"],
        capture_output=True,
        text=True,
        timeout=60.0,
    )
    assert (
        result.returncode == 0
    ), f"pcc1 --pytest failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_pcc1_compiles_and_runs_pytest_style_assertions(tmp_path):
    """pcc1 compiles a pytest-style ``.py`` (def test_*() + ``assert``)
    into a native binary; running the binary actually executes the
    asserts inside pcc-native code. Unlike the ``--pytest`` launcher
    test above which forks host CPython's pytest, this test exercises
    pcc1's own compile-and-run path on assertion logic.

    Failure modes this catches:
    - assert lowering broken
    - method calls on str/list inside test functions broken
    - exception path on assertion failure unreachable
    - test discovery loop (calling each ``test_*`` from main) regressed
    """
    src_text = """
        # pytest-style test file. No pytest framework needed: a tiny
        # main() runner calls each ``test_*`` and aborts on the first
        # AssertionError. This is what pytest reduces to once you
        # strip discovery + reporting.

        def test_int_arithmetic() -> None:
            assert 1 + 1 == 2
            assert 7 - 3 == 4
            assert 6 * 7 == 42
            assert 10 // 3 == 3
            assert 10 % 3 == 1

        def test_string_methods() -> None:
            assert "hello".upper() == "HELLO"
            assert "WORLD".lower() == "world"
            assert "  spaced  ".strip() == "spaced"
            assert "a,b,c".split(",") == ["a", "b", "c"]
            assert "-".join(["x", "y", "z"]) == "x-y-z"

        def test_list_and_dict() -> None:
            xs = [1, 2, 3]
            xs.append(4)
            assert len(xs) == 4
            assert xs[-1] == 4
            assert sum(xs) == 10

            d = {"a": 1, "b": 2}
            d["c"] = 3
            assert len(d) == 3
            assert d["b"] == 2

        def test_assertion_failure_actually_raises() -> None:
            # Confirm the assert path triggers AssertionError when the
            # condition is False. Without this, the ``all passed`` line
            # below would lie if asserts silently no-op'd.
            raised = False
            try:
                assert 1 == 2
            except AssertionError:
                raised = True
            assert raised

        def main() -> None:
            test_int_arithmetic()
            test_string_methods()
            test_list_and_dict()
            test_assertion_failure_actually_raises()
            print("all passed")

        if __name__ == "__main__":
            main()
    """
    out = _compile_and_run(tmp_path, src_text)
    assert out.strip() == "all passed", (
        f"pcc1-compiled pytest-style file produced unexpected output: " f"{out!r}"
    )


def test_pcc1_smoke_class_and_method(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        class P:
            def __init__(self, x: int, y: int) -> None:
                self.x = x
                self.y = y

            def magnitude_squared(self) -> int:
                return self.x * self.x + self.y * self.y

        def main() -> None:
            p = P(3, 4)
            print(p.magnitude_squared())

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip() == "25"


def test_pcc1_smoke_generator(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
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
        """,
    )
    assert out.strip().splitlines() == ["0", "1", "1", "2", "3", "5", "8"]


def test_pcc1_smoke_exception_handling(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        def main() -> None:
            try:
                raise ValueError("boom")
            except ValueError as e:
                print(str(e))
            print("after")

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip().splitlines() == ["boom", "after"]


def test_pcc1_smoke_list_comprehension(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        def main() -> None:
            sq = [x * x for x in range(5)]
            print(sq[0], sq[1], sq[2], sq[3], sq[4])

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip() == "0 1 4 9 16"


def test_pcc1_smoke_walrus_expression(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        def main() -> None:
            values = [1, 2, 3]
            if (n := len(values)) > 2:
                print(n)
            print(n + 4)

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip().splitlines() == ["3", "7"]


def test_pcc1_smoke_integer_bitwise_and_shifts(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        def main() -> None:
            c2 = -61 & 255
            c3 = -87 & 255
            c4 = -16 & 255
            c5 = -97 & 255
            c6 = -104 & 255
            c7 = -128 & 255
            print((c2 & 224) == 192)
            print((c3 & 192) == 128)
            print(((c2 & 31) << 6) | (c3 & 63))
            print(
                ((c4 & 7) << 18)
                | ((c5 & 63) << 12)
                | ((c6 & 63) << 6)
                | (c7 & 63)
            )

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip().splitlines() == ["True", "True", "233", "128512"]


def test_pcc1_smoke_fstring_ascii_conversion(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        def main() -> None:
            value = "\\u00e9"
            face = chr(0x1f600)
            print(f"{value!a}")
            print(f"{face!a}")

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip().splitlines() == ["'\\xe9'", "'\\U0001f600'"]


def test_pcc1_smoke_async_await_asyncio_run(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        import asyncio

        async def slow(n: int) -> int:
            await asyncio.sleep(0)
            return n * 2

        async def main_async() -> None:
            a = await slow(5)
            b = await slow(7)
            print(a + b)

        def main() -> None:
            asyncio.run(main_async())

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip() == "24"


def test_pcc1_smoke_str_methods(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        def main() -> None:
            s = "hello world"
            print(s.upper())
            print(s.split()[1])
            print(len(s))
            print("abc" * 3)

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip().splitlines() == ["HELLO WORLD", "world", "11", "abcabcabc"]


@pytest.mark.xfail(
    reason=(
        "Older pcc1 binaries (built before recent set / list / dict "
        "native-method coverage) treat the combined ``set`` literal + "
        "``add`` / ``len`` surface as needing libpython. Drops once "
        "pcc1 is rebuilt against post-2026-05-08 codegen."
    ),
    strict=False,
)
def test_pcc1_smoke_list_dict_set(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        def main() -> None:
            xs = [1, 2, 3]
            xs.append(4)
            print(len(xs), xs[0], xs[3])

            d: dict = {"a": 1, "b": 2}
            d["c"] = 3
            print(d["b"], len(d))

            s: set = {1, 2, 3}
            s.add(4)
            s.add(2)
            print(len(s))

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip().splitlines() == ["4 1 4", "2 3", "4"]


def test_pcc1_smoke_inheritance_super(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        class Animal:
            def __init__(self, name: str) -> None:
                self.name = name

            def greeting(self) -> str:
                return "I am " + self.name

        class Dog(Animal):
            def __init__(self, name: str, breed: str) -> None:
                super().__init__(name)
                self.breed = breed

            def greeting(self) -> str:
                return super().greeting() + ", a " + self.breed

        def main() -> None:
            d = Dog("Rex", "lab")
            print(d.greeting())

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip() == "I am Rex, a lab"


def test_pcc1_smoke_json_loads(tmp_path):
    out = _compile_and_run(
        tmp_path,
        """
        import json

        def main() -> None:
            d = json.loads("{\\\"a\\\": 1, \\\"b\\\": 2}")
            print(d["a"], d["b"])

        if __name__ == "__main__":
            main()
        """,
    )
    assert out.strip() == "1 2"


def test_pcc1_smoke_threading_basic_lock(tmp_path):
    """4 threads contended on one Lock; final counter must be 4000.
    This validates the runtime threading + lock mutual-exclusion path
    in a binary built by pcc1 itself.
    """
    out = _compile_and_run(
        tmp_path,
        """
        from threading import Lock, Thread

        counts = [0]
        lock = Lock()

        def worker() -> None:
            i = 0
            while i < 1000:
                lock.acquire()
                counts[0] = counts[0] + 1
                lock.release()
                i = i + 1

        def main() -> None:
            t0 = Thread(target=worker)
            t1 = Thread(target=worker)
            t2 = Thread(target=worker)
            t3 = Thread(target=worker)
            t0.start(); t1.start(); t2.start(); t3.start()
            t0.join(); t1.join(); t2.join(); t3.join()
            print(counts[0])

        if __name__ == "__main__":
            main()
        """,
    )
    # NOTE: the pcc1 binary on this machine was built before the
    # 2026-05-08 list-indexed-Lock fix; the contended counter still
    # serializes correctly in this case because we use a single
    # global ``lock`` (not list-indexed).
    assert out.strip() == "4000"
