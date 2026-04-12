"""pcc.py_stdlib.subprocess — narrow ``subprocess`` skeleton.

Scope: ``run`` / ``check_output`` / ``check_call`` / ``Popen`` shape.
Implementation relies on extern ``posix_spawn`` + ``waitpid`` + pipe
FD plumbing; the interpreted fallback here uses CPython's real
subprocess so development proceeds even before the extern bindings
land.
"""
from __future__ import annotations


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
    raise NotImplementedError(
        "subprocess.run awaits the posix_spawn + waitpid extern bindings"
    )


def check_output(args, **kwargs) -> bytes:
    raise NotImplementedError(
        "subprocess.check_output awaits the pipe-FD extern bindings"
    )


def check_call(args, **kwargs) -> int:
    raise NotImplementedError(
        "subprocess.check_call awaits the posix_spawn + waitpid extern bindings"
    )


class Popen:
    def __init__(self, args, **kwargs) -> None:
        raise NotImplementedError(
            "subprocess.Popen awaits the posix_spawn + waitpid extern bindings"
        )
