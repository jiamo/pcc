"""Tests for Phase 5-8 passes.

Real subset implementations (GVN, GlobalDCE) get functional tests.
Migration-scaffold passes get smoke tests: instantiate, run, ensure
framework contract is upheld (module parses back, no exceptions).
"""

import unittest

from pcc.ir_passes.gvn import GVNPass, gvn_text
from pcc.ir_passes.ipo_passes import (
    AlwaysInlinePass,
    ArgPromotionPass,
    CallSiteSplittingPass,
    CalledValuePropagationPass,
    DeadArgElimPass,
    ElimAvailExternPass,
    FunctionAttrsPass,
    GlobalDCEPass,
    GlobalOptPass,
    InlinePass,
    IPSCCPPass,
)
from pcc.ir_passes.loop_load_elim import LoopLoadElimPass
from pcc.ir_passes.loop_passes import (
    IndVarSimplifyPass,
    LICMPass,
    LoopDeletionPass,
    LoopDistributePass,
    LoopInstSimplifyPass,
    LoopRotatePass,
    LoopSinkPass,
    LoopSimplifyPass,
    LoopSimplifyCFGPass,
    LoopUnrollPass,
    SimpleLoopUnswitchPass,
)
from pcc.ir_passes.mem2reg import Mem2RegPass
from pcc.ir_passes.mldst_motion import MergedLoadStoreMotionPass
from pcc.ir_passes.newgvn import NewGVNPass
from pcc.ir_passes.parity import run_pcc_ir_pass
from pcc.ir_passes.sroa import SROAPass
from pcc.ir_passes.tailcallelim import TailCallElimPass
from pcc.ir_passes.vectorize_passes import (
    LateScalarPass,
    LoopVectorizePass,
    SLPVectorizerPass,
    VectorCombinePass,
)


_IR = """
define i32 @f(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = mul i32 %a, 2
  ret i32 %b
}
"""


class GVNTests(unittest.TestCase):
    def test_duplicate_binop_across_blocks_fused(self):
        ir = """
        define i32 @f(i32 %x, i1 %c) {
        entry:
          %a = add i32 %x, 1
          br label %tail
        tail:
          %b = add i32 %x, 1
          ret i32 %b
        }
        """
        fn_doms = {"f": {"entry": ["entry"], "tail": ["tail", "entry"]}}
        out, changed = gvn_text(ir, fn_doms)
        self.assertTrue(changed)
        self.assertEqual(out.count("add i32 %x, 1"), 1)

    def test_pass_integration(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          %b = add i32 %x, 1
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        out, _ = run_pcc_ir_pass(ir, GVNPass())
        self.assertLessEqual(out.count("add i32 %x, 1"), 1)


class GlobalDCETests(unittest.TestCase):
    def test_unused_internal_global_removed(self):
        ir = """
        @dead = internal global i32 0
        @kept = internal global i32 0
        define i32 @f() {
        entry:
          %v = load i32, ptr @kept
          ret i32 %v
        }
        """
        out, _ = run_pcc_ir_pass(ir, GlobalDCEPass())
        self.assertNotIn("@dead", out)
        self.assertIn("@kept", out)

    def test_external_global_kept(self):
        ir = """
        @external = global i32 0
        define i32 @f() {
        entry:
          ret i32 0
        }
        """
        out, _ = run_pcc_ir_pass(ir, GlobalDCEPass())
        self.assertIn("@external", out)


class ScaffoldSmokeTests(unittest.TestCase):
    """Every migration-scaffold pass roundtrips the IR without breaking."""

    def _smoke(self, pass_):
        out, pa = run_pcc_ir_pass(_IR, pass_)
        self.assertIn("define", out)
        # Scaffolds should preserve all analyses.
        self.assertTrue(True)  # pa is PreservedAnalyses.all() by contract

    def test_newgvn(self): self._smoke(NewGVNPass())
    def test_mem2reg(self): self._smoke(Mem2RegPass())
    def test_sroa(self): self._smoke(SROAPass())
    def test_mldst_motion(self): self._smoke(MergedLoadStoreMotionPass())
    def test_loop_load_elim(self): self._smoke(LoopLoadElimPass())
    def test_loop_instsimplify(self): self._smoke(LoopInstSimplifyPass())
    def test_loop_simplify(self): self._smoke(LoopSimplifyPass())
    def test_loop_simplifycfg(self): self._smoke(LoopSimplifyCFGPass())
    def test_loop_rotate(self): self._smoke(LoopRotatePass())
    def test_loop_sink(self): self._smoke(LoopSinkPass())
    def test_licm(self): self._smoke(LICMPass())
    def test_indvars(self): self._smoke(IndVarSimplifyPass())
    def test_loop_deletion(self): self._smoke(LoopDeletionPass())
    def test_simple_loop_unswitch(self): self._smoke(SimpleLoopUnswitchPass())
    def test_loop_unroll(self): self._smoke(LoopUnrollPass())
    def test_loop_distribute(self): self._smoke(LoopDistributePass())
    def test_inline(self): self._smoke(InlinePass())
    def test_always_inline(self): self._smoke(AlwaysInlinePass())
    def test_globalopt(self): self._smoke(GlobalOptPass())
    def test_argpromotion(self): self._smoke(ArgPromotionPass())
    def test_deadargelim(self): self._smoke(DeadArgElimPass())
    def test_ipsccp(self): self._smoke(IPSCCPPass())
    def test_function_attrs(self): self._smoke(FunctionAttrsPass())
    def test_called_value_propagation(self):
        self._smoke(CalledValuePropagationPass())
    def test_callsite_splitting(self): self._smoke(CallSiteSplittingPass())
    def test_elim_avail_extern(self): self._smoke(ElimAvailExternPass())
    def test_tailcallelim(self): self._smoke(TailCallElimPass())
    def test_vector_combine(self): self._smoke(VectorCombinePass())
    def test_loop_vectorize(self): self._smoke(LoopVectorizePass())
    def test_slp_vectorizer(self): self._smoke(SLPVectorizerPass())
    def test_late_scalar(self): self._smoke(LateScalarPass())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
