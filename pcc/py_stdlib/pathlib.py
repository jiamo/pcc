"""pcc.py_stdlib.pathlib — narrow ``PurePath`` / ``Path``.

Uses ``os.path`` under the hood for the join/split logic. The full
Pathlib has ~60 methods; this scaffold covers the 15 or so pcc's
source and build scripts touch.
"""
from __future__ import annotations

from .os import path as _op


class PurePath:
    def __init__(self, *parts) -> None:
        if len(parts) == 0:
            self._raw = ""
        elif len(parts) == 1:
            self._raw = str(parts[0])
        else:
            self._raw = _op.join(*[str(p) for p in parts])

    def __str__(self) -> str:
        return self._raw

    def __repr__(self) -> str:
        return f"PurePath({self._raw!r})"

    def __truediv__(self, other) -> "PurePath":
        return PurePath(_op.join(self._raw, str(other)))

    @property
    def name(self) -> str:
        return _op.basename(self._raw)

    @property
    def parent(self) -> "PurePath":
        return PurePath(_op.dirname(self._raw))

    @property
    def suffix(self) -> str:
        n = self.name
        i = n.rfind(".")
        if i <= 0:
            return ""
        return n[i:]

    @property
    def stem(self) -> str:
        n = self.name
        i = n.rfind(".")
        if i <= 0:
            return n
        return n[:i]


class Path(PurePath):
    def exists(self) -> bool:
        return _op.exists(self._raw)

    def is_file(self) -> bool:
        # Real os.path.isfile would stat; defer until extern stat lands.
        return _op.exists(self._raw)

    def is_dir(self) -> bool:
        # Same caveat as is_file.
        return _op.exists(self._raw)

    def read_text(self, encoding: str = "utf-8") -> str:
        raise NotImplementedError("Path.read_text awaits extern fopen/read")

    def write_text(self, s: str) -> int:
        raise NotImplementedError("Path.write_text awaits extern fopen/write")
