from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from tests.c_testsuite_cases import case_config, read_expected_output, subprocess_env
from tests.self_backend_c_testsuite_common import (
    REPO_ROOT,
    c_testsuite_case_path,
    exact_match_cases,
)

X86_64_LINUX_TRIPLE = "x86_64-unknown-linux-gnu"


def _host_cc() -> str:
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        raise RuntimeError("host C compiler not found in linux x86_64 harness")
    return cc


def _pcc_command() -> list[str]:
    return ["env", "-u", "LC_ALL", "uv", "run", "pcc"]


def _compile_and_run_native(case_path: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    cc = _host_cc()
    config = case_config(case_path)
    with tempfile.TemporaryDirectory(prefix="c_testsuite_linux_native_") as tmpdir:
        binary = Path(tmpdir) / "a.out"
        compile_result = subprocess.run(
            [cc, *config.native_cflags, str(case_path), "-o", str(binary)],
            cwd=REPO_ROOT,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if compile_result.returncode != 0:
            return compile_result
        return subprocess.run(
            [str(binary)],
            cwd=REPO_ROOT,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def _compile_and_run_pcc_llvm_x86_64(case_path: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    config = case_config(case_path)
    with tempfile.TemporaryDirectory(prefix="c_testsuite_linux_pcc_") as tmpdir:
        obj_path = Path(tmpdir) / "case.o"
        binary = Path(tmpdir) / "a.out"
        cmd = [
            *_pcc_command(),
            "--target",
            X86_64_LINUX_TRIPLE,
            "--emit-obj",
            str(obj_path),
        ]
        for cpp_arg in config.cpp_args:
            cmd.extend(["--cpp-arg", cpp_arg])
        cmd.append(str(case_path))
        compile_result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if compile_result.returncode != 0:
            return compile_result
        link_result = subprocess.run(
            [_host_cc(), "-no-pie", str(obj_path), "-o", str(binary)],
            cwd=REPO_ROOT,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if link_result.returncode != 0:
            return link_result
        return subprocess.run(
            [str(binary)],
            cwd=REPO_ROOT,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def _compile_with_pcc_self_x86_64_expect_unsupported(
    case_path: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    config = case_config(case_path)
    with tempfile.TemporaryDirectory(prefix="c_testsuite_linux_self_unsupported_") as tmpdir:
        asm_path = Path(tmpdir) / "case.s"
        cmd = [
            *_pcc_command(),
            "--backend=self",
            "--target",
            X86_64_LINUX_TRIPLE,
            "--emit-asm",
            str(asm_path),
        ]
        for cpp_arg in config.cpp_args:
            cmd.extend(["--cpp-arg", cpp_arg])
        cmd.append(str(case_path))
        return subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def _compile_and_run_pcc_self_x86_64(case_path: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    config = case_config(case_path)
    with tempfile.TemporaryDirectory(prefix="c_testsuite_linux_self_") as tmpdir:
        obj_path = Path(tmpdir) / "case.o"
        binary = Path(tmpdir) / "a.out"
        cmd = [
            *_pcc_command(),
            "--backend=self",
            "--target",
            X86_64_LINUX_TRIPLE,
            "--emit-obj",
            str(obj_path),
        ]
        for cpp_arg in config.cpp_args:
            cmd.extend(["--cpp-arg", cpp_arg])
        cmd.append(str(case_path))
        compile_result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if compile_result.returncode != 0:
            return compile_result
        link_result = subprocess.run(
            [_host_cc(), "-no-pie", str(obj_path), "-o", str(binary)],
            cwd=REPO_ROOT,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if link_result.returncode != 0:
            return link_result
        return subprocess.run(
            [str(binary)],
            cwd=REPO_ROOT,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def _check_llvm_bucket(bucket_size: int, timeout: int) -> int:
    failures: list[str] = []
    for filename in exact_match_cases(limit=bucket_size):
        case_path = c_testsuite_case_path(filename)
        native_result = _compile_and_run_native(case_path, timeout)
        llvm_result = _compile_and_run_pcc_llvm_x86_64(case_path, timeout)
        if native_result.returncode != llvm_result.returncode:
            failures.append(
                f"{filename}: returncode native={native_result.returncode} llvm={llvm_result.returncode}\n"
                f"native stderr:\n{native_result.stderr}\nllvm stderr:\n{llvm_result.stderr}"
            )
            continue
        if native_result.stdout != llvm_result.stdout:
            failures.append(
                f"{filename}: stdout mismatch\n"
                f"native={native_result.stdout!r}\nllvm={llvm_result.stdout!r}"
            )
            continue
        if native_result.stderr != llvm_result.stderr:
            failures.append(
                f"{filename}: stderr mismatch\n"
                f"native={native_result.stderr!r}\nllvm={llvm_result.stderr!r}"
            )
            continue
        expected = read_expected_output(case_path)
        if expected and llvm_result.stdout != expected:
            failures.append(
                f"{filename}: output mismatch vs .expected\n"
                f"expected={expected!r}\nllvm={llvm_result.stdout!r}"
            )
    if failures:
        for failure in failures:
            print(failure)
            print("-" * 60)
        return 1
    print(f"linux x86_64 llvm bucket passed: {bucket_size} cases")
    return 0


def _check_self_unsupported_bucket(bucket_size: int, timeout: int) -> int:
    failures: list[str] = []
    for filename in exact_match_cases(limit=bucket_size):
        case_path = c_testsuite_case_path(filename)
        result = _compile_with_pcc_self_x86_64_expect_unsupported(case_path, timeout)
        if result.returncode == 0:
            failures.append(
                f"{filename}: self backend unexpectedly succeeded on unsupported linux x86_64 target"
            )
            continue
        combined = result.stdout + "\n" + result.stderr
        if (
            "not translated yet" not in combined
            and "only supports" not in combined
            and "unknown instruction kind" not in combined
            and "unknown terminator kind" not in combined
            and "has no emitter for target triple" not in combined
        ):
            failures.append(
                f"{filename}: unsupported-target failure did not mention expected boundary\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
    if failures:
        for failure in failures:
            print(failure)
            print("-" * 60)
        return 1
    print(f"linux x86_64 self unsupported bucket passed: {bucket_size} cases")
    return 0


def _check_self_partial_bucket(bucket_size: int, timeout: int) -> int:
    failures: list[str] = []
    supported = 0
    unsupported = 0
    for filename in exact_match_cases(limit=bucket_size):
        case_path = c_testsuite_case_path(filename)
        native_result = _compile_and_run_native(case_path, timeout)
        self_result = _compile_and_run_pcc_self_x86_64(case_path, timeout)
        if self_result.returncode != 0:
            combined = self_result.stdout + "\n" + self_result.stderr
            if (
                "not translated yet" in combined
                or "only supports" in combined
                or "unknown instruction kind" in combined
                or "unknown terminator kind" in combined
                or "has no emitter for target triple" in combined
            ):
                unsupported += 1
                continue
            failures.append(
                f"{filename}: unexpected self-backend failure\n"
                f"stdout:\n{self_result.stdout}\n"
                f"stderr:\n{self_result.stderr}"
            )
            continue
        supported += 1
        if native_result.returncode != self_result.returncode:
            failures.append(
                f"{filename}: returncode native={native_result.returncode} self={self_result.returncode}\n"
                f"native stderr:\n{native_result.stderr}\nself stderr:\n{self_result.stderr}"
            )
            continue
        if native_result.stdout != self_result.stdout:
            failures.append(
                f"{filename}: stdout mismatch\n"
                f"native={native_result.stdout!r}\nself={self_result.stdout!r}"
            )
            continue
        if native_result.stderr != self_result.stderr:
            failures.append(
                f"{filename}: stderr mismatch\n"
                f"native={native_result.stderr!r}\nself={self_result.stderr!r}"
            )
            continue
        expected = read_expected_output(case_path)
        if expected and self_result.stdout != expected:
            failures.append(
                f"{filename}: output mismatch vs .expected\n"
                f"expected={expected!r}\nself={self_result.stdout!r}"
            )
    if failures:
        for failure in failures:
            print(failure)
            print("-" * 60)
        return 1
    print(
        f"linux x86_64 self partial bucket passed: {bucket_size} cases "
        f"(supported={supported}, unsupported={unsupported})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("llvm-native-exact", "self-unsupported", "self-partial"),
        default="llvm-native-exact",
    )
    parser.add_argument("--bucket-size", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    if args.mode == "llvm-native-exact":
        return _check_llvm_bucket(args.bucket_size, args.timeout)
    if args.mode == "self-partial":
        return _check_self_partial_bucket(args.bucket_size, args.timeout)
    return _check_self_unsupported_bucket(args.bucket_size, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
