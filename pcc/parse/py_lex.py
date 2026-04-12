"""P6C.3 — native Python tokenizer (skeleton).

A hand-written lexer for the Python subset pcc uses. Full CPython
tokenization is ~2000 LoC; this skeleton targets ~800 LoC when
complete. Today it handles indentation tracking, identifiers,
numeric / string literals, the keyword set, and common operators —
enough to parse ``hello.py``-class programs.

Anchor: CPython ``Lib/tokenize.py`` is NOT our parity target — we
ship only what pcc needs. PEP 617 grammar references are used where
they simplify decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator


# Token kinds. Keep in sync with parser expectations.
TK_NEWLINE  = "NEWLINE"
TK_INDENT   = "INDENT"
TK_DEDENT   = "DEDENT"
TK_NAME     = "NAME"
TK_NUMBER   = "NUMBER"
TK_STRING   = "STRING"
TK_OP       = "OP"
TK_KEYWORD  = "KEYWORD"
TK_EOF      = "EOF"


KEYWORDS = frozenset({
    "False", "None", "True",
    "and", "as", "assert", "async", "await", "break", "class",
    "continue", "def", "del", "elif", "else", "except", "finally",
    "for", "from", "global", "if", "import", "in", "is", "lambda",
    "nonlocal", "not", "or", "pass", "raise", "return", "try",
    "while", "with", "yield",
})

# Multi-character operators matched longest-first.
_OPS_MULTI = (
    "**=", "//=", ">>=", "<<=", "...", "->", "**", "//", "<<", ">>",
    "<=", ">=", "==", "!=", "+=", "-=", "*=", "/=", "%=", "&=",
    "|=", "^=", ":=",
)
_OPS_SINGLE = set("+-*/%@&|^~<>=()[]{},:.;")


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    line: int
    col: int


class LexError(Exception):
    pass


class Lexer:
    """Minimal hand-written Python tokenizer.

    Caller usage::

        for tok in Lexer(source, filename="hello.py"):
            ...

    The iterator yields tokens in source order, ending with a single
    ``TK_EOF`` token. Indentation is tracked as virtual INDENT /
    DEDENT tokens so the parser can rely on block boundaries without
    re-counting whitespace.
    """

    def __init__(self, src: str, filename: str = "<input>") -> None:
        self.src = src
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.col = 1
        self._indent_stack: list[int] = [0]
        self._paren_depth = 0
        self._at_line_start = True

    # ------------------------------------------------------ entry

    def __iter__(self) -> Iterator[Token]:
        # Build the full token list eagerly and iterate it. Avoids
        # ``yield`` — ``scripts/audit_selfhost.py`` flags generator
        # functions as self-host blockers (no coroutine state machine
        # in codegen yet).
        return iter(self.tokenize())

    def tokenize(self) -> list[Token]:
        """Run the lexer to completion, returning all tokens."""
        out: list[Token] = []
        while self.pos < len(self.src):
            if self._at_line_start and self._paren_depth == 0:
                self._emit_indent(out)
                self._at_line_start = False
                if self.pos >= len(self.src):
                    break
            ch = self.src[self.pos]
            if ch == "\n":
                self._emit_newline(out)
                continue
            if ch in " \t":
                self.pos += 1
                self.col += 1
                continue
            if ch == "#":
                while self.pos < len(self.src) and self.src[self.pos] != "\n":
                    self.pos += 1
                continue
            if ch == "\\" and self._peek(1) == "\n":
                self.pos += 2
                self.line += 1
                self.col = 1
                continue
            if (
                ch.lower() in "bfru"
                and self._peek(1) in ('"', "'")
            ):
                out.append(self._read_string())
                continue
            if (
                ch.lower() in "bfru"
                and self._peek(1).lower() in "bfru"
                and self._peek(2) in ('"', "'")
            ):
                out.append(self._read_string())
                continue
            if ch.isalpha() or ch == "_":
                out.append(self._read_name())
                continue
            if ch.isdigit() or (
                ch == "." and self._peek(1).isdigit()
            ):
                out.append(self._read_number())
                continue
            if ch in ('"', "'"):
                out.append(self._read_string())
                continue
            out.append(self._read_op())
        if not self._at_line_start:
            out.append(Token(TK_NEWLINE, "\n", self.line, self.col))
        while len(self._indent_stack) > 1:
            self._indent_stack.pop()
            out.append(Token(TK_DEDENT, "", self.line, self.col))
        out.append(Token(TK_EOF, "", self.line, self.col))
        return out

    # ------------------------------------------------------ helpers

    def _peek(self, off: int) -> str:
        p = self.pos + off
        return self.src[p] if p < len(self.src) else ""

    def _emit_indent(self, out: list) -> None:
        depth = 0
        p = self.pos
        while p < len(self.src) and self.src[p] in " \t":
            depth += 1 if self.src[p] == " " else 8
            p += 1
        if p >= len(self.src) or self.src[p] in ("\n", "#"):
            self.pos = p
            self.col = depth + 1
            return
        top = self._indent_stack[-1]
        if depth > top:
            self._indent_stack.append(depth)
            out.append(Token(TK_INDENT, "", self.line, 1))
        while depth < self._indent_stack[-1]:
            self._indent_stack.pop()
            out.append(Token(TK_DEDENT, "", self.line, 1))
        if depth != self._indent_stack[-1]:
            raise LexError(
                f"{self.filename}:{self.line}: inconsistent indentation"
            )
        self.pos = p
        self.col = depth + 1

    def _emit_newline(self, out: list) -> None:
        if self._paren_depth == 0:
            out.append(Token(TK_NEWLINE, "\n", self.line, self.col))
            self._at_line_start = True
        self.pos += 1
        self.line += 1
        self.col = 1

    def _read_name(self) -> Token:
        start = self.pos
        start_col = self.col
        while (
            self.pos < len(self.src)
            and (self.src[self.pos].isalnum() or self.src[self.pos] == "_")
        ):
            self.pos += 1
            self.col += 1
        text = self.src[start:self.pos]
        kind = TK_KEYWORD if text in KEYWORDS else TK_NAME
        return Token(kind, text, self.line, start_col)

    def _read_number(self) -> Token:
        start = self.pos
        start_col = self.col
        # Hex / octal / binary: ``0x…`` / ``0o…`` / ``0b…`` (PEP 3127).
        if (
            self.src[self.pos] == "0"
            and self.pos + 1 < len(self.src)
            and self.src[self.pos + 1] in "xXoObB"
        ):
            base = self.src[self.pos + 1].lower()
            self.pos += 2
            self.col += 2
            if base == "x":
                valid = "0123456789abcdefABCDEF_"
            elif base == "o":
                valid = "01234567_"
            else:
                valid = "01_"
            while self.pos < len(self.src) and self.src[self.pos] in valid:
                self.pos += 1
                self.col += 1
            return Token(
                TK_NUMBER, self.src[start:self.pos], self.line, start_col,
            )
        has_dot = False
        has_exp = False
        while self.pos < len(self.src):
            ch = self.src[self.pos]
            if ch.isdigit():
                self.pos += 1
                self.col += 1
            elif ch == "." and not has_dot and not has_exp:
                has_dot = True
                self.pos += 1
                self.col += 1
            elif ch in ("e", "E") and not has_exp:
                has_exp = True
                self.pos += 1
                self.col += 1
                if self.pos < len(self.src) and self.src[self.pos] in "+-":
                    self.pos += 1
                    self.col += 1
            elif ch in ("_",):
                self.pos += 1
                self.col += 1
            elif ch in ("j", "J"):
                # Imaginary literal suffix — we accept the char so the
                # tokenizer doesn't choke, the frontend doesn't use them.
                self.pos += 1
                self.col += 1
                break
            else:
                break
        return Token(TK_NUMBER, self.src[start:self.pos], self.line, start_col)

    def _read_string(self) -> Token:
        start = self.pos
        start_col = self.col
        # Consume the optional prefix chars (``b``, ``r``, ``f``, ``u``
        # and their 2-char combinations).
        while (
            self.pos < len(self.src)
            and self.src[self.pos].lower() in "bfru"
            and self._peek(0) not in ('"', "'")
        ):
            self.pos += 1
            self.col += 1
        quote = self.src[self.pos]
        # Handle triple-quoted.
        triple = self._peek(1) == quote and self._peek(2) == quote
        if triple:
            self.pos += 3
            self.col += 3
            while self.pos < len(self.src):
                if (
                    self.src[self.pos] == quote
                    and self._peek(1) == quote
                    and self._peek(2) == quote
                ):
                    self.pos += 3
                    self.col += 3
                    return Token(
                        TK_STRING, self.src[start:self.pos],
                        self.line, start_col,
                    )
                if self.src[self.pos] == "\n":
                    self.line += 1
                    self.col = 1
                else:
                    self.col += 1
                self.pos += 1
            raise LexError(
                f"{self.filename}:{self.line}: unterminated triple-quoted string"
            )
        # Single-line.
        self.pos += 1
        self.col += 1
        while self.pos < len(self.src):
            ch = self.src[self.pos]
            if ch == "\\":
                self.pos += 2
                self.col += 2
                continue
            if ch == quote:
                self.pos += 1
                self.col += 1
                return Token(
                    TK_STRING, self.src[start:self.pos],
                    self.line, start_col,
                )
            if ch == "\n":
                raise LexError(
                    f"{self.filename}:{self.line}: unterminated string"
                )
            self.pos += 1
            self.col += 1
        raise LexError(f"{self.filename}:{self.line}: unterminated string at EOF")

    def _read_op(self) -> Token:
        start_col = self.col
        # Try 3-, 2-, then 1-char operators.
        for op in _OPS_MULTI:
            if self.src.startswith(op, self.pos):
                self.pos += len(op)
                self.col += len(op)
                return Token(TK_OP, op, self.line, start_col)
        ch = self.src[self.pos]
        if ch in _OPS_SINGLE:
            if ch in "([{":
                self._paren_depth += 1
            elif ch in ")]}":
                self._paren_depth = max(0, self._paren_depth - 1)
            self.pos += 1
            self.col += 1
            return Token(TK_OP, ch, self.line, start_col)
        raise LexError(
            f"{self.filename}:{self.line}:{self.col}: stray character {ch!r}"
        )
