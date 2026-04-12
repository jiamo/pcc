"""pcc.py_stdlib.io — StringIO + BytesIO (minimal)."""
from __future__ import annotations


class StringIO:
    def __init__(self, initial: str = "") -> None:
        self._chunks: list[str] = [initial] if initial else []
        self._pos = 0

    def write(self, s: str) -> int:
        self._chunks.append(s)
        return len(s)

    def getvalue(self) -> str:
        return "".join(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        self._chunks = []

    def read(self) -> str:
        v = self.getvalue()
        r = v[self._pos:]
        self._pos = len(v)
        return r


class BytesIO:
    def __init__(self, initial: bytes = b"") -> None:
        self._buf = bytearray(initial)
        self._pos = 0

    def write(self, b: bytes) -> int:
        self._buf.extend(b)
        return len(b)

    def getvalue(self) -> bytes:
        return bytes(self._buf)

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            r = bytes(self._buf[self._pos:])
            self._pos = len(self._buf)
            return r
        r = bytes(self._buf[self._pos:self._pos + n])
        self._pos += len(r)
        return r

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        self._buf = bytearray()
