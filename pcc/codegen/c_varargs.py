"""Structured varargs textual lowering for the C frontend.

llvmlite cannot directly emit LLVM ``va_arg`` in the subset pcc uses, so the C
frontend historically rewrote helper calls with a regex.  This module keeps the
rewrite but gives it a structured report surface.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Iterable, Optional

_PCC_VAARG_DECL_RE = re.compile(
    r'^declare .+@(?:"__pcc_va_arg_\d+"|__pcc_va_arg_\d+)\(.+\)\n?', re.M
)
_PCC_VAARG_CALL_RE = re.compile(
    r"^(?P<lhs>\s*%\S+)\s*=\s*call\s+"
    r"(?P<rettype>[^()\s]+)\s+(?:\([^)]*\)\s+)?"
    r'@(?:"(?P<qname>__pcc_va_arg_\d+)"|(?P<name>__pcc_va_arg_\d+))\('
    r'(?P<argtype>.+?)\s+(?P<argval>%".+?"|%\S+)\)$',
    re.M,
)


@dataclass(frozen=True)
class VarargsRewrite:
    helper: str
    lhs: str
    arg_type: str
    arg_value: str
    result_type: str

    def to_json(self) -> dict[str, str]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class VarargsRewriteReport:
    rewrites: tuple[VarargsRewrite, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "schema": "pcc.c.varargs_rewrite.v1",
            "count": len(self.rewrites),
            "rewrites": [r.to_json() for r in self.rewrites],
        }

    def format_json(self) -> str:
        return json.dumps(self.to_json(), indent=2, sort_keys=True)


def postprocess_varargs_ir(text: str, *, report: Optional[list[VarargsRewrite]] = None) -> str:
    text = _PCC_VAARG_DECL_RE.sub("", text)

    def repl(match):
        helper = match.group("qname") or match.group("name") or ""
        entry = VarargsRewrite(
            helper=helper,
            lhs=match.group("lhs").strip(),
            arg_type=match.group("argtype").strip(),
            arg_value=match.group("argval").strip(),
            result_type=match.group("rettype").strip(),
        )
        if report is not None:
            report.append(entry)
        return f"{entry.lhs} = va_arg {entry.arg_type} {entry.arg_value}, {entry.result_type}"

    return _PCC_VAARG_CALL_RE.sub(repl, text)


def build_report(rewrites: Iterable[VarargsRewrite]) -> VarargsRewriteReport:
    return VarargsRewriteReport(tuple(rewrites))
