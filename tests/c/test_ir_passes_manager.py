"""Tests for the IR pass manager runtime (pcc.ir_passes.manager).

Upstream reference anchors (for the behavior we are matching):

- ``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/IR/PassManager.h``
- ``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/IR/PassManagerInternal.h``
"""

import unittest

import llvmlite.binding as llvm

from pcc.ir_passes import (
    AnalysisKey,
    AnalysisManager,
    FunctionPass,
    IRPassManager,
    ModulePass,
    PreservedAnalyses,
)
from pcc.ir_passes.manager import AnalysisResult


_TRIVIAL_IR = """
define i32 @f(i32 %x) {
  %1 = add i32 %x, 0
  ret i32 %1
}
"""


def _parse(ir_text: str = _TRIVIAL_IR) -> llvm.ModuleRef:
    module = llvm.parse_assembly(ir_text)
    module.verify()
    return module


class PreservedAnalysesTests(unittest.TestCase):
    def test_all_preserves_every_key(self):
        pa = PreservedAnalyses.all()
        self.assertTrue(pa.preserves(AnalysisKey("dom-tree")))
        self.assertTrue(pa.preserves(AnalysisKey("loop-info")))

    def test_none_preserves_nothing(self):
        pa = PreservedAnalyses.none()
        self.assertFalse(pa.preserves(AnalysisKey("dom-tree")))

    def test_explicit_set_preserves_only_listed_keys(self):
        k = AnalysisKey("dom-tree")
        pa = PreservedAnalyses({k})
        self.assertTrue(pa.preserves(k))
        self.assertFalse(pa.preserves(AnalysisKey("loop-info")))

    def test_preserve_adds_to_finite_set(self):
        pa = PreservedAnalyses.none()
        k = AnalysisKey("loop-info")
        pa.preserve(k)
        self.assertTrue(pa.preserves(k))

    def test_preserve_is_noop_when_all_already_preserved(self):
        pa = PreservedAnalyses.all()
        pa.preserve(AnalysisKey("whatever"))
        self.assertTrue(pa.preserves(AnalysisKey("anything")))


class _ConstResult(AnalysisResult):
    KEY = AnalysisKey("const-analysis")

    def __init__(self, value: int) -> None:
        self.value = value


class _LoopyResult(AnalysisResult):
    KEY = AnalysisKey("loopy-analysis")

    def __init__(self, value: int) -> None:
        self.value = value

    def invalidate(self, ir_unit, preserved):
        return not preserved.preserves(type(self).KEY)


class AnalysisManagerTests(unittest.TestCase):
    def test_register_and_cache_returns_same_result(self):
        am = AnalysisManager()
        calls = []

        def compute(_unit):
            calls.append(1)
            return _ConstResult(42)

        am.register(_ConstResult.KEY, compute)
        module = _parse()
        first = am.get(_ConstResult.KEY, module)
        second = am.get(_ConstResult.KEY, module)
        self.assertIs(first, second)
        self.assertEqual(len(calls), 1, "analysis must be cached")

    def test_unknown_analysis_key_raises(self):
        am = AnalysisManager()
        module = _parse()
        with self.assertRaises(KeyError):
            am.get(AnalysisKey("missing"), module)

    def test_invalidate_drops_when_not_preserved(self):
        am = AnalysisManager()
        am.register(_LoopyResult.KEY, lambda _u: _LoopyResult(1))
        module = _parse()
        am.get(_LoopyResult.KEY, module)
        am.invalidate(module, PreservedAnalyses.none())
        # getting again should re-run compute — use a probe
        probes = {"count": 0}

        def recompute(_unit):
            probes["count"] += 1
            return _LoopyResult(2)

        am.register(_LoopyResult.KEY, recompute)
        am.get(_LoopyResult.KEY, module)
        self.assertEqual(probes["count"], 1)

    def test_invalidate_keeps_preserved(self):
        am = AnalysisManager()
        am.register(_LoopyResult.KEY, lambda _u: _LoopyResult(1))
        module = _parse()
        first = am.get(_LoopyResult.KEY, module)
        am.invalidate(module, PreservedAnalyses({_LoopyResult.KEY}))
        second = am.get(_LoopyResult.KEY, module)
        self.assertIs(first, second)

    def test_clear_drops_everything(self):
        am = AnalysisManager()
        calls = {"count": 0}

        def compute(_unit):
            calls["count"] += 1
            return _ConstResult(calls["count"])

        am.register(_ConstResult.KEY, compute)
        module = _parse()
        am.get(_ConstResult.KEY, module)
        am.clear()
        am.get(_ConstResult.KEY, module)
        self.assertEqual(calls["count"], 2)


class _RecordingPass(ModulePass):
    name = "recording"

    def __init__(self, preserved: PreservedAnalyses) -> None:
        self.preserved = preserved
        self.invocations = 0

    def run(self, module, am):
        self.invocations += 1
        return self.preserved


class IRPassManagerTests(unittest.TestCase):
    def test_runs_each_pass_in_insertion_order(self):
        order: list[str] = []

        class P(ModulePass):
            def __init__(self, name: str) -> None:
                self.name = name

            def run(self, module, am):
                order.append(self.name)
                return PreservedAnalyses.all()

        pm = IRPassManager().add(P("a")).add(P("b")).add(P("c"))
        pm.run(_parse())
        self.assertEqual(order, ["a", "b", "c"])

    def test_disabled_pass_is_skipped(self):
        p = _RecordingPass(PreservedAnalyses.all())
        pm = IRPassManager().add(p).disable("recording")
        pm.run(_parse())
        self.assertEqual(p.invocations, 0)

    def test_all_preserved_keeps_analysis_cache(self):
        am = AnalysisManager()
        am.register(_ConstResult.KEY, lambda _u: _ConstResult(99))
        module = _parse()
        first = am.get(_ConstResult.KEY, module)

        pm = IRPassManager().add(_RecordingPass(PreservedAnalyses.all()))
        pm.run(module, am)

        # AllPreserved → cache intact → same object back.
        second = am.get(_ConstResult.KEY, module)
        self.assertIs(first, second)

    def test_none_preserved_invalidates_cache(self):
        counter = {"n": 0}

        def compute(_u):
            counter["n"] += 1
            return _LoopyResult(counter["n"])

        am = AnalysisManager()
        am.register(_LoopyResult.KEY, compute)
        module = _parse()
        am.get(_LoopyResult.KEY, module)

        pm = IRPassManager().add(_RecordingPass(PreservedAnalyses.none()))
        pm.run(module, am)

        am.get(_LoopyResult.KEY, module)
        self.assertEqual(counter["n"], 2)


class _FnPass(FunctionPass):
    name = "fn-visitor"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def run(self, function, am):
        self.seen.append(function.name)
        return PreservedAnalyses.all()


class FunctionPassAdaptorTests(unittest.TestCase):
    def test_runs_inner_pass_on_every_definition(self):
        ir = """
        define i32 @a(i32 %x) { ret i32 %x }
        define i32 @b(i32 %x) { ret i32 %x }
        declare i32 @extern_fn(i32)
        """
        module = _parse(ir)
        inner = _FnPass()
        pm = IRPassManager().add_function_pass(inner)
        pm.run(module)
        self.assertEqual(sorted(inner.seen), ["a", "b"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
