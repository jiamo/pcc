import os
import subprocess

import conftest as root_conftest
import run as run_module


def _git(tmp_path, *args):
    return subprocess.run(
        ["git", *args],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_clean_restores_tracked_files_and_removes_generated_outputs(tmp_path, monkeypatch):
    dep_dir = tmp_path / "dep"
    dep_dir.mkdir()

    pristine_makefile = (
        "distclean:\n"
        "\trm -f build.o generated.pc\n"
    )
    pristine_header = "#define VALUE 41\n"

    (dep_dir / "Makefile").write_text(pristine_makefile)
    (dep_dir / "config.h").write_text(pristine_header)

    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "add", "dep/Makefile", "dep/config.h")
    _git(tmp_path, "commit", "-m", "init")

    (dep_dir / "Makefile").write_text(
        "distclean:\n"
        "\trm -f build.o generated.pc\n"
        "\t@echo cleaned\n"
    )
    (dep_dir / "config.h").write_text("#define VALUE 99\n")
    (dep_dir / "build.o").write_text("object\n")
    (dep_dir / "generated.pc").write_text("pc\n")

    monkeypatch.setattr(
        run_module,
        "CLEAN_TARGETS",
        {
            "fake": run_module.CleanTarget(
                make_dir="dep",
                make_target="distclean",
                make_if_exists=("Makefile",),
                restore_paths=("dep/Makefile", "dep/config.h"),
                remove_paths=("dep/generated.pc",),
            )
        },
    )

    assert (dep_dir / "build.o").exists()
    assert (dep_dir / "generated.pc").exists()

    run_module.clean(["fake"], repo_root=str(tmp_path))

    assert (dep_dir / "Makefile").read_text() == pristine_makefile
    assert (dep_dir / "config.h").read_text() == pristine_header
    assert not (dep_dir / "build.o").exists()
    assert not (dep_dir / "generated.pc").exists()


def test_auto_clean_targets_skip_preexisting_dirty_project_trees():
    targets = root_conftest._auto_clean_targets(
        {
            "projects/zlib-1.3.1/Makefile",
            "README.md",
        },
        {
            "projects/zlib-1.3.1/Makefile",
            "projects/postgresql-17.4/config.status",
            "projects/readline-8.2/Makefile",
        },
    )

    assert "generated" in targets
    assert "zlib" not in targets
    assert "postgres" in targets
    assert "readline" in targets


def test_auto_clean_targets_only_include_newly_dirty_project_trees():
    targets = root_conftest._auto_clean_targets(
        {"README.md"},
        {
            "README.md",
            "projects/readline-8.2/Makefile",
            "projects/readline-8.2/confdefs.h",
        },
    )

    assert targets == ("generated", "readline")


def test_pytest_sessionfinish_cleans_controller_targets(monkeypatch):
    calls = []

    class FakeConfig:
        _pcc_initial_status_paths = {"README.md"}

    class FakeSession:
        config = FakeConfig()

    def fake_clean(targets, repo_root=None):
        calls.append((targets, repo_root))

    monkeypatch.setattr(root_conftest.pcc_run, "clean", fake_clean)
    monkeypatch.setattr(
        root_conftest,
        "_status_paths",
        lambda _repo_root: {"README.md", "projects/zlib-1.3.1/Makefile"},
    )

    root_conftest.pytest_sessionfinish(FakeSession(), 0)

    assert calls == [(("generated", "zlib"), root_conftest._repo_root())]


def test_pytest_sessionfinish_skips_auto_clean_when_lock_is_busy(monkeypatch):
    calls = []

    class FakeLock:
        def close(self):
            pass

    class FakeConfig:
        _pcc_initial_status_paths = {"README.md"}
        _pcc_auto_clean_lockfile = FakeLock()

    class FakeSession:
        config = FakeConfig()

    def fake_clean(targets, repo_root=None):
        calls.append((targets, repo_root))

    monkeypatch.setattr(root_conftest.pcc_run, "clean", fake_clean)
    monkeypatch.setattr(
        root_conftest,
        "_status_paths",
        lambda _repo_root: {"README.md", "projects/zlib-1.3.1/Makefile"},
    )

    calls_to_flock = {"count": 0}
    real_flock = root_conftest.fcntl.flock

    def fake_flock(lockfile, op):
        calls_to_flock["count"] += 1
        if calls_to_flock["count"] == 1:
            return None
        raise BlockingIOError

    monkeypatch.setattr(root_conftest.fcntl, "flock", fake_flock)
    try:
        root_conftest.pytest_sessionfinish(FakeSession(), 0)
    finally:
        monkeypatch.setattr(root_conftest.fcntl, "flock", real_flock)

    assert calls == []
