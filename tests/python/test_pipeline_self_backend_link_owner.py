"""Focused ownership contracts for self-backend link orchestration."""

from __future__ import annotations

import pytest

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_self_backend_link as self_link
from pcc.py_frontend import pipeline_self_link as link_contract


def test_explicit_repo_root_owns_self_link_driver_resolution(monkeypatch, tmp_path):
    frozen = tmp_path / "frozen-source"
    frozen.mkdir()
    (frozen / "AGENTS.md").write_text("# frozen\n", encoding="utf-8")
    monkeypatch.setenv("PCC_REPO_ROOT", str(frozen))
    monkeypatch.setattr(pipeline, "__file__", "/live/repo/pcc/py_frontend/pipeline.py")
    assert pipeline._repo_root_for_link() == str(frozen)


def test_invalid_explicit_repo_root_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("PCC_REPO_ROOT", str(tmp_path))
    with pytest.raises(pipeline.PyPipelineError, match="complete pcc source root"):
        pipeline._repo_root_for_link()


def test_cc_link_owner_runs_the_exact_prepared_command(monkeypatch):
    calls = []

    def fake_run(command, *, check):
        calls.append((command, check))

    monkeypatch.setattr(self_link.subprocess, "run", fake_run)
    command = ["cc", "input.s", "-o", "program"]
    self_link.run_link_command(
        command,
        "input.s",
        "program",
        None,
        (),
        False,
        resolve_self_link_mode=lambda: "cc",
        validate_pcc_self_link_surface=lambda **_kwargs: None,
        repo_root_for_link=lambda: "/unused",
        host_python_command=lambda: "/unused/python3",
        build_pcc_link_command=lambda **_kwargs: [],
        log=lambda *_args: None,
        join_strings=lambda values, sep: sep.join(values),
    )
    assert calls == [(command, True)]


@pytest.mark.parametrize(
    ("platform", "mode"),
    [("darwin", "cc"), ("linux", "pcc")],
)
def test_semantic_policy_cannot_cross_cc_or_linux_link_boundary(
    monkeypatch,
    platform,
    mode,
):
    calls = []
    monkeypatch.setattr(self_link.sys, "platform", platform)
    monkeypatch.setattr(
        self_link.subprocess,
        "run",
        lambda *_args, **_kwargs: calls.append(True),
    )

    with pytest.raises(
        self_link.SelfBackendLinkError,
        match="requires the pcc-owned Darwin linker",
    ):
        self_link.run_link_command(
            ["cc", "input.s", "-o", "program"],
            "input.s",
            "program",
            None,
            (),
            False,
            semantic_layout_policy="policy.json",
            resolve_self_link_mode=lambda: mode,
            validate_pcc_self_link_surface=lambda **_kwargs: None,
            repo_root_for_link=lambda: "/unused",
            host_python_command=lambda: "/unused/python3",
            build_pcc_link_command=lambda **_kwargs: [],
            log=lambda *_args: None,
            join_strings=lambda values, sep: sep.join(values),
        )

    assert calls == []


def test_facade_routes_ir_text_linking_to_the_owner(monkeypatch, tmp_path):
    observed = {}

    def fake_link_ir_texts(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs

    monkeypatch.setattr(self_link, "link_ir_texts", fake_link_ir_texts)
    pipeline._link_with_self_backend_ir_texts(
        ["define i32 @main() { ret i32 0 }"],
        str(tmp_path / "program"),
        None,
        False,
        tmp_dir=str(tmp_path),
    )

    assert observed["args"][0] == ["define i32 @main() { ret i32 0 }"]
    assert observed["kwargs"]["tmp_dir"] == str(tmp_path)
    assert observed["kwargs"]["link_run"] is pipeline._link_self_backend_ir_texts_run


def test_link_facade_has_no_second_file_path_owner():
    assert not hasattr(pipeline, "_link_with_self_backend_ir_paths")
    assert not hasattr(self_link, "link_ir_paths")


def test_semantic_layout_rejects_split_module_before_emission(
    monkeypatch,
    tmp_path,
):
    emitted = []
    policies = []
    monkeypatch.setattr(self_link.sys, "platform", "darwin")

    with pytest.raises(
        self_link.SelfBackendLinkError,
        match="does not yet own split-module symbol renaming",
    ):
        self_link.link_ir_texts_run(
            ["define i32 @main() { ret i32 0 }"],
            str(tmp_path / "program"),
            None,
            False,
            needs_libpython=False,
            tmp=str(tmp_path),
            profile=None,
            resolve_self_link_mode=lambda: "pcc",
            validate_pcc_self_link_surface=lambda **_kwargs: None,
            profile_begin=lambda _profile: 0,
            profile_end=lambda *_args: None,
            debug_dump_ir_texts=lambda _texts: None,
            split_large_modules_enabled=lambda: True,
            split_threshold_bytes=lambda: 1,
            emit_asm=lambda *_args: emitted.append("asm"),
            emit_objects=lambda *_args, **_kwargs: emitted.append("object"),
            runtime_archive_link_args=lambda *_args: [],
            native_extension_export_flags=lambda *_args: [],
            libpython_isolation_flags=lambda *_args: [],
            platform_link_flags=lambda: [],
            append_libpython_link_flags=lambda _cmd: None,
            log=lambda *_args: None,
            join_strings=lambda values, sep: sep.join(values),
            run_self_link_command=lambda *_args, **_kwargs: None,
            finish_self_backend_executable=lambda *_args, **_kwargs: None,
            semantic_layout_enabled=lambda: True,
            write_semantic_layout_policy=(
                lambda *_args: policies.append(True)
            ),
        )

    assert emitted == []
    assert policies == []


def test_pcc_link_selection_fails_before_silent_cc_fallback(tmp_path):
    try:
        self_link.run_link_command(
            ["cc", "input.s"],
            "input.s",
            str(tmp_path / "program"),
            None,
            (),
            False,
            resolve_self_link_mode=lambda: "pcc",
            validate_pcc_self_link_surface=lambda **_kwargs: None,
            repo_root_for_link=lambda: str(tmp_path),
            host_python_command=lambda: "/unused/python3",
            build_pcc_link_command=lambda **_kwargs: [],
            log=lambda *_args: None,
            join_strings=lambda values, sep: sep.join(values),
        )
    except self_link.SelfBackendLinkError as exc:
        assert "driver is missing" in str(exc)
    else:
        raise AssertionError("missing pcc driver must fail closed")


def test_linux_pcc_link_route_uses_owned_elf_driver_and_internal_assembly(
    monkeypatch, tmp_path
):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    driver = scripts / "pcc_link_elf.py"
    driver.write_text("# owned ELF driver\n", encoding="utf-8")
    output = tmp_path / "program"
    calls = []

    def fake_run(command, *, check):
        calls.append(list(command))
        assert check is True
        output.write_bytes(b"ELF")
        output.chmod(0o755)

    monkeypatch.setattr(self_link.sys, "platform", "linux")
    monkeypatch.setattr(self_link.sys, "executable", "/compiled/pcc1")
    monkeypatch.setattr(self_link.subprocess, "run", fake_run)
    self_link.run_link_command(
        ["cc", "ignored"],
        None,
        str(output),
        "/runtime/libpcc.a",
        ("extra.o",),
        False,
        pcc_asm_inputs=("first.s", "second.s"),
        resolve_self_link_mode=lambda: "pcc",
        validate_pcc_self_link_surface=lambda **_kwargs: None,
        repo_root_for_link=lambda: str(tmp_path),
        host_python_command=lambda: "/repo/.venv/bin/python3",
        build_pcc_link_command=link_contract.build_pcc_link_command,
        log=lambda *_args: None,
        join_strings=lambda values, sep: sep.join(values),
    )
    assert len(calls) == 1
    command = calls[0]
    assert command[0] == "/repo/.venv/bin/python3"
    assert "/compiled/pcc1" not in command
    assert str(driver) in command
    assert [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--asm"
    ] == ["first.s", "second.s"]
    assert [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--object"
    ] == ["extra.o"]
    assert command[command.index("--archive") + 1] == "/runtime/libpcc.a"
