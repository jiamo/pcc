"""pcc.py_stdlib.hashlib — small pure Python SHA-256 subset."""
from __future__ import annotations

_K = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2,
]


def _rotr(x, n):
    return ((x >> n) | ((x << (32 - n)) & 0xffffffff)) & 0xffffffff


def _as_bytes(data):
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, str):
        return data.encode()
    return bytes(data)


class _SHA256:
    digest_size = 32
    block_size = 64
    name = "sha256"

    def __init__(self, data=b""):
        self._h = [
            0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
            0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
        ]
        self._buf = b""
        self._counter = 0
        if data:
            self.update(data)

    def copy(self):
        other = _SHA256()
        other._h = list(self._h)
        other._buf = self._buf
        other._counter = self._counter
        return other

    def update(self, data):
        data = _as_bytes(data)
        self._counter += len(data)
        data = self._buf + data
        i = 0
        while i + 64 <= len(data):
            self._compress(data[i:i+64])
            i += 64
        self._buf = data[i:]
        return None

    def _compress(self, block):
        w = []
        for i in range(16):
            j = i * 4
            w.append((block[j] << 24) | (block[j+1] << 16) | (block[j+2] << 8) | block[j+3])
        for i in range(16, 64):
            s0 = _rotr(w[i-15], 7) ^ _rotr(w[i-15], 18) ^ (w[i-15] >> 3)
            s1 = _rotr(w[i-2], 17) ^ _rotr(w[i-2], 19) ^ (w[i-2] >> 10)
            w.append((w[i-16] + s0 + w[i-7] + s1) & 0xffffffff)

        a,b,c,d,e,f,g,h = self._h
        for i in range(64):
            S1 = _rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25)
            ch = (e & f) ^ ((~e) & g)
            temp1 = (h + S1 + ch + _K[i] + w[i]) & 0xffffffff
            S0 = _rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22)
            maj = (a & b) ^ (a & c) ^ (b & c)
            temp2 = (S0 + maj) & 0xffffffff
            h = g
            g = f
            f = e
            e = (d + temp1) & 0xffffffff
            d = c
            c = b
            b = a
            a = (temp1 + temp2) & 0xffffffff
        self._h = [
            (self._h[0] + a) & 0xffffffff,
            (self._h[1] + b) & 0xffffffff,
            (self._h[2] + c) & 0xffffffff,
            (self._h[3] + d) & 0xffffffff,
            (self._h[4] + e) & 0xffffffff,
            (self._h[5] + f) & 0xffffffff,
            (self._h[6] + g) & 0xffffffff,
            (self._h[7] + h) & 0xffffffff,
        ]

    def digest(self):
        clone = self.copy()
        bit_len = clone._counter * 8
        clone.update(b"\x80")
        while len(clone._buf) != 56:
            if len(clone._buf) > 56:
                clone.update(b"\x00" * (64 - len(clone._buf)))
            else:
                clone.update(b"\x00")
        clone.update(bit_len.to_bytes(8, "big"))
        return b"".join(x.to_bytes(4, "big") for x in clone._h)

    def hexdigest(self):
        return "".join(f"{b:02x}" for b in self.digest())


def sha256(data=b""):
    return _SHA256(data)


def new(name, data=b""):
    n = name.lower().replace("-", "")
    if n == "sha256":
        return sha256(data)
    raise ValueError("unsupported hash type: " + name)


algorithms_guaranteed = {"sha256"}
algorithms_available = algorithms_guaranteed
