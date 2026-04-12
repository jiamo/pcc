"""pcc.py_stdlib.string — constants pcc's code actually uses."""
from __future__ import annotations

ascii_lowercase: str = "abcdefghijklmnopqrstuvwxyz"
ascii_uppercase: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ascii_letters: str = ascii_lowercase + ascii_uppercase
digits: str = "0123456789"
hexdigits: str = "0123456789abcdefABCDEF"
octdigits: str = "01234567"
punctuation: str = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
whitespace: str = " \t\n\r\x0b\x0c"
printable: str = digits + ascii_letters + punctuation + whitespace
