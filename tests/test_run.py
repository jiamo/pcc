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
        }
    )

    assert "generated" in targets
    assert "zlib" not in targets
    assert "postgres" in targets
    assert "readline" in targets


def test_pytest_sessionfinish_cleans_controller_targets(monkeypatch):
    calls = []

    class FakeConfig:
        _pcc_auto_clean_targets = ("generated", "zlib")

    class FakeSession:
        config = FakeConfig()

    def fake_clean(targets, repo_root=None):
        calls.append((targets, repo_root))

    monkeypatch.setattr(root_conftest.pcc_run, "clean", fake_clean)

    root_conftest.pytest_sessionfinish(FakeSession(), 0)

    assert calls == [(("generated", "zlib"), root_conftest._repo_root())]
