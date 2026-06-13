"""pcc.py_stdlib.urllib.parse — narrow native subset.

The public spelling remains ``import urllib.parse``. This module is the
native provider selected by pcc's resolver for the subset needed during
self-host bring-up.
"""
from __future__ import annotations


_HEX = "0123456789ABCDEF"


def _is_unreserved(ch: str) -> bool:
    # RFC 3986 unreserved set: ALPHA / DIGIT / "-" / "." / "_" / "~"
    if ch >= "a" and ch <= "z":
        return True
    if ch >= "A" and ch <= "Z":
        return True
    if ch >= "0" and ch <= "9":
        return True
    if ch == "-" or ch == "." or ch == "_" or ch == "~":
        return True
    return False


def quote(s: str, safe: str = "/") -> str:
    out = ""
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if _is_unreserved(ch):
            out = out + ch
        elif ch in safe:
            out = out + ch
        else:
            b = ord(ch)
            # ASCII path — pcc-Python str is UTF-8; quoting multi-byte
            # characters needs the underlying bytes, which the closed
            # world layer hasn't surfaced yet. ASCII is sufficient for
            # the self-host / py_corpus probes that exercise quote.
            out = out + "%" + _HEX[(b >> 4) & 0xF] + _HEX[b & 0xF]
        i = i + 1
    return out


def _hex_digit_val(ch: str) -> int:
    if ch >= "0" and ch <= "9":
        return ord(ch) - ord("0")
    if ch >= "A" and ch <= "F":
        return ord(ch) - ord("A") + 10
    if ch >= "a" and ch <= "f":
        return ord(ch) - ord("a") + 10
    return -1


def unquote(s: str) -> str:
    out = ""
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "%" and i + 2 < n:
            hi = _hex_digit_val(s[i + 1])
            lo = _hex_digit_val(s[i + 2])
            if hi >= 0 and lo >= 0:
                out = out + chr((hi << 4) | lo)
                i = i + 3
                continue
        out = out + ch
        i = i + 1
    return out


class ParseResult:
    def __init__(
        self,
        scheme: str,
        netloc: str,
        path: str,
        params: str,
        query: str,
        fragment: str,
    ) -> None:
        self.scheme = scheme
        self.netloc = netloc
        self.path = path
        self.params = params
        self.query = query
        self.fragment = fragment

    def __len__(self) -> int:
        return 6

    def __getitem__(self, index: int) -> str:
        if index < 0:
            index = index + 6
        if index == 0:
            return self.scheme
        if index == 1:
            return self.netloc
        if index == 2:
            return self.path
        if index == 3:
            return self.params
        if index == 4:
            return self.query
        if index == 5:
            return self.fragment
        raise IndexError("ParseResult index out of range")

    def _replace(
        self,
        scheme=None,
        netloc=None,
        path=None,
        params=None,
        query=None,
        fragment=None,
    ):
        return ParseResult(
            self.scheme if scheme is None else scheme,
            self.netloc if netloc is None else netloc,
            self.path if path is None else path,
            self.params if params is None else params,
            self.query if query is None else query,
            self.fragment if fragment is None else fragment,
        )

    def _userinfo_hostport(self):
        userinfo = ""
        hostport = self.netloc
        if "@" in hostport:
            userinfo, hostport = hostport.rsplit("@", 1)
        return userinfo, hostport

    @property
    def username(self):
        userinfo, _hostport = self._userinfo_hostport()
        if not userinfo:
            return None
        if ":" in userinfo:
            return userinfo.split(":", 1)[0]
        return userinfo

    @property
    def password(self):
        userinfo, _hostport = self._userinfo_hostport()
        if not userinfo:
            return None
        if ":" not in userinfo:
            return None
        return userinfo.split(":", 1)[1]

    @property
    def hostname(self):
        _userinfo, hostport = self._userinfo_hostport()
        if not hostport:
            return None
        if hostport.startswith("["):
            end = hostport.find("]")
            if end >= 0:
                host = hostport[1:end]
                return host.lower() if host else None
        if ":" in hostport:
            host = hostport.rsplit(":", 1)[0]
        else:
            host = hostport
        return host.lower() if host else None

    @property
    def port(self):
        _userinfo, hostport = self._userinfo_hostport()
        if not hostport:
            return None
        port_text = ""
        if hostport.startswith("["):
            end = hostport.find("]")
            if end >= 0 and end + 1 < len(hostport) and hostport[end + 1] == ":":
                port_text = hostport[end + 2 :]
        elif ":" in hostport:
            port_text = hostport.rsplit(":", 1)[1]
        if not port_text:
            return None
        return int(port_text)

    def geturl(self) -> str:
        out = ""
        if self.scheme:
            out = out + self.scheme + ":"
        if self.netloc:
            out = out + "//" + self.netloc
        out = out + self.path
        if self.params:
            out = out + ";" + self.params
        if self.query:
            out = out + "?" + self.query
        if self.fragment:
            out = out + "#" + self.fragment
        return out


def _first_authority_sep(text: str) -> int:
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "/" or ch == "?" or ch == "#":
            return i
        i += 1
    return -1


def urlparse(url: str, scheme: str = "", allow_fragments: bool = True):
    rest = url
    i = 0
    while i < len(url):
        ch = url[i]
        if ch == ":":
            scheme = url[:i].lower()
            rest = url[i + 1 :]
            break
        if ch == "/" or ch == "?" or ch == "#":
            break
        i += 1

    netloc = ""
    if rest.startswith("//"):
        rest = rest[2:]
        end = _first_authority_sep(rest)
        if end < 0:
            netloc = rest
            rest = ""
        else:
            netloc = rest[:end]
            rest = rest[end:]

    fragment = ""
    if allow_fragments:
        rest, sep, frag = rest.partition("#")
        if sep:
            fragment = frag

    query = ""
    rest, sep, qry = rest.partition("?")
    if sep:
        query = qry

    path = rest
    params = ""
    return ParseResult(scheme, netloc, path, params, query, fragment)
