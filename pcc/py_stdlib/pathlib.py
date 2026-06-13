"""pcc.py_stdlib.pathlib — narrow ``PurePath`` / ``Path``.

Uses ``os.path`` under the hood for the join/split logic. The full
Pathlib has ~60 methods; this scaffold covers the 15 or so pcc's
source and build scripts touch.
"""
from __future__ import annotations

from os import path as _op


class PurePath:
    def __init__(self, path: str = "", *extra) -> None:
        raw = str(path)
        for part in extra:
            raw = _op.join(raw, str(part))
        self._raw = raw

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

    def with_suffix(self, suffix: str) -> "PurePath":
        base, _old = _op.splitext(self._raw)
        return PurePath(base + suffix)

    def with_name(self, name: str) -> "PurePath":
        parent = _op.dirname(self._raw)
        if parent:
            return PurePath(_op.join(parent, name))
        return PurePath(name)

    def match(self, pattern: str) -> bool:
        if pattern.startswith("*."):
            return self.name.endswith(pattern[1:])
        return self.name == pattern or self._raw == pattern


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
        with open(self._raw, "r", encoding=encoding) as f:
            return f.read()

    def write_text(self, s: str, encoding: str = "utf-8") -> int:
        with open(self._raw, "w", encoding=encoding) as f:
            return f.write(s)
