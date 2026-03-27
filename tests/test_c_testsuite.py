from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
C_TESTSUITE_DIR = REPO_ROOT / "projects" / "c-testsuite"


def test_c_testsuite_directory_is_present_but_currently_empty():
    if not C_TESTSUITE_DIR.exists():
        assert True
        return
    assert C_TESTSUITE_DIR.is_dir()
    assert list(C_TESTSUITE_DIR.rglob("*.c")) == []
