from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import zipfile
from pathlib import Path

import pytest

from pcc1_gate import find_current_pcc1, skip_or_fail_no_current_pcc1

from pcc.package.install import install_package
from pcc.package.metadata import current_platform_tag
from pcc.py_frontend.pipeline import (
    PyPipelineError,
    _collect_relative_module_closure,
    compile_python,
)


REPO = Path(__file__).resolve().parents[2]
def _find_current_pcc1() -> Path | None:
    return find_current_pcc1(REPO)


def _write_importable_project(root: Path) -> Path:
    root.mkdir()
    pkg = root / "demo_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg / "core.py").write_text(
        "def answer() -> int:\n"
        "    return 42\n",
        encoding="utf-8",
    )
    return root


def _write_import_main(path: Path) -> None:
    path.write_text(
        "from demo_pkg.core import answer\n"
        "def main():\n"
        "    print(answer())\n"
        "main()\n",
        encoding="utf-8",
    )


def _write_import_package_main(path: Path) -> None:
    path.write_text(
        "import demo_pkg\n"
        "from demo_pkg.core import answer\n"
        "def main():\n"
        "    print(demo_pkg.VALUE + answer())\n"
        "main()\n",
        encoding="utf-8",
    )


def _assert_compiled_import_prints_42(main: Path, exe: Path) -> None:
    compile_python(
        str(main),
        str(exe),
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    proc = subprocess.run(
        [str(exe)],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert proc.stdout.strip() == "42"


def _assert_pcc_cli_import_prints(main: Path, exe: Path, site: Path, expected: str) -> None:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=90,
        env=env,
    )
    proc = subprocess.run(
        [str(exe)],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert proc.stdout.strip() == expected


def _write_importable_wheel(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("demo_pkg/__init__.py", "VALUE = 1\n")
        zf.writestr(
            "demo_pkg/core.py",
            "def answer() -> int:\n"
            "    return 42\n",
        )
        zf.writestr("demo_pkg-0.1.dist-info/METADATA", "Name: demo-pkg\n")
    return path


def _write_numpy_command_shape_wheel(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("numpy/__init__.py", "VALUE = 3\n")
        zf.writestr(
            "numpy/core.py",
            "def answer() -> int:\n"
            "    return 39\n",
        )
        zf.writestr("numpy-2.4.4.dist-info/METADATA", "Name: numpy\nVersion: 2.4.4\n")
        zf.writestr(
            "numpy-2.4.4.dist-info/WHEEL",
            "Wheel-Version: 1.0\n"
            "Generator: pcc-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
    return path


def _write_real_cpython_extension_wheel(tmp_path: Path) -> Path:
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        pytest.skip("C compiler required for real CPython extension wheel tracer")
    include = sysconfig.get_paths().get("include")
    if not include or not Path(include).is_dir():
        pytest.skip("Python include directory required for real CPython extension wheel tracer")
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not ext_suffix:
        pytest.skip("Python EXT_SUFFIX required for real CPython extension wheel tracer")

    build = tmp_path / "real_ext_build"
    build.mkdir()
    src = build / "_native.c"
    src.write_text(
        "#include <Python.h>\n"
        "static PyObject *answer(PyObject *self, PyObject *args) {\n"
        "    return PyLong_FromLong(7);\n"
        "}\n"
        "static PyMethodDef Methods[] = {\n"
        "    {\"answer\", answer, METH_NOARGS, \"\"},\n"
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "static struct PyModuleDef Module = {\n"
        "    PyModuleDef_HEAD_INIT, \"_native\", NULL, -1, Methods,\n"
        "};\n"
        "PyMODINIT_FUNC PyInit__native(void) {\n"
        "    return PyModule_Create(&Module);\n"
        "}\n",
        encoding="utf-8",
    )
    ext = build / ("_native" + ext_suffix)
    if sys.platform == "darwin":
        cmd = [cc, "-bundle", "-undefined", "dynamic_lookup", "-I", include, str(src), "-o", str(ext)]
    else:
        cmd = [cc, "-shared", "-fPIC", "-I", include, str(src), "-o", str(ext)]
    subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=60)

    cp_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    wheel = tmp_path / f"real_ext_pkg-0.1-{cp_tag}-{cp_tag}-{current_platform_tag()}.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("real_ext_pkg/__init__.py", "from . import _native\nfrom .core import answer\n")
        zf.writestr(
            "real_ext_pkg/core.py",
            "def answer() -> int:\n"
            "    return 42\n",
        )
        zf.write(ext, "real_ext_pkg/" + ext.name)
        zf.writestr("real_ext_pkg-0.1.dist-info/METADATA", "Name: real-ext-pkg\nVersion: 0.1\n")
        zf.writestr(
            "real_ext_pkg-0.1.dist-info/WHEEL",
            "Wheel-Version: 1.0\n"
            "Generator: pcc-test\n"
            "Root-Is-Purelib: false\n"
            f"Tag: {cp_tag}-{cp_tag}-{current_platform_tag()}\n",
        )
    return wheel


def _write_importable_sdist(path: Path, src_root: Path) -> Path:
    src_root.mkdir()
    project = _write_importable_project(src_root / "demo_pkg-0.1")
    with tarfile.open(path, "w:gz") as tf:
        tf.add(project, arcname="demo_pkg-0.1")
    return path


def test_package_site_participates_in_python_source_closure(tmp_path, monkeypatch):
    project = _write_importable_project(tmp_path / "demo_pkg-0.1")
    site = tmp_path / "site"
    result = install_package(str(project), target_dir=site, cache_dir=tmp_path / "cache")
    assert result["ok"] is True
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(site))

    main = tmp_path / "main.py"
    _write_import_main(main)

    srcs, mods = _collect_relative_module_closure(str(main))
    assert "demo_pkg.core" in mods
    assert str(site / "demo_pkg" / "core.py") in srcs


def test_installed_pure_python_package_compiles_without_libpython(tmp_path, monkeypatch):
    project = _write_importable_project(tmp_path / "demo_pkg-0.1")
    site = tmp_path / "site"
    result = install_package(str(project), target_dir=site, cache_dir=tmp_path / "cache")
    assert result["ok"] is True
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(site))

    main = tmp_path / "main.py"
    _write_import_main(main)
    _assert_compiled_import_prints_42(main, tmp_path / "main")


def test_installed_pure_python_wheel_compiles_without_libpython(tmp_path, monkeypatch):
    wheel = _write_importable_wheel(tmp_path / "demo_pkg-0.1-py3-none-any.whl")
    site = tmp_path / "site"
    result = install_package(str(wheel), target_dir=site, cache_dir=tmp_path / "cache")
    assert result["ok"] is True
    assert (site / "demo_pkg" / "core.py").exists()
    assert (tmp_path / "cache" / "demo_pkg" / "core.py").exists()
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(site))

    main = tmp_path / "main.py"
    _write_import_main(main)
    _assert_compiled_import_prints_42(main, tmp_path / "main")


def test_installed_real_cpython_extension_wheel_rejected_by_no_libpython_import(
    tmp_path, monkeypatch
):
    wheel = _write_real_cpython_extension_wheel(tmp_path)
    site = tmp_path / "site"
    result = install_package(
        str(wheel),
        target_dir=site,
        cache_dir=tmp_path / "cache",
        abi="cpython-compat",
    )
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX")
    assert (site / "real_ext_pkg" / ("_native" + ext_suffix)).exists()
    assert (site / "real_ext_pkg" / "pcc-package.json").exists()
    assert result["metadata"]["source_kind"] == "wheel"
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(site))

    main = tmp_path / "main.py"
    main.write_text(
        "from real_ext_pkg.core import answer\n"
        "def main():\n"
        "    print(answer())\n"
        "main()\n",
        encoding="utf-8",
    )

    with pytest.raises(PyPipelineError) as exc:
        compile_python(
            str(main),
            str(tmp_path / "main"),
            libpython_mode="off",
            ir_scaffold_mode="on",
            backend="self",
        )

    msg = str(exc.value)
    assert "PCC-PKG-004" in msg
    assert "_native" in msg
    assert "no-libpython import" in msg


def test_cli_import_real_cpython_extension_package_init_rejected_by_no_libpython(
    tmp_path,
):
    wheel = _write_real_cpython_extension_wheel(tmp_path)
    site = tmp_path / "site"
    result = install_package(
        str(wheel),
        target_dir=site,
        cache_dir=tmp_path / "cache",
        abi="cpython-compat",
    )
    assert result["ok"] is True

    main = tmp_path / "main.py"
    main.write_text(
        "import real_ext_pkg\n"
        "def main():\n"
        "    print(real_ext_pkg.answer())\n"
        "main()\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(tmp_path / "main"),
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=90,
        env=env,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "PCC-PKG-004" in combined
    assert "_native" in combined


def test_installed_pure_python_sdist_compiles_without_libpython(tmp_path, monkeypatch):
    sdist = _write_importable_sdist(tmp_path / "demo_pkg-0.1.tar.gz", tmp_path / "src")
    site = tmp_path / "site"
    result = install_package(str(sdist), target_dir=site, cache_dir=tmp_path / "cache")
    assert result["ok"] is True
    assert (site / "demo_pkg" / "core.py").exists()
    assert (tmp_path / "cache" / "demo_pkg" / "core.py").exists()
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(site))

    main = tmp_path / "main.py"
    _write_import_main(main)
    _assert_compiled_import_prints_42(main, tmp_path / "main")


def test_package_site_rejects_cpython_extension_abi_for_no_libpython(tmp_path, monkeypatch):
    site = tmp_path / "site"
    pkg = site / "demo_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from .core import answer\n",
        encoding="utf-8",
    )
    (pkg / "core.py").write_text(
        "def answer() -> int:\n"
        "    return 42\n",
        encoding="utf-8",
    )
    (pkg / "_native.cpython-314-darwin.so").write_text(
        "libpcc_runtime\n",
        encoding="utf-8",
    )
    (pkg / "pcc-package.json").write_text(
        json.dumps({"abi_mode": "pcc-native", "ok": True}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(site))

    main = tmp_path / "main.py"
    _write_import_main(main)

    with pytest.raises(PyPipelineError) as exc:
        compile_python(
            str(main),
            str(tmp_path / "main"),
            libpython_mode="off",
            ir_scaffold_mode="on",
            backend="self",
        )

    assert "PCC-PKG-004" in str(exc.value)
    assert "pcc-native no-libpython" in str(exc.value)


def test_pcc_pip_install_wheel_participates_in_cli_import_path(tmp_path):
    wheel = _write_importable_wheel(tmp_path / "demo_pkg-0.1-py3-none-any.whl")
    site = tmp_path / "site"
    cache = tmp_path / "cache"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "-m",
            "pip",
            "install",
            str(wheel),
            "--target",
            str(site),
            "--cache-dir",
            str(cache),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    plan = json.loads(proc.stdout)
    assert plan["ok"] is True
    assert plan["installs"][0]["ok"] is True
    assert (site / "demo_pkg" / "__init__.py").exists()
    assert (site / "demo_pkg" / "core.py").exists()

    main = tmp_path / "main.py"
    _write_import_package_main(main)
    _assert_pcc_cli_import_prints(main, tmp_path / "main", site, "43")


def test_pcc1_pip_install_wheel_participates_in_import_site(tmp_path):
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native package install shim")
    wheel = _write_importable_wheel(tmp_path / "demo_pkg-0.1-py3-none-any.whl")
    site = tmp_path / "site"
    cache = tmp_path / "cache"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    env["PCC_PYTHON_IR_PASSES"] = "off"
    env["PCC_PLATFORM_TAG"] = current_platform_tag()
    subprocess.run(
        [
            str(pcc1),
            "-m",
            "pip",
            "install",
            str(wheel),
            "--target",
            str(site),
            "--cache-dir",
            str(cache),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert (site / "demo_pkg" / "__init__.py").exists()
    assert (site / "demo_pkg" / "core.py").exists()

    env["PCC_PACKAGE_SITE"] = str(site)
    main = tmp_path / "main.py"
    _write_import_package_main(main)
    exe = tmp_path / "main_pcc1"
    subprocess.run(
        [
            str(pcc1),
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=90,
        env=env,
    )
    proc = subprocess.run(
        [str(exe)],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert proc.stdout.strip() == "43"


def test_pcc1_pip_install_numpy_name_from_find_links_command_shape(tmp_path):
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native package install shim")

    wheel = _write_numpy_command_shape_wheel(tmp_path / "numpy-2.4.4-py3-none-any.whl")
    site = tmp_path / "site"
    cache = tmp_path / "cache"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    env["PCC_PYTHON_IR_PASSES"] = "off"
    env["PCC_PLATFORM_TAG"] = current_platform_tag()
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pip",
            "install",
            "numpy",
            "--no-index",
            "--find-links",
            str(tmp_path),
            "--target",
            str(site),
            "--cache-dir",
            str(cache),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    plan = json.loads(proc.stdout)
    assert plan["ok"] is True
    assert plan["packages"] == ["numpy"]
    assert plan["installs"][0]["name"] == "numpy"
    assert plan["installs"][0]["source_path"] == str(wheel.resolve())
    assert (site / "numpy" / "__init__.py").exists()
    assert (site / "numpy" / "core.py").exists()


def test_pcc1_pip_install_real_numpy_artifact_opt_in(tmp_path):
    if os.environ.get("PCC_RUN_REAL_NUMPY_INSTALL") != "1":
        pytest.skip("set PCC_RUN_REAL_NUMPY_INSTALL=1 for the real NumPy install gate")
    artifact = os.environ.get("PCC_NUMPY_ARTIFACT")
    if not artifact:
        pytest.skip("set PCC_NUMPY_ARTIFACT to a real NumPy wheel or source artifact")
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native package install shim")

    artifact_path = Path(artifact).expanduser().resolve()
    find_links = artifact_path.parent if artifact_path.is_file() else artifact_path
    site = tmp_path / "site"
    cache = tmp_path / "cache"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    env["PCC_PYTHON_IR_PASSES"] = "off"
    env["PCC_PLATFORM_TAG"] = current_platform_tag()
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pip",
            "install",
            "numpy",
            "--no-index",
            "--find-links",
            str(find_links),
            "--abi",
            "cpython-compat",
            "--target",
            str(site),
            "--cache-dir",
            str(cache),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=300,
        env=env,
    )
    plan = json.loads(proc.stdout)
    assert plan["ok"] is True
    assert plan["installs"][0]["name"] == "numpy"
    assert (site / "numpy").exists()


def test_pcc1_real_numpy_first_import_boundary_opt_in(tmp_path):
    if os.environ.get("PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY") != "1":
        pytest.skip(
            "set PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 for the real NumPy import boundary gate"
        )
    artifact = os.environ.get("PCC_NUMPY_ARTIFACT")
    if not artifact:
        pytest.skip("set PCC_NUMPY_ARTIFACT to a real NumPy wheel or source artifact")
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native package install shim")

    artifact_path = Path(artifact).expanduser().resolve()
    find_links = artifact_path.parent if artifact_path.is_file() else artifact_path
    site = tmp_path / "site"
    cache = tmp_path / "cache"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    env["PCC_PYTHON_IR_PASSES"] = "off"
    env["PCC_PLATFORM_TAG"] = current_platform_tag()
    install_proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pip",
            "install",
            "numpy",
            "--no-index",
            "--find-links",
            str(find_links),
            "--abi",
            "cpython-compat",
            "--target",
            str(site),
            "--cache-dir",
            str(cache),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=300,
        env=env,
    )
    install_plan = json.loads(install_proc.stdout)
    assert install_plan["ok"] is True
    assert (site / "numpy").exists()

    main = tmp_path / "main.py"
    main.write_text(
        "import numpy\n"
        "def main():\n"
        "    print('numpy-imported')\n"
        "main()\n",
        encoding="utf-8",
    )
    exe = tmp_path / "numpy_import"
    compile_env = {**env, "PCC_PACKAGE_SITE": str(site)}
    compile_proc = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=300,
        env=compile_env,
    )
    combined = compile_proc.stdout + compile_proc.stderr
    if compile_proc.returncode != 0:
        assert "PCC-PY-COMPILE-001" in combined
        if "PCC-PKG-004" in combined:
            assert "no-libpython import" in combined
            assert "cpython" in combined
            return
        assert "multi-file compile" in combined
        assert "requires libpython fallback for multi-file compile" in combined
        assert "numpy.f2py.symbolic" in combined
        assert "numpy.f2py.func2subr" in combined
        return

    run_proc = subprocess.run(
        [str(exe)],
        check=True,
        text=True,
        capture_output=True,
        timeout=60,
        env=compile_env,
    )
    assert run_proc.stdout.strip() == "numpy-imported"


def test_pcc1_pip_install_chain_no_host_ext_abi_smoke(tmp_path):
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native ext-abi shim")

    wheel = _write_importable_wheel(tmp_path / "demo_pkg-0.1-py3-none-any.whl")
    site = tmp_path / "site"
    cache = tmp_path / "cache"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    env["PCC_PYTHON_IR_PASSES"] = "off"
    env["PCC_PLATFORM_TAG"] = current_platform_tag()

    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pip",
            "install",
            str(wheel),
            "--target",
            str(site),
            "--cache-dir",
            str(cache),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    install_plan = json.loads(proc.stdout)
    assert install_plan["ok"] is True

    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package",
            "ext-abi",
            "--symbol",
            "Py_Initialize",
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    plan = json.loads(proc.stdout)
    assert plan["ok"] is True
    assert plan["missing_symbols"] == []

    main = tmp_path / "main.py"
    _write_import_package_main(main)
    exe = tmp_path / "main_pcc1"
    subprocess.run(
        [
            str(pcc1),
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=90,
        env={**env, "PCC_PACKAGE_SITE": str(site)},
    )
    proc = subprocess.run(
        [str(exe)],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env={**env, "PCC_PACKAGE_SITE": str(site)},
    )
    assert proc.stdout.strip() == "43"
