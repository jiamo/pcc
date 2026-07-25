from __future__ import annotations

import os
from pathlib import Path

import pytest

import scripts.numpy_head_gate as numpy_gate
from pcc.package.build_exec import execute_build_actions

ROOT = Path(__file__).resolve().parents[1]

# The two plan/replay tests below read the meson build products of a real local
# NumPy build (projects/numpy-2.4.4/build/pcc-package/meson-build). Those
# products are generated, never committed, so a clean checkout (CI) does not
# have them: gate at collection instead of failing on a missing prerequisite.
# When the build products ARE present the tests run for real and must pass.
_MESON_COMPILE_COMMANDS = (
    ROOT
    / "projects"
    / "numpy-2.4.4"
    / "build"
    / "pcc-package"
    / "meson-build"
    / "compile_commands.json"
)
_MESON_REASON = (
    None
    if _MESON_COMPILE_COMMANDS.is_file()
    else f"local NumPy meson build products required: {_MESON_COMPILE_COMMANDS}"
)


def test_numpy_artifact_uses_package_qualified_site_layout(tmp_path: Path) -> None:
    artifact = numpy_gate._artifact_path(tmp_path, ".pcc-native.so")

    assert artifact == (
        tmp_path
        / "site"
        / "numpy"
        / "_core"
        / "_multiarray_umath.pcc-native.so"
    )


def test_loader_package_site_includes_generated_python_modules(tmp_path: Path) -> None:
    source = tmp_path / "numpy-source"
    roots = numpy_gate._loader_package_site(tmp_path / "gate", source).split(
        os.pathsep
    )

    assert roots == [
        str((tmp_path / "gate" / "site").resolve()),
        str(
            (
                source / "build" / "pcc-package" / "meson-build"
            ).resolve()
        ),
        str(source.resolve()),
    ]


@pytest.mark.pcc_gate(unavailable=_MESON_REASON)
def test_current_numpy_plan_locks_historical_surface_and_real_link_closure() -> None:
    plan = numpy_gate.build_plan(ROOT / "projects" / "numpy-2.4.4")

    numpy_gate.validate_plan(plan)
    assert plan.source_name == "numpy"
    assert plan.source_version == "2.4.4"
    assert len(plan.compile_surface) == 137
    assert len(plan.link_closure_outputs) == 136
    assert set(plan.link_closure_outputs) <= {
        action.original_output for action in plan.compile_surface
    }
    assert len(plan.source_sha256) == 64


@pytest.mark.pcc_gate(unavailable=_MESON_REASON)
def test_package_executor_dry_plan_replays_only_multiarray_umath_closure() -> None:
    report = execute_build_actions(
        "numpy",
        ROOT / "projects" / "numpy-2.4.4",
        execute=False,
        from_compile_commands=True,
        meson_target="numpy/_core/_multiarray_umath.cpython-314-darwin.so",
        abi_mode="pcc-native",
        link_output=(
            "build/pcc-package/m2-dry/"
            "_multiarray_umath.pcc3-pcc_native-macosx_14_0_arm64.so"
        ),
    )

    assert report["ok"] is True
    compile_actions = [
        action
        for action in report["actions"]
        if action["kind"].startswith("compile_command_")
    ]
    assert len(compile_actions) == 136
    assert sum(action["kind"].endswith("cxx") for action in compile_actions) == 24
    assert all(
        "build/pcc-package/pcc-native-target/objects" in action["output"]
        for action in compile_actions
    )
    assert not any(
        "python3.14" in token or "Python.framework" in token
        for action in compile_actions
        for token in action["command"]
    )
    link_action = next(
        action for action in report["actions"] if action["kind"] == "native_link"
    )
    assert Path(link_action["command"][0]).name == "c++"
    assert sum(token.endswith(".o") for token in link_action["command"]) == 136
    assert not any(token.endswith(".a") for token in link_action["command"])


def test_first_missing_module_is_a_pep489_exec_boundary() -> None:
    entered_init, entered_exec, blocker = numpy_gate.classify_loader_output(
        1,
        "",
        "Traceback (most recent call last):\nRuntimeError: module not found: math\n",
    )

    assert entered_init is True
    assert entered_exec is True
    assert blocker == {
        "kind": "first_missing_module",
        "value": "math",
        "phase": "Py_mod_exec",
    }


def test_unclassified_loader_failure_does_not_claim_init_or_exec() -> None:
    entered_init, entered_exec, blocker = numpy_gate.classify_loader_output(
        1, "", "RuntimeError: native extension init failed: opaque failure"
    )

    assert entered_init is False
    assert entered_exec is False
    assert blocker is not None
    assert blocker["kind"] == "first_semantic_mismatch"
    assert blocker["phase"] == "extension_load_or_init"


def test_missing_pyinit_is_classified_as_first_missing_symbol() -> None:
    entered_init, entered_exec, blocker = numpy_gate.classify_loader_output(
        1,
        "",
        "RuntimeError: dlsym failed: symbol not found: _PyInit__multiarray_umath\n",
    )

    assert entered_init is False
    assert entered_exec is False
    assert blocker == {
        "kind": "first_missing_symbol",
        "value": "PyInit__multiarray_umath",
        "phase": "extension_load_or_init",
    }


def test_cpp_compile_action_gets_fresh_pcc_headers_and_output(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(numpy_gate.shutil, "which", lambda _name: "/usr/bin/c++")
    source = tmp_path / "source.cpp"
    output = tmp_path / "fresh" / "source.o"
    capi = tmp_path / "pcc-capi"
    runtime = tmp_path / "runtime"
    action = numpy_gate.CompileAction(
        source=source,
        original_output="numpy/_core/target/source.cpp.o",
        command=(
            "c++",
            "-I/opt/python3.14/include/python3.14",
            "-D_LIBCPP_ENABLE_ASSERTIONS=1",
            "-MD",
            "-MQ",
            "old.o",
            "-MF",
            "old.o.d",
            "-o",
            "old.o",
            "-c",
            str(source),
        ),
        cwd=tmp_path,
    )

    command = numpy_gate._compile_command(
        action,
        output=output,
        capi_dir=capi,
        runtime_include=runtime,
    )

    assert command[0] == "/usr/bin/c++"
    assert not any("python3.14" in token for token in command)
    assert "-D_LIBCPP_ENABLE_ASSERTIONS=1" not in command
    assert "-D_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_FAST" in command
    assert "-I" + str(capi) in command
    assert "-I" + str(runtime) in command
    assert command[command.index("-o") + 1] == str(output)
    assert "-MD" not in command
    assert "-MQ" not in command
    assert "-MF" not in command
    assert "-fPIC" in command
