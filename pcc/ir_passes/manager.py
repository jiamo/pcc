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
import time
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
    scope: str = "module"

    def __post_init__(self) -> None:
        if self.scope not in ("module", "function", "loop", "scc"):
            raise ValueError("unknown analysis scope: " + self.scope)


@dataclass
class AnalysisCounters:
    queries: int = 0
    hits: int = 0
    misses: int = 0
    recomputes: int = 0
    invalidations: int = 0
    compute_ns: int = 0


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

    Keyed by ``(AnalysisKey, ir_unit)``. LLVM ``ValueRef`` wrappers compare
    and hash by their underlying LLVM pointer, so repeated iterator wrappers
    for one function share a cache entry without permitting Python ``id``
    reuse. Passes register analysis
    *computation functions* (``compute(ir_unit) -> AnalysisResult``);
    the manager lazily runs them on first request and caches the
    result until invalidation.
    """

    def __init__(self) -> None:
        self._computations: dict[AnalysisKey, Callable[[Any], AnalysisResult]] = {}
        self._cache: dict[tuple[AnalysisKey, Any], AnalysisResult] = {}
        self._counters: dict[AnalysisKey, AnalysisCounters] = {}

    def _counter(self, key: AnalysisKey) -> AnalysisCounters:
        counter = self._counters.get(key)
        if counter is None:
            counter = AnalysisCounters()
            self._counters[key] = counter
        return counter

    def register(
        self,
        key: AnalysisKey,
        compute: Callable[[Any], AnalysisResult],
    ) -> None:
        self._computations[key] = compute

    def get(self, key: AnalysisKey, ir_unit: Any) -> AnalysisResult:
        try:
            hash(ir_unit)
        except TypeError as exc:
            raise TypeError("analysis IR units must be hashable") from exc
        cache_key = (key, ir_unit)
        counter = self._counter(key)
        counter.queries += 1
        if cache_key in self._cache:
            counter.hits += 1
            return self._cache[cache_key]
        counter.misses += 1
        compute = self._computations.get(key)
        if compute is None:
            raise KeyError(f"analysis {key.name!r} not registered")
        started = time.perf_counter_ns()
        result = compute(ir_unit)
        counter.compute_ns += time.perf_counter_ns() - started
        counter.recomputes += 1
        self._cache[cache_key] = result
        return result

    def require(
        self,
        keys: Iterable[AnalysisKey],
        ir_unit: Any,
    ) -> None:
        for key in keys:
            self.get(key, ir_unit)

    def require_for_module_pass(
        self,
        keys: Iterable[AnalysisKey],
        module: llvm.ModuleRef,
    ) -> None:
        for key in keys:
            if key.scope == "module":
                self.get(key, module)
                continue
            if key.scope == "function":
                for function in module.functions:
                    if not function.is_declaration:
                        self.get(key, function)
                continue
            raise ValueError(
                "module pass cannot materialize analysis scope " + key.scope
            )

    def invalidate(self, ir_unit: Any, preserved: PreservedAnalyses) -> None:
        """Drop cached results that aren't preserved.

        Matches the behavior at PassManager.h:291 (``invalidate``):
        walk all cached results for this IR unit and ask each one
        whether ``preserved`` keeps it valid.
        """
        to_drop: list[tuple[AnalysisKey, Any]] = []
        for cache_key, result in self._cache.items():
            if cache_key[1] != ir_unit:
                continue
            if result.invalidate(ir_unit, preserved):
                to_drop.append(cache_key)
        for cache_key in to_drop:
            self._counter(cache_key[0]).invalidations += 1
            self._cache.pop(cache_key, None)

    def invalidate_module_tree(
        self,
        module: llvm.ModuleRef,
        preserved: PreservedAnalyses,
    ) -> None:
        self.invalidate(module, preserved)
        for function in module.functions:
            if not function.is_declaration:
                self.invalidate(function, preserved)

    def erase(self, ir_unit: Any) -> None:
        to_drop = [key for key in self._cache if key[1] == ir_unit]
        for cache_key in to_drop:
            self._counter(cache_key[0]).invalidations += 1
            self._cache.pop(cache_key, None)

    def erase_module_tree(self, module: llvm.ModuleRef) -> None:
        for function in module.functions:
            if not function.is_declaration:
                self.erase(function)
        self.erase(module)

    def telemetry(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        ordered = sorted(
            self._counters.items(),
            key=lambda item: (item[0].scope, item[0].name),
        )
        for key, counter in ordered:
            out[key.scope + ":" + key.name] = {
                "queries": counter.queries,
                "hits": counter.hits,
                "misses": counter.misses,
                "recomputes": counter.recomputes,
                "invalidations": counter.invalidations,
                "compute_ns": counter.compute_ns,
            }
        return out

    def clear(self) -> None:
        for key, _unit_id in self._cache:
            self._counter(key).invalidations += 1
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
    scope: str = "module"
    required_analyses: tuple[AnalysisKey, ...] = ()
    mutation_class: str = "unknown"

    def register_analyses(self, am: AnalysisManager) -> None:
        return None

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
    scope: str = "function"
    required_analyses: tuple[AnalysisKey, ...] = ()
    mutation_class: str = "unknown"

    def register_analyses(self, am: AnalysisManager) -> None:
        return None

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
    scope: str = "loop"
    required_analyses: tuple[AnalysisKey, ...] = ()
    mutation_class: str = "unknown"

    def register_analyses(self, am: AnalysisManager) -> None:
        return None

    @abstractmethod
    def run(
        self,
        loop: Any,
        am: AnalysisManager,
    ) -> PreservedAnalyses: ...


# ---------------------------------------------------------------------------
# Pass manager
# ---------------------------------------------------------------------------


_MUTATION_CLASSES = {
    "none",
    "instructions",
    "cfg",
    "module",
    "unknown",
}


def _invalidation_contract(
    pass_: ModulePass | FunctionPass | LoopPass,
    reported: PreservedAnalyses,
) -> PreservedAnalyses:
    if not isinstance(reported, PreservedAnalyses):
        raise TypeError(pass_.name + " did not return PreservedAnalyses")
    if pass_.mutation_class not in _MUTATION_CLASSES:
        raise ValueError(
            pass_.name + " has unknown mutation class " + pass_.mutation_class
        )
    if pass_.mutation_class == "unknown":
        return PreservedAnalyses.none()
    return reported


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
            if pass_.scope != "module":
                raise ValueError(
                    pass_.name + " registered in module pipeline with scope "
                    + pass_.scope
                )
            pass_.register_analyses(am)
            am.require_for_module_pass(pass_.required_analyses, module)
            pass_pa = pass_.run(module, am)
            am.invalidate_module_tree(
                module,
                _invalidation_contract(pass_, pass_pa),
            )
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
        self.mutation_class = inner.mutation_class

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        any_change = False
        self._inner.register_analyses(am)
        for function in module.functions:
            if function.is_declaration:
                continue
            for key in self._inner.required_analyses:
                if key.scope != "function":
                    raise ValueError(
                        self.name + " function pass requires " + key.scope
                        + " analysis " + key.name
                    )
            am.require(self._inner.required_analyses, function)
            pa = self._inner.run(function, am)
            am.invalidate(
                function,
                _invalidation_contract(self._inner, pa),
            )
            if not pa.preserves(_ALL_KEY):
                any_change = True
        return PreservedAnalyses.none() if any_change else PreservedAnalyses.all()
