from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pcc1_gate import find_current_pcc1, skip_or_fail_no_current_pcc1

REPO = Path(__file__).absolute().parents[2]


def _real_simplejson_site() -> Path:
    raw = os.environ.get("PCC_M1_SIMPLEJSON_SITE", "").strip()
    if not raw:
        pytest.skip("set PCC_M1_SIMPLEJSON_SITE to the pinned pcc-native install")
    site = Path(raw).resolve()
    if not (site / "simplejson" / "__init__.py").is_file():
        pytest.fail(f"PCC_M1_SIMPLEJSON_SITE is not a simplejson install: {site}")
    extensions = tuple((site / "simplejson").glob("_speedups.pcc3-pcc_native-*.so"))
    if len(extensions) != 1:
        pytest.fail(f"expected one pcc-native simplejson extension, got {extensions}")
    return site


def _current_pcc1() -> Path:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 for the M1 package gate")
    return pcc1


def _compile_with_pcc1(pcc1: Path, site: Path, source: Path, exe: Path) -> None:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    env["PCC_RUNTIME_HIGH"] = "c"
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    env["PCC_HOST_PCC"] = "/usr/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(exe),
        ],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _assert_no_host_runtime_dependencies(exe: Path) -> None:
    command = (
        ["otool", "-L", str(exe)] if sys.platform == "darwin" else ["ldd", str(exe)]
    )
    proc = subprocess.run(
        command,
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    dependencies = (proc.stdout + proc.stderr).lower()
    assert "libpython" not in dependencies
    assert "python.framework" not in dependencies
    assert "libllvm" not in dependencies


def test_real_simplejson_extension_behavior_matches_cpython_under_all_gcs(
    tmp_path,
):
    site = _real_simplejson_site()
    pcc1 = _current_pcc1()
    source = tmp_path / "simplejson_behavior.py"
    source.write_text(
        textwrap.dedent("""
            import simplejson
            import simplejson.decoder as decoder
            import simplejson.encoder as encoder
            import simplejson.scanner as scanner

            native = (
                scanner.c_make_scanner is not None
                and scanner.make_scanner is scanner.c_make_scanner
                and decoder.c_scanstring is not None
                and encoder.c_make_encoder is not None
            )
            payload = {"items": [1, "two", None], "ok": True}
            encoded = simplejson.dumps(payload, separators=(",", ":"), sort_keys=True)
            print("native", native)
            print("encoded", encoded)
            print("roundtrip", simplejson.loads(encoded) == payload)
            """).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "simplejson_behavior"
    _compile_with_pcc1(pcc1, site, source, exe)
    _assert_no_host_runtime_dependencies(exe)

    expected_stdout = (
        "native True\n"
        'encoded {"items":[1,"two",null],"ok":true}\n'
        "roundtrip True\n"
    )
    gc_stdout = {}
    for backend in range(5):
        env = os.environ.copy()
        env.pop("LC_ALL", None)
        env["PCC_PACKAGE_SITE"] = str(site)
        env["PCC_GC_BACKEND"] = str(backend)
        run = subprocess.run(
            [str(exe)],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert (
            run.returncode == 0
        ), f"GC backend {backend} failed:\n{run.stdout}{run.stderr}"
        assert run.stderr == "", f"GC backend {backend}: {run.stderr}"
        assert run.stdout == expected_stdout
        gc_stdout[str(backend)] = run.stdout
    assert len(set(gc_stdout.values())) == 1

    oracle_source = tmp_path / "simplejson_oracle.py"
    oracle_source.write_text(
        "import simplejson\n"
        "payload = {'items': [1, 'two', None], 'ok': True}\n"
        "encoded = simplejson.dumps(payload, separators=(',', ':'), sort_keys=True)\n"
        "print('encoded', encoded)\n"
        "print('roundtrip', simplejson.loads(encoded) == payload)\n",
        encoding="utf-8",
    )
    oracle_env = os.environ.copy()
    oracle_env.pop("LC_ALL", None)
    oracle_env["PCC_PACKAGE_SITE"] = str(site)
    oracle_env["PYTHONPATH"] = str(site)
    oracle = subprocess.run(
        [
            os.environ.get("PYTHON", str(REPO / ".venv" / "bin" / "python")),
            str(oracle_source),
        ],
        cwd=REPO,
        env=oracle_env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert oracle.returncode == 0, oracle.stdout + oracle.stderr
    assert gc_stdout["0"].splitlines()[1:] == oracle.stdout.splitlines()


def test_real_simplejson_missing_compiled_dependency_has_mode_labeled_diagnostic(
    tmp_path,
):
    site = _real_simplejson_site()
    pcc1 = _current_pcc1()
    source = tmp_path / "simplejson_missing_dependency.py"
    source.write_text(
        "from simplejson._speedups import make_scanner\nprint(make_scanner)\n",
        encoding="utf-8",
    )
    exe = tmp_path / "simplejson_missing_dependency"
    _compile_with_pcc1(pcc1, site, source, exe)
    _assert_no_host_runtime_dependencies(exe)

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    run = subprocess.run(
        [str(exe)],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert run.returncode == 1
    assert run.stderr == (
        "Traceback (most recent call last):\n"
        "RuntimeError: PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython] "
        "module not found: simplejson.raw_json\n"
    )
