"""Nested-def hoisting edge cases for the Python frontend."""
from __future__ import annotations

import subprocess
import textwrap


def test_hoisted_sibling_function_call_is_not_captured(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(
        textwrap.dedent(
            """
            class Checker:
                def valid(self, text: str) -> bool:
                    def is_alpha_code(c: int) -> bool:
                        return (97 <= c <= 122) or (65 <= c <= 90)

                    def is_alnum_code(c: int) -> bool:
                        return is_alpha_code(c) or (48 <= c <= 57)

                    for ch in text:
                        c = ord(ch)
                        if not (c == 95 or is_alnum_code(c)):
                            return False
                    return True

            def main() -> None:
                checker = Checker()
                print(checker.valid("abc_123"))
                print(checker.valid("abc-123"))

            if __name__ == "__main__":
                main()
            """
        ).lstrip()
    )
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
        backend="self",
    )
    run = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "True\nFalse\n"
