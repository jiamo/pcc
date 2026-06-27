"""Reusable skeleton for pcc-native package end-to-end gates.

One function drives the whole acquire/build/run pipeline
(docs/design/pcc-package-model.md) for ANY distribution: install it into a fresh
pcc-native site, compile a driver program with a no-libpython self-backend pcc1,
run the produced binary in isolation, and assert its output plus the absence of
any libpython / host-CPython linkage.

It is package-name agnostic on purpose: the package, the driver source, and the
expected output are parameters, and there is no per-package branching. The
pure-Python ``wheel`` gate uses it to prove the mechanism is generic (a second,
unrelated distribution flows through the same pipeline as numpy); the numpy gate
reuses the identical helper (缝合) with only those three parameters changed.
"""
from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def pcc1_binary() -> Path:
    env_path = os.environ.get("PCC1_BINARY")
    if env_path:
        return Path(env_path)
    return REPO / "build" / "bootstrap" / "pcc1"


def install_pcc_native(
    pcc1: Path,
    package: str,
    *,
    find_links,
    target: Path,
    timeout: int = 300,
) -> dict:
    """Install ``package`` into ``target`` as a pcc-native site.

    Drive the compiled pcc1's own local pip shim.  This is the literal code path
    documented by the package model, including pcc1's native source builder;
    calling the host ``install_package`` helper here used to bypass that layer
    and made the E2E skeleton incapable of proving a C-extension source build.
    No index/network: resolution is local (direct path / ``find_links``) only.
    """
    cache = target.parent / "package-cache"
    command = [
        str(pcc1),
        "-m",
        "pip",
        "install",
        package,
        "--no-index",
        "--abi",
        "pcc-native",
        "--target",
        str(target),
        "--cache-dir",
        str(cache),
    ]
    for link in find_links:
        command.extend(["--find-links", str(link)])
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    env["PCC_HOST_PCC"] = "/usr/bin/false"
    proc = subprocess.run(
        command,
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"pcc1 installer returned non-JSON for {package!r} "
            f"(exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        ) from exc
    assert proc.returncode == 0 and report.get("ok"), (
        f"pcc1 pcc-native install of {package!r} failed (exit {proc.returncode}):\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    return report


def compile_run_assert_no_libpython(
    pcc1: Path,
    tmp_path: Path,
    *,
    driver_src: str,
    expected_stdout: str,
    compile_site: str,
    label: str = "package",
    runtime_site: str = "",
    compile_timeout: int = 600,
    run_timeout: int = 120,
) -> dict:
    """Compile ``driver_src`` with a no-libpython pcc1, run it, assert output.

    The reusable run layer of the skeleton, independent of how the site was
    produced: pass ``compile_site`` (os.pathsep-joined) whether it came from a
    fresh pcc-native install (pure-Python wheel gate) or a prebuilt site (numpy
    gate). Asserts pcc1 compile ok, the binary prints exactly ``expected_stdout``,
    and ``otool -L`` shows no libpython / python3 edge. ``runtime_site`` defaults
    to "" to prove the artifact is self-contained (closed-world lowering compiles
    the imported modules in).
    """
    main = tmp_path / "driver.py"
    main.write_text(driver_src, encoding="utf-8")
    app = tmp_path / "app"

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = compile_site
    compile_proc = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(app),
        ],
        text=True,
        capture_output=True,
        timeout=compile_timeout,
        env=env,
    )
    assert compile_proc.returncode == 0, (
        f"pcc1 compile of {label!r} driver failed:\n"
        f"{compile_proc.stdout}\n{compile_proc.stderr}"
    )
    assert app.is_file(), f"pcc1 produced no binary for {label!r}"

    run_env = os.environ.copy()
    run_env.pop("LC_ALL", None)
    run_env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    run_env["PYTHONPATH"] = ""
    run_env["PCC_PACKAGE_SITE"] = runtime_site
    run_proc = subprocess.run(
        [str(app)], text=True, capture_output=True, timeout=run_timeout, env=run_env
    )
    assert run_proc.returncode == 0, (
        f"pcc1-built {label!r} binary exited {run_proc.returncode}:\n"
        f"{run_proc.stdout}\n{run_proc.stderr}"
    )
    assert run_proc.stdout.strip() == expected_stdout, run_proc.stdout

    otool = subprocess.run(
        ["otool", "-L", str(app)], text=True, capture_output=True, timeout=60
    )
    assert otool.returncode == 0, otool.stderr
    lowered = otool.stdout.lower()
    assert "libpython" not in lowered, otool.stdout
    assert "python3" not in lowered, otool.stdout
    return {"app": app}


def run_package_e2e(
    pcc1: Path,
    tmp_path: Path,
    *,
    package: str,
    find_links,
    driver_src: str,
    expected_stdout: str,
    compile_site_extra=(),
    runtime_site="",
    compile_timeout: int = 600,
    run_timeout: int = 120,
) -> dict:
    """Install ``package`` pcc-native, then compile+run ``driver_src`` via pcc1.

    The full skeleton: acquire/build (install into a fresh site) + the shared
    run layer (``compile_run_assert_no_libpython``). The numpy gate reuses that
    same run layer directly against a prebuilt site.
    """
    site = tmp_path / "site"
    install = install_pcc_native(
        pcc1,
        package,
        find_links=find_links,
        target=site,
        timeout=compile_timeout,
    )
    assert install.get("ok"), f"pcc-native install of {package!r} failed: {install}"

    compile_site = os.pathsep.join([str(site), *(str(p) for p in compile_site_extra)])
    result = compile_run_assert_no_libpython(
        pcc1,
        tmp_path,
        driver_src=driver_src,
        expected_stdout=expected_stdout,
        compile_site=compile_site,
        label=package,
        runtime_site=runtime_site,
        compile_timeout=compile_timeout,
        run_timeout=run_timeout,
    )
    result["site"] = site
    result["install"] = install
    return result
