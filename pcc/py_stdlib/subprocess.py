"""pcc.py_stdlib.subprocess — narrow ``subprocess`` skeleton.

Scope: ``run`` / ``check_output`` / ``check_call`` / ``Popen`` shape.
Implementation relies on extern ``posix_spawn`` + ``waitpid`` + pipe
FD plumbing; the interpreted fallback here uses CPython's real
subprocess so development proceeds even before the extern bindings
land.
"""
from __future__ import annotations

from pcc.extern import extern, c_int64, c_ptr


py_subprocess_check_output = extern(
    "py_subprocess_check_output",
    (c_ptr, c_int64, c_int64),
    c_ptr,
)
py_subprocess_run = extern(
    "py_subprocess_run",
    (c_ptr, c_int64, c_int64),
    c_int64,
)


class CalledProcessError(Exception):
    def __init__(self, returncode: int, cmd, output=None, stderr=None) -> None:
        super().__init__(f"command {cmd!r} returned non-zero exit {returncode}")
        self.returncode = returncode
        self.cmd = cmd
        self.output = output
        self.stderr = stderr


class CompletedProcess:
    def __init__(self, args, returncode: int, stdout=None, stderr=None) -> None:
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def check_returncode(self) -> None:
        if self.returncode != 0:
            raise CalledProcessError(
                self.returncode, self.args, self.stdout, self.stderr,
            )


PIPE = -1
STDOUT = -2
DEVNULL = -3


def run(args, *, check=False, capture_output=False, text=False,
        input=None, stdout=None, stderr=None, timeout=None,
        encoding=None, env=None, cwd=None, **kwargs) -> CompletedProcess:
    host_subprocess = __import__("subprocess")
    proc = host_subprocess.run(
        args,
        check=False,
        capture_output=capture_output,
        text=text,
        input=input,
        stdout=stdout,
        stderr=stderr,
        timeout=timeout,
        encoding=encoding,
        env=env,
        cwd=cwd,
        **kwargs,
    )
    result = CompletedProcess(proc.args, proc.returncode, proc.stdout, proc.stderr)
    if check:
        result.check_returncode()
    return result


def check_output(args, **kwargs) -> bytes:
    host_subprocess = __import__("subprocess")
    return host_subprocess.check_output(args, **kwargs)


def check_call(args, **kwargs) -> int:
    host_subprocess = __import__("subprocess")
    return host_subprocess.check_call(args, **kwargs)


class Popen:
    def __init__(self, args, **kwargs) -> None:
        host_subprocess = __import__("subprocess")
        self._popen = host_subprocess.Popen(args, **kwargs)

    def wait(self, timeout=None):
        return self._popen.wait(timeout=timeout)

    def communicate(self, input=None, timeout=None):
        return self._popen.communicate(input=input, timeout=timeout)

    def poll(self):
        return self._popen.poll()
