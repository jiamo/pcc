from __future__ import annotations

import sys

import pytest

from pcc.tools.ir_reduce import (
    InterestingnessContract,
    InterestingnessRunner,
    ReductionError,
    reduce_ir_text,
)


HISTORICAL_MISCOMPILE_SHAPE = """\
; dead declarations and helpers model a previously hand-reduced miscompile.
define i32 @dead_helper(i32 %x) {
entry:
  %a = add i32 %x, 1
  ret i32 %a
}

define i32 @bug(i32 %x) {
entry:
  %noise = add i32 %x, 0
  br label %bad
bad:
  %marker = sdiv i32 -2147483648, -1
  ret i32 %marker
unused:
  ret i32 7
}

define i32 @more_dead() {
entry:
  ret i32 99
}
"""


class _Predicate:
    def __init__(self):
        self.attempts = 0

    def __call__(self, text: str) -> bool:
        self.attempts += 1
        return (
            "define i32 @bug" in text
            and "sdiv i32 -2147483648, -1" in text
        )


def test_function_block_instruction_reduction_keeps_historical_marker():
    predicate = _Predicate()
    result = reduce_ir_text(HISTORICAL_MISCOMPILE_SHAPE, predicate)
    assert "@dead_helper" not in result.text
    assert "@more_dead" not in result.text
    assert "sdiv i32 -2147483648, -1" in result.text
    assert result.reduced_bytes < result.original_bytes
    assert result.attempts == predicate.attempts
    assert dict(result.phase_accepts)["function"] >= 1


def test_protected_function_is_not_offered_to_function_reduction():
    predicate = _Predicate()
    result = reduce_ir_text(
        HISTORICAL_MISCOMPILE_SHAPE,
        predicate,
        keep_functions=("dead_helper",),
    )
    assert "define i32 @dead_helper" in result.text
    assert "define i32 @bug" in result.text


def test_baseline_must_be_interesting():
    with pytest.raises(ReductionError, match="baseline input"):
        reduce_ir_text("define void @f() {\n  ret void\n}\n", lambda _text: False)


def test_command_contract_binds_exit_stdout_stderr_and_input(tmp_path):
    script = tmp_path / "interesting.py"
    script.write_text(
        "import pathlib, sys\n"
        "data = pathlib.Path(sys.argv[1]).read_text()\n"
        "sys.stdout.write('interesting\\n')\n"
        "sys.stderr.write('marker\\n')\n"
        "raise SystemExit(7 if 'needle' in data else 3)\n"
    )
    runner = InterestingnessRunner(
        InterestingnessContract(
            command=(sys.executable, str(script), "{input}"),
            timeout_s=2.0,
            expected_exit=7,
            expected_stdout=b"interesting\n",
            expected_stderr=b"marker\n",
        )
    )
    assert runner("; needle\n")
    assert not runner("; absent\n")


def test_timeout_is_an_uninteresting_candidate(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n")
    runner = InterestingnessRunner(
        InterestingnessContract(
            command=(sys.executable, str(script), "{input}"),
            timeout_s=0.01,
        )
    )
    assert not runner("; candidate\n")
