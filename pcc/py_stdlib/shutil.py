"""pcc.py_stdlib.shutil — narrow ``shutil`` skeleton."""
from __future__ import annotations

from pcc.extern import extern, c_int, c_str


_access: "extern" = extern("access", (c_str, c_int), c_int)


def which(cmd: str) -> str:
    """Find ``cmd`` on ``PATH`` and return its absolute path or the
    empty string. Skeleton — real implementation needs iterating
    PATH entries."""
    raise NotImplementedError(
        "shutil.which awaits PATH environment parsing"
    )


def copyfile(src: str, dst: str) -> str:
    raise NotImplementedError(
        "shutil.copyfile awaits fopen/fread/fwrite extern bindings"
    )


def rmtree(path: str, ignore_errors: bool = False) -> None:
    raise NotImplementedError(
        "shutil.rmtree awaits dirent / unlink / rmdir extern bindings"
    )


def copy(src: str, dst: str) -> str:
    return copyfile(src, dst)


def move(src: str, dst: str) -> str:
    raise NotImplementedError(
        "shutil.move awaits rename extern binding"
    )
