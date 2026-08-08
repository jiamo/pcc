from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


def _write_completed_c_runtime_bundle(archive: Path) -> Path:
    source = archive.with_suffix(".c")
    object_path = archive.with_suffix(".o")
    source.write_text(
        "int PyRuntime_IsolationAnchor(void) { return 1; }\n",
        encoding="utf-8",
    )
    compile_result = subprocess.run(
        ["cc", "-c", str(source), "-o", str(object_path)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr
    archive_result = subprocess.run(
        ["ar", "rcs", str(archive), str(object_path)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert archive_result.returncode == 0, archive_result.stdout + archive_result.stderr
    nm_result = subprocess.run(
        ["nm", "-g", str(archive)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert nm_result.returncode == 0, nm_result.stdout + nm_result.stderr
    symbols: set[str] = set()
    for line in nm_result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[1] == "U" or not parts[1].isupper():
            continue
        symbol = parts[2]
        bare = symbol[1:] if symbol.startswith("_") else symbol
        if bare.startswith("Py") or bare.startswith("_Py"):
            symbols.add(symbol)
    assert symbols
    Path(str(archive) + ".capi_syms").write_text(
        "\n".join(sorted(symbols)) + "\n", encoding="ascii"
    )
    return archive


def _write_valid_production_runtime_archive(runtime_root: Path) -> Path:
    from llvmlite import binding as llvm

    from pcc.tools.ir_to_obj import emit_object
    from pcc.tools.runtime_archive_provenance import (
        assemble_runtime_archive_manifest,
        capi_inventory_path_for_archive,
        write_pcc_python_receipt,
    )

    source = runtime_root / "py" / "member.py"
    ir_path = runtime_root / "build_py" / "member.ll"
    object_path = runtime_root / "build_py" / "member.o"
    source.parent.mkdir(parents=True, exist_ok=True)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def member() -> int:\n    return 1\n", encoding="utf-8")
    target_triple = llvm.get_default_triple()
    ir_text = (
        f'target triple = "{target_triple}"\n' "define i32 @member() { ret i32 1 }\n"
    )
    ir_path.write_text(ir_text, encoding="utf-8")
    object_path.write_bytes(emit_object(ir_text))
    write_pcc_python_receipt(
        object_path=object_path,
        ir_path=ir_path,
        source_path=source,
        runtime_root=runtime_root,
        target_triple=target_triple,
    )
    archive = runtime_root / "libpy_runtime_pcc_py.a"
    process = subprocess.run(
        ["ar", "rcs", str(archive), str(object_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    capi_inventory_path_for_archive(archive).write_text(
        "PyRuntime_IsolationAnchor\n",
        encoding="ascii",
    )
    assemble_runtime_archive_manifest(
        archive,
        [object_path],
        runtime_root=runtime_root,
    )
    return archive


def test_runtime_archive_environment_override_is_fail_closed(
    tmp_path: Path,
    monkeypatch,
):
    from pcc.py_frontend import pipeline

    archive = tmp_path / "libpy_runtime.a"
    _write_completed_c_runtime_bundle(archive)
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    assert pipeline._ensure_runtime(False) == str(archive)

    missing = tmp_path / "missing.a"
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(missing))
    with pytest.raises(pipeline.PyPipelineError, match="explicit runtime archive"):
        pipeline._ensure_runtime(False)


def test_explicit_c_runtime_archive_rejects_empty_member_inventory(
    tmp_path: Path,
    monkeypatch,
):
    from pcc.py_frontend import pipeline

    archive = tmp_path / "libpy_runtime.a"
    # This is the observed corrupt publication shape: a regular archive whose
    # only real content was the ar symbol table, accompanied by an empty C-API
    # completion inventory.
    archive.write_bytes(b"!<arch>\n")
    Path(str(archive) + ".capi_syms").write_text("", encoding="ascii")
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))

    with pytest.raises(
        pipeline.PyPipelineError,
        match="invalid archive/inventory bundle",
    ):
        pipeline._ensure_runtime(False)


def test_explicit_c_runtime_archive_rejects_inventory_from_another_archive(
    tmp_path: Path,
    monkeypatch,
):
    from pcc.py_frontend import pipeline

    archive = _write_completed_c_runtime_bundle(tmp_path / "libpy_runtime.a")
    Path(str(archive) + ".capi_syms").write_text(
        "PyDifferentRuntimeSymbol\n",
        encoding="ascii",
    )
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))

    with pytest.raises(
        pipeline.PyPipelineError,
        match="invalid archive/inventory bundle",
    ):
        pipeline._ensure_runtime(False)


def test_runtime_make_never_captures_away_build_diagnostics(monkeypatch):
    from pcc.py_frontend import pipeline

    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    pipeline._run_runtime_make(["make", "runtime"], verbose=False)

    assert len(calls) == 1
    assert calls[0]["check"] is True
    assert calls[0]["capture_output"] is False


def test_runtime_make_reclaims_dead_owner_lock(tmp_path: Path):
    from pcc.py_frontend.pipeline_runtime_archive import run_runtime_make

    lock = tmp_path / ".pcc-runtime-build.lock"
    lock.mkdir()
    (lock / "owner").write_text("99999999\n", encoding="ascii")

    run_runtime_make(
        str(tmp_path),
        [sys.executable, "-c", "pass"],
        verbose=False,
    )

    assert not lock.exists()


def test_runtime_build_failure_is_reported_before_link(
    tmp_path: Path,
    monkeypatch,
):
    from pcc.py_frontend import pipeline

    runtime_root = tmp_path / "py_runtime"
    runtime_root.mkdir()
    (runtime_root / "Makefile").write_text("all:\n", encoding="utf-8")
    archive = runtime_root / "libpy_runtime.a"

    def fail_build(_make_cmd, *, verbose):
        raise subprocess.CalledProcessError(2, ["make", "libpy_runtime.a"])

    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_DIR", str(runtime_root))
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_ARCHIVE", str(archive))
    monkeypatch.setattr(pipeline, "_run_runtime_make", fail_build)

    with pytest.raises(pipeline.PyPipelineError, match="failed to build required"):
        pipeline._ensure_runtime(False, needs_libpython=False)


def test_runtime_build_rejects_empty_archive_publication(
    tmp_path: Path,
    monkeypatch,
):
    from pcc.py_frontend import pipeline

    runtime_root = tmp_path / "py_runtime"
    runtime_root.mkdir()
    (runtime_root / "Makefile").write_text("all:\n", encoding="utf-8")
    archive = runtime_root / "libpy_runtime.a"

    def publish_empty(_make_cmd, *, verbose):
        archive.write_bytes(b"!<arch>\n")
        Path(str(archive) + ".capi_syms").write_text("", encoding="ascii")

    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_DIR", str(runtime_root))
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_ARCHIVE", str(archive))
    monkeypatch.setattr(pipeline, "_run_runtime_make", publish_empty)

    with pytest.raises(
        pipeline.PyPipelineError,
        match="invalid archive/inventory bundle",
    ):
        pipeline._ensure_runtime(False, needs_libpython=False)


def test_c_runtime_make_publication_requires_nonempty_inventory() -> None:
    makefile = (
        Path(__file__).resolve().parents[2] / "pcc" / "py_runtime" / "Makefile"
    ).read_text(encoding="utf-8")

    host_rule = makefile[makefile.index("$(LIB): $(OBJS)") :]
    host_rule = host_rule[: host_rule.index("$(OBJDIR)/%.o:")]
    pcc_rule = makefile[makefile.index("$(LIB_PCC):") :]
    pcc_rule = pcc_rule[: pcc_rule.index("$(OBJDIR_PCC)/%.o:")]
    for rule in (host_rule, pcc_rule):
        assert 'test -s "$@.capi_syms.tmp"' in rule
        assert rule.index('mv -f "$@.tmp" "$@"') < rule.index(
            'mv -f "$@.capi_syms.tmp" "$@.capi_syms"'
        )


def test_production_runtime_archive_environment_override_rejects_invalid_provenance(
    tmp_path: Path,
    monkeypatch,
):
    from pcc.py_frontend import pipeline

    archive = tmp_path / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"not a production archive")
    Path(str(archive) + ".provenance.json").write_text(
        '{"schema": "not-the-production-schema"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))

    with pytest.raises(pipeline.PyPipelineError, match="invalid provenance"):
        pipeline._ensure_runtime(False)


@pytest.mark.parametrize("shortcut", ["fresh", "wheel"])
def test_invalid_production_runtime_shortcut_rebuilds_before_acceptance(
    tmp_path: Path,
    monkeypatch,
    shortcut: str,
):
    from pcc.py_frontend import pipeline

    runtime_root = tmp_path / "py_runtime"
    runtime_root.mkdir()
    makefile = runtime_root / "Makefile"
    makefile.write_text("all:\n", encoding="utf-8")
    archive = _write_valid_production_runtime_archive(runtime_root)
    manifest = Path(str(archive) + ".provenance.json")
    manifest.write_text('{"schema": "tampered"}\n', encoding="utf-8")
    Path(pipeline._runtime_archive_target_stamp(str(archive))).write_text(
        pipeline._runtime_archive_target_id() + "\n",
        encoding="utf-8",
    )
    if shortcut == "wheel":
        Path(str(archive) + ".wheel").write_text(
            "pcc.runtime-wheel-artifact.v1\n"
            + pipeline._runtime_archive_target_id()
            + "\nsha256:"
            + ("a" * 64)
            + "\n",
            encoding="utf-8",
        )
    old = 1_700_000_000
    current = old + 10
    os.utime(makefile, (old, old))
    os.utime(runtime_root / "py" / "member.py", (old, old))
    os.utime(archive, (current, current))

    make_calls: list[list[str]] = []

    def rebuild(make_cmd, *, verbose):
        make_calls.append(list(make_cmd))
        _write_valid_production_runtime_archive(runtime_root)

    monkeypatch.setenv("PCC_RUNTIME_CC", "pcc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "py")
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_DIR", str(runtime_root))
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_ARCHIVE_PCC_PY", str(archive))
    monkeypatch.setattr(
        pipeline,
        "_runtime_archive_compiler_sources_newer_than",
        lambda *_args: False,
    )
    monkeypatch.setattr(pipeline, "_run_runtime_make", rebuild)

    assert pipeline._ensure_runtime(False, needs_libpython=False) == str(archive)
    assert len(make_calls) == 1
    assert pipeline._runtime_archive_stale(str(archive)) is False


def test_production_runtime_is_verified_after_make_before_acceptance(
    tmp_path: Path,
    monkeypatch,
):
    from pcc.py_frontend import pipeline

    runtime_root = tmp_path / "py_runtime"
    runtime_root.mkdir()
    (runtime_root / "Makefile").write_text("all:\n", encoding="utf-8")
    archive = runtime_root / "libpy_runtime_pcc_py.a"

    def build_invalid_archive(_make_cmd, *, verbose):
        archive.write_bytes(b"not a production archive")
        Path(str(archive) + ".provenance.json").write_text(
            '{"schema": "tampered"}\n',
            encoding="utf-8",
        )

    monkeypatch.setenv("PCC_RUNTIME_CC", "pcc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "py")
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_DIR", str(runtime_root))
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_ARCHIVE_PCC_PY", str(archive))
    monkeypatch.setattr(pipeline, "_run_runtime_make", build_invalid_archive)

    with pytest.raises(pipeline.PyPipelineError, match="invalid provenance"):
        pipeline._ensure_runtime(False, needs_libpython=False)
    assert not Path(pipeline._runtime_archive_target_stamp(str(archive))).exists()


def test_invalid_production_runtime_without_makefile_fails_closed(
    tmp_path: Path,
    monkeypatch,
):
    from pcc.py_frontend import pipeline

    runtime_root = tmp_path / "py_runtime"
    runtime_root.mkdir()
    archive = runtime_root / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"not a production archive")
    Path(str(archive) + ".provenance.json").write_text(
        '{"schema": "tampered"}\n',
        encoding="utf-8",
    )
    Path(pipeline._runtime_archive_target_stamp(str(archive))).write_text(
        pipeline._runtime_archive_target_id() + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PCC_RUNTIME_CC", "pcc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "py")
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_DIR", str(runtime_root))
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_ARCHIVE_PCC_PY", str(archive))

    with pytest.raises(pipeline.PyPipelineError, match="invalid provenance"):
        pipeline._ensure_runtime(False, needs_libpython=False)


def test_auto_package_compile_propagates_explicit_runtime_archive(
    tmp_path: Path,
    monkeypatch,
):
    from pcc.py_frontend import pipeline

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "lib.py").write_text("VALUE = 7\n", encoding="utf-8")
    entry = package / "__main__.py"
    entry.write_text("from .lib import VALUE\nprint(VALUE)\n", encoding="utf-8")
    archive = tmp_path / "isolated-runtime.a"
    archive.write_bytes(b"archive")
    captured = {}

    def fake_compile_python_multi(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(pipeline, "compile_python_multi", fake_compile_python_multi)
    pipeline.compile_python(
        str(entry),
        str(tmp_path / "out"),
        libpython_mode="off",
        ir_scaffold_mode="on",
        runtime_archive=str(archive),
    )

    assert captured["runtime_archive"] == str(archive)
