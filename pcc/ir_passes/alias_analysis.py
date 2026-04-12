"""AliasAnalysis boundary: conservative may-alias queries.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/Analysis/AliasAnalysis.h``
  defines :cpp:class:`llvm::AliasAnalysis` with the four-valued result
  :cpp:enum:`llvm::AliasResult` — ``NoAlias``, ``MayAlias``,
  ``PartialAlias``, ``MustAlias``. Upstream composes multiple
  providers (``BasicAA``, ``TBAA``, ``ScopedNoAliasAA``, ...) and
  returns the strongest agreed answer.

The AA boundary here is deliberately a lowest-common-denominator:
every query defaults to ``MayAlias`` unless we can prove otherwise
locally. We then add a small set of sound rules that mirror BasicAA's
easy wins:

- identical pointers               → MustAlias
- distinct non-escaping allocas    → NoAlias
- distinct globals                 → NoAlias
- alloca vs global                 → NoAlias (different address spaces)
- alloca vs function argument      → NoAlias (callee stack vs caller memory)

The interface is designed to be extended incrementally — more
precise analyses (TBAA, CFL-AA, ScopedAA) are allowed to plug in and
tighten the result without the callers needing to change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

import llvmlite.binding as llvm

from .manager import AnalysisKey, AnalysisManager, AnalysisResult, PreservedAnalyses


class AliasResult(Enum):
    NoAlias = "no-alias"
    MayAlias = "may-alias"
    PartialAlias = "partial-alias"
    MustAlias = "must-alias"


@dataclass(frozen=True)
class MemoryLocation:
    """A narrow description of a memory location."""

    pointer_name: str
    kind: str  # "alloca" | "global" | "argument" | "unknown"


class AliasAnalysis:
    """Queryable AA oracle over a module."""

    def __init__(self, module: llvm.ModuleRef) -> None:
        self._allocas: set[str] = set()
        self._globals: set[str] = set()
        self._arguments: set[str] = set()
        self._aliases: dict[str, str] = {}
        self._alias_candidates: dict[str, set[str]] = {}
        self._scan(module)

    def _scan(self, module: llvm.ModuleRef) -> None:
        for g in module.global_variables:
            if g.name:
                self._globals.add(g.name)
        for fn in module.functions:
            if fn.is_declaration:
                continue
            for arg in fn.arguments:
                if arg.name:
                    self._arguments.add(arg.name)
            for block in fn.blocks:
                for inst in block.instructions:
                    text = str(inst).strip()
                    m = re.match(
                        r"^\s*%([\w\.]+)\s*=\s*alloca\b", text
                    )
                    if m:
                        self._allocas.add(m.group(1))
                        continue
                    m = re.match(
                        r"^\s*%([\w\.]+)\s*=\s*bitcast\s+ptr\s+[@%]([\w\.]+)\s+to\s+ptr\b",
                        text,
                    )
                    if m:
                        self._alias_candidates.setdefault(m.group(1), set()).add(
                            self._canonical(m.group(2))
                        )
                        continue
                    m = re.match(
                        r"^\s*%([\w\.]+)\s*=\s*getelementptr\b.+?,\s*ptr\s+[@%]([\w\.]+)"
                        r"(?:,\s*i\d+\s+0)+\s*$",
                        text,
                    )
                    if m:
                        self._alias_candidates.setdefault(m.group(1), set()).add(
                            self._canonical(m.group(2))
                        )
        self._aliases = {
            name: next(iter(roots))
            for name, roots in self._alias_candidates.items()
            if (
                name not in self._allocas
                and name not in self._globals
                and name not in self._arguments
                and len(roots) == 1
            )
        }

    def classify(self, pointer_name: str) -> MemoryLocation:
        pointer_name = self._canonical(pointer_name)
        if pointer_name in self._allocas:
            return MemoryLocation(pointer_name, "alloca")
        if pointer_name in self._globals:
            return MemoryLocation(pointer_name, "global")
        if pointer_name in self._arguments:
            return MemoryLocation(pointer_name, "argument")
        return MemoryLocation(pointer_name, "unknown")

    def _canonical(self, pointer_name: str) -> str:
        seen: set[str] = set()
        current = pointer_name
        while current not in seen and current in self._aliases:
            seen.add(current)
            current = self._aliases[current]
        return current

    def alias(self, a: MemoryLocation, b: MemoryLocation) -> AliasResult:
        if a.pointer_name == b.pointer_name:
            return AliasResult.MustAlias

        # Two distinct allocas never alias.
        if a.kind == "alloca" and b.kind == "alloca":
            return AliasResult.NoAlias
        # Two distinct globals never alias.
        if a.kind == "global" and b.kind == "global":
            return AliasResult.NoAlias
        # An alloca and a global are in different address spaces for
        # BasicAA's purposes.
        if {a.kind, b.kind} == {"alloca", "global"}:
            return AliasResult.NoAlias
        # A pointer argument cannot alias an alloca created in the
        # current callee frame.
        if {a.kind, b.kind} == {"alloca", "argument"}:
            return AliasResult.NoAlias

        return AliasResult.MayAlias

    def alias_names(self, a: str, b: str) -> AliasResult:
        """Convenience: classify + alias in one call."""
        return self.alias(self.classify(a), self.classify(b))


class AliasAnalysisResult(AnalysisResult):
    KEY = AnalysisKey("alias-analysis")

    def __init__(self, aa: AliasAnalysis) -> None:
        self.aa = aa

    def invalidate(self, ir_unit, preserved: PreservedAnalyses) -> bool:
        return not preserved.preserves(type(self).KEY)


def register_alias_analysis(am: AnalysisManager) -> None:
    am.register(
        AliasAnalysisResult.KEY,
        lambda module: AliasAnalysisResult(AliasAnalysis(module)),
    )
