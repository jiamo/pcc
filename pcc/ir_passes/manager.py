"""Pass manager and analysis manager for pcc's IR pass framework.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/IR/PassManager.h`` defines
  ``PassManager<IRUnitT, AnalysisManagerT, ExtraArgTs...>``, the
  ``PreservedAnalyses`` bitset, and the analysis manager contract. The
  Python runtime here only mirrors the subset we actually need:

  - module / function / loop pass tiers,
  - an analysis cache keyed by ``(AnalysisKey, IRUnit)``,
  - a ``PreservedAnalyses`` object that can be ``all()``, ``none()``, or a
    finite set, and drives invalidation when a pass reports changes.

We intentionally do **not** copy LLVM's template metaprogramming. The
pass framework here is small enough that dynamic dispatch is sufficient
and keeps the Python side readable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import llvmlite.binding as llvm
import llvmlite.ir as ir


# ---------------------------------------------------------------------------
# PreservedAnalyses
# ---------------------------------------------------------------------------


class PreservedAnalyses:
    """Tracks which analyses stay valid after a pass runs.

    Mirrors ``llvm::PreservedAnalyses`` (see PassManager.h:141). Three
    useful forms:

    - ``PreservedAnalyses.all()``: pass made no changes that invalidate
      analyses. The analysis cache stays intact.
    - ``PreservedAnalyses.none()``: pass invalidated everything.
    - ``PreservedAnalyses({DomTreeAnalysis, LoopInfo})``: pass explicitly
      preserved a finite set; everything else gets dropped.
    """

    __slots__ = ("_all", "_preserved")

    def __init__(self, preserved: Iterable[AnalysisKey] | None = None, *, all_preserved: bool = False):
        self._all = bool(all_preserved)
        self._preserved: set[AnalysisKey] = set(preserved or ())

    @classmethod
    def all(cls) -> "PreservedAnalyses":
        return cls(all_preserved=True)

    @classmethod
    def none(cls) -> "PreservedAnalyses":
        return cls()

    def preserves(self, key: AnalysisKey) -> bool:
        return self._all or key in self._preserved

    def preserve(self, key: AnalysisKey) -> None:
        if not self._all:
            self._preserved.add(key)

    def __repr__(self) -> str:
        if self._all:
            return "PreservedAnalyses.all()"
        if not self._preserved:
            return "PreservedAnalyses.none()"
        return f"PreservedAnalyses({sorted(k.name for k in self._preserved)})"


# ---------------------------------------------------------------------------
# Analysis key / manager
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalysisKey:
    """Stable key identifying an analysis type.

    LLVM uses a per-analysis-class ``AnalysisKey`` static object (see
    PassManagerInternal.h:88). We keep the same idea with a frozen
    dataclass: the value equality + hashability matches what LLVM does
    by address on the static variable.
    """

    name: str


class AnalysisResult:
    """Base class for an analysis's cached result.

    Subclasses should implement ``invalidate(ir_unit, preserved)``; the
    default says "stay valid whenever the pass claims to preserve me".
    LLVM's equivalent is ``Result::invalidate`` defined per-analysis
    (e.g. DominatorTreeAnalysis::Result::invalidate in
    /tmp/llvm-src/llvm-20.1.8.src/lib/IR/Dominators.cpp).
    """

    #: Subclasses override with the module-level AnalysisKey sentinel.
    KEY: AnalysisKey = AnalysisKey("analysis-result-base")

    def invalidate(self, ir_unit: Any, preserved: PreservedAnalyses) -> bool:
        return not preserved.preserves(type(self).KEY)


class AnalysisManager:
    """Per-IR-unit analysis cache.

    Keyed by ``(AnalysisKey, id(ir_unit))``. Passes register analysis
    *computation functions* (``compute(ir_unit) -> AnalysisResult``);
    the manager lazily runs them on first request and caches the
    result until invalidation.
    """

    def __init__(self) -> None:
        self._computations: dict[AnalysisKey, Callable[[Any], AnalysisResult]] = {}
        self._cache: dict[tuple[AnalysisKey, int], AnalysisResult] = {}

    def register(
        self,
        key: AnalysisKey,
        compute: Callable[[Any], AnalysisResult],
    ) -> None:
        self._computations[key] = compute

    def get(self, key: AnalysisKey, ir_unit: Any) -> AnalysisResult:
        cache_key = (key, id(ir_unit))
        if cache_key in self._cache:
            return self._cache[cache_key]
        compute = self._computations.get(key)
        if compute is None:
            raise KeyError(f"analysis {key.name!r} not registered")
        result = compute(ir_unit)
        self._cache[cache_key] = result
        return result

    def invalidate(self, ir_unit: Any, preserved: PreservedAnalyses) -> None:
        """Drop cached results that aren't preserved.

        Matches the behavior at PassManager.h:291 (``invalidate``):
        walk all cached results for this IR unit and ask each one
        whether ``preserved`` keeps it valid.
        """
        unit_id = id(ir_unit)
        to_drop: list[tuple[AnalysisKey, int]] = []
        for cache_key, result in self._cache.items():
            if cache_key[1] != unit_id:
                continue
            if result.invalidate(ir_unit, preserved):
                to_drop.append(cache_key)
        for cache_key in to_drop:
            self._cache.pop(cache_key, None)

    def clear(self) -> None:
        self._cache.clear()


# ---------------------------------------------------------------------------
# Pass base classes
# ---------------------------------------------------------------------------


class ModulePass(ABC):
    """An LLVM module-level pass.

    Subclasses implement ``run(module, am) -> PreservedAnalyses`` and
    set ``name`` to the LLVM pass name (e.g. ``"globaldce"``).
    """

    #: LLVM pass name used for reporting and ablation control.
    name: str = "<anonymous>"

    @abstractmethod
    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses: ...


class FunctionPass(ABC):
    """An LLVM function-level pass.

    ``run(function, am) -> PreservedAnalyses``; the manager applies it
    to every function in the module in deterministic order and
    aggregates preserved-analyses.
    """

    name: str = "<anonymous>"

    @abstractmethod
    def run(
        self,
        function: llvm.ValueRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses: ...


class LoopPass(ABC):
    """A loop-level pass (subset — LoopInfo driven).

    Loop passes operate on individual loop nests within a function.
    The manager drives them by walking a LoopInfo analysis and
    invoking ``run(loop, am)`` for each loop in bottom-up order.
    """

    name: str = "<anonymous>"

    @abstractmethod
    def run(
        self,
        loop: Any,
        am: AnalysisManager,
    ) -> PreservedAnalyses: ...


# ---------------------------------------------------------------------------
# Pass manager
# ---------------------------------------------------------------------------


@dataclass
class IRPassManager:
    """Top-level module pass manager.

    Order of execution is preserved in insertion order. Disabled pass
    names are skipped. For function-tier passes, ``add_function_pass``
    schedules a function-pass adaptor that runs the pass on every
    function in the module.
    """

    module_passes: list[ModulePass] = field(default_factory=list)
    disabled_names: set[str] = field(default_factory=set)

    def add(self, pass_: ModulePass) -> "IRPassManager":
        self.module_passes.append(pass_)
        return self

    def add_function_pass(self, pass_: FunctionPass) -> "IRPassManager":
        self.module_passes.append(_FunctionPassAdaptor(pass_))
        return self

    def disable(self, name: str) -> "IRPassManager":
        self.disabled_names.add(name)
        return self

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager | None = None,
    ) -> PreservedAnalyses:
        if am is None:
            am = AnalysisManager()
        pa = PreservedAnalyses.all()
        for pass_ in self.module_passes:
            if pass_.name in self.disabled_names:
                continue
            pass_pa = pass_.run(module, am)
            am.invalidate(module, pass_pa)
            # Aggregate: if any pass returns "none preserved", whole
            # pipeline preserves nothing downstream.
            if not pass_pa.preserves(_ALL_KEY):
                pa = pass_pa
        return pa


# ---------------------------------------------------------------------------
# Function-pass adaptor
# ---------------------------------------------------------------------------


_ALL_KEY = AnalysisKey("__all__")


class _FunctionPassAdaptor(ModulePass):
    """Run a function pass across every function in a module.

    Matches ``createModuleToFunctionPassAdaptor`` in
    /tmp/llvm-src/llvm-20.1.8.src/include/llvm/IR/PassManager.h. The
    adaptor collects per-function preserved-analyses and unions them
    into a module-level preserved set. Analyses keyed on an individual
    function are invalidated per function.
    """

    def __init__(self, inner: FunctionPass) -> None:
        self._inner = inner
        self.name = inner.name

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        any_change = False
        for function in module.functions:
            if function.is_declaration:
                continue
            pa = self._inner.run(function, am)
            am.invalidate(function, pa)
            if not pa.preserves(_ALL_KEY):
                any_change = True
        return PreservedAnalyses.none() if any_change else PreservedAnalyses.all()
