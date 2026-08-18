from __future__ import annotations

from pathlib import Path
import shutil
import stat
import sys
from types import SimpleNamespace

import pytest

from tests import runtime_build_cache as cache
from tests.test_runtime_archive_consumers import _write_valid_runtime_archive


_SOURCE_FILES = {
    "pcc/py_runtime/Makefile": "all:\n",
    "pcc/py_runtime/src/py_tuple.c": "tuple-c\n",
    "pcc/py_runtime/py/py_tuple.py": "tuple-python\n",
    "pcc/py_runtime/include/py_runtime.h": "header\n",
    "pcc/py_frontend/lowering.py": "compiler\n",
    "pcc/backend/emit.py": "backend\n",
}


@pytest.fixture
def runtime_sources(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    for name, content in reversed(tuple(_SOURCE_FILES.items())):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    runtime = repo / "pcc" / "py_runtime"
    external = tmp_path / ".frozen-source" / "py_runtime"
    shutil.copytree(runtime, external)
    pcc_bin = repo / ".venv" / "bin" / "pcc"
    pcc_bin.parent.mkdir(parents=True)
    pcc_bin.write_text("fixture compiler launcher\n", encoding="utf-8")
    monkeypatch.setattr(cache, "_REPO_ROOT", repo)
    monkeypatch.setattr(cache, "_RUNTIME_DIR", runtime)
    monkeypatch.setattr(cache, "sys", SimpleNamespace(version="fixture-python", executable=sys.executable))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    for name in cache._PCC_PY_ARCHIVE_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    return SimpleNamespace(repo=repo, runtime=runtime, external=external, pcc_bin=pcc_bin)


def test_default_runtime_key_preserves_v4_serialization(runtime_sources):
    assert cache._pcc_runtime_source_key(Path("/frozen/pcc")) == "7d209b20edafce1a5b49226b"


def test_identical_external_runtime_uses_same_logical_source_key(runtime_sources):
    sources = runtime_sources
    assert cache._pcc_runtime_source_key(
        sources.pcc_bin, runtime_source=sources.external,
    ) == cache._pcc_runtime_source_key(sources.pcc_bin)
    assert cache._pcc_runtime_cache_key(
        sources.pcc_bin, variant="pcc-py", runtime_source=sources.external,
    ) == cache._pcc_runtime_cache_key(sources.pcc_bin, variant="pcc-py")


def test_runtime_override_hashes_selected_tuple_and_current_compiler(runtime_sources):
    sources = runtime_sources
    original = cache._pcc_runtime_source_key(sources.pcc_bin, runtime_source=sources.external)
    (sources.runtime / "py" / "py_tuple.py").write_text("working-tree variant\n")
    assert cache._pcc_runtime_source_key(sources.pcc_bin, runtime_source=sources.external) == original
    (sources.external / "py" / "py_tuple.py").write_text("tuple control\n")
    tuple_changed = cache._pcc_runtime_source_key(sources.pcc_bin, runtime_source=sources.external)
    assert tuple_changed != original
    (sources.repo / "pcc" / "py_frontend" / "lowering.py").write_text("compiler changed\n")
    assert cache._pcc_runtime_source_key(sources.pcc_bin, runtime_source=sources.external) != tuple_changed


@pytest.mark.parametrize("threaded", (False, True))
def test_runtime_override_copies_readonly_source_into_writable_verified_staging(
    runtime_sources, monkeypatch, threaded,
):
    sources = runtime_sources
    selected_tuple = sources.external / "py" / "py_tuple.py"
    selected_tuple.write_text("frozen tuple control\n", encoding="utf-8")
    stale = sources.external / "build_py"
    stale.mkdir()
    (stale / "py_tuple.o").write_bytes(b"stale object")
    (sources.external / "libpy_runtime_pcc_py.a").write_bytes(b"stale archive")
    (sources.external / "libpy_runtime_pcc_py.a.provenance.json").write_text("stale receipt")
    all_sources = [sources.external, *sources.external.rglob("*")]
    old_modes = {path: stat.S_IMODE(path.stat().st_mode) for path in all_sources}
    for path in all_sources:
        path.chmod(0o555 if path.is_dir() else 0o444)
    builds = []

    def fake_make(command, **kwargs):
        staging = Path(command[2])
        builds.append(staging)
        assert staging != sources.external and staging != sources.runtime
        assert (staging / "py" / "py_tuple.py").read_text() == "frozen tuple control\n"
        assert not (staging / "build_py").exists()
        assert not (staging / "libpy_runtime_pcc_py.a").exists()
        assert not (staging / "libpy_runtime_pcc_py.a.provenance.json").exists()
        for path in (staging, *staging.rglob("*")):
            assert path.stat().st_mode & stat.S_IWUSR, path
            if path.is_dir():
                assert path.stat().st_mode & stat.S_IXUSR, path
        assert f"PCC={sources.pcc_bin}" in command
        assert f"PCC_REPO_ROOT={sources.repo}" in command
        assert f"PCC_WITH_THREADS={1 if threaded else 0}" in command
        assert kwargs["timeout"] == 900
        _write_valid_runtime_archive(staging)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cache, "subprocess", SimpleNamespace(run=fake_make))
    builder = cache.cached_threaded_pcc_python_runtime if threaded else cache.cached_pcc_python_runtime
    try:
        runtime = builder(runtime_source=sources.external)
        assert builder(runtime_source=sources.external) == runtime
        assert len(builds) == 1
        assert selected_tuple.read_text() == "frozen tuple control\n"
        assert stat.S_IMODE(selected_tuple.stat().st_mode) == 0o444
        assert stat.S_IMODE(sources.external.stat().st_mode) == 0o555
        archive = runtime / "libpy_runtime_pcc_py.a"
        cache.verify_runtime_archive_manifest(archive, runtime_root=runtime)
        variant = "threaded-pcc-py" if threaded else "pcc-py"
        assert cache._pcc_runtime_cache_is_complete(
            runtime=runtime, archive=archive,
            marker=runtime / (".pcc-" + variant + "-complete"),
            key=cache._pcc_runtime_cache_key(sources.pcc_bin, variant=variant, runtime_source=sources.external),
        )
    finally:
        for path, mode in old_modes.items():
            path.chmod(mode)
