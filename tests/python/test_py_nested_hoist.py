"""Nested-def hoisting edge cases for the Python frontend."""
from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path


_REPO_ROOT = Path(__file__).absolute().parents[2]
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)


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


def _compile_to_ll(source: str, name: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source)
    compile_python(
        str(src),
        str(out),
        emit_llvm_only=True,
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    return out.read_text()


def _read_key_value_profile(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for raw in path.read_text().splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = int(value.strip())
    return values


def test_nested_hoist_free_name_analysis_is_cached(monkeypatch, tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    profile = tmp_path / "hoist.profile"
    monkeypatch.setenv("PCC_HOIST_PROFILE_PATH", str(profile))
    src = tmp_path / "nested_cache_probe.py"
    out = tmp_path / "nested_cache_probe.ll"
    src.write_text(
        textwrap.dedent(
            """
            def outer(seed: int) -> int:
                captured = seed

                def leaf(v: int) -> int:
                    return captured + v

                def f0(v: int) -> int:
                    return leaf(v)

                def f1(v: int) -> int:
                    return f0(v) + leaf(v)

                def f2(v: int) -> int:
                    return f1(v) + f0(v)

                def f3(v: int) -> int:
                    return f2(v) + f1(v)

                def f4(v: int) -> int:
                    return f3(v) + f2(v)

                return f4(1)
            """
        ).lstrip()
    )

    compile_python(
        str(src),
        str(out),
        emit_llvm_only=True,
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    assert profile.exists()
    stats = _read_key_value_profile(profile)
    assert stats["compute_free_names_cache_hits"] > 0
    assert stats["called_sibling_names_cache_hits"] > 0
    assert stats["referenced_sibling_names_cache_hits"] > 0
    assert stats["sibling_effective_free_names_calls"] <= 6


def test_value_position_nested_capture_propagates_through_sibling_cycle():
    program = textwrap.dedent(
        """
        class Dom:
            def dominates(self, a: str, b: str) -> bool:
                return a == b

        def outer(dom: Dom) -> tuple[str, str] | None:
            cfg = {"x": ("a", "b")}
            pred_order_by_block = {"x": {"a": 0, "b": 1}}

            def sort_preds(
                block: str,
                pred_infos: dict[str, tuple[str, str]],
            ) -> tuple[str, ...]:
                preds = tuple(cfg.get(block, ()))
                pred_order = pred_order_by_block.get(block, {})
                local_preds = {
                    pred
                    for pred, (_, kind) in pred_infos.items()
                    if kind == "local"
                }

                def key(pred: str) -> tuple[int, int, int]:
                    flag = any(
                        pred != other
                        and other in local_preds
                        and dom.dominates(other, pred)
                        for other in local_preds
                    )
                    if flag:
                        return (1, 0, pred_order.get(pred, 0))
                    return (4, 0, pred_order.get(pred, 0))

                return tuple(sorted(preds, key=key))

            def block_exit_value(block: str) -> tuple[str, str] | None:
                return block_entry_value(block)

            def block_entry_value(block: str) -> tuple[str, str] | None:
                preds = tuple(cfg.get(block, ()))
                pred_infos: dict[str, tuple[str, str]] = {}
                for pred in preds:
                    info = block_exit_value(pred)
                    if info is None:
                        pred_infos[pred] = (pred, "local")
                    else:
                        pred_infos[pred] = info
                ordered = sort_preds(block, pred_infos)
                return (ordered[0], "local")

            return block_exit_value("x")
        """
    )

    ir = _compile_to_ll(program, "nested_capture_sibling_cycle")
    m = re.search(
        r"define (?:external )?ptr @user_[^(]*___nested_block_exit_value"
        r"\(([^)]*)\)",
        ir,
    )
    assert m is not None, ir
    assert "ptr %dom" in m.group(1)


def test_mem2reg_self_compile_emits_llvm_after_nested_capture_propagation():
    from pcc.py_frontend.pipeline import compile_python

    src = _REPO_ROOT / "pcc" / "ir_passes" / "mem2reg.py"
    out = _BUILD / "mem2reg_nested_capture_probe.ll"
    compile_python(str(src), str(out), emit_llvm_only=True)
    ir = out.read_text()
    assert "@user_pcc_ir_passes_mem2reg__ssa_plan_for_alloca" in ir
    assert re.search(
        r"define (?:external )?ptr "
        r"@user_pcc_ir_passes_mem2reg___nested_block_exit_value"
        r"\([^)]*ptr %dom[^)]*\)",
        ir,
    )
