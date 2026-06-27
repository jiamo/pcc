from __future__ import annotations

from pathlib import Path

import pytest


def test_runtime_archive_environment_override_is_fail_closed(
    tmp_path: Path,
    monkeypatch,
):
    from pcc.py_frontend import pipeline

    archive = tmp_path / "libpy_runtime.a"
    archive.write_bytes(b"archive")
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    assert pipeline._ensure_runtime(False) == str(archive)

    missing = tmp_path / "missing.a"
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(missing))
    with pytest.raises(pipeline.PyPipelineError, match="explicit runtime archive"):
        pipeline._ensure_runtime(False)


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
