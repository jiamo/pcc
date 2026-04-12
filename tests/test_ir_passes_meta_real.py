"""Tests for IR meta / barrier passes."""

import unittest

from pcc.ir_passes.meta_passes import (
    AggressiveInstCombineIRPass,
    Annotation2MetadataIRPass,
    AnnotationRemarksIRPass,
    CGProfileIRPass,
    ChrIRPass,
    CoroBoundaryIRPass,
    EEInstrumentIRPass,
    ForceAttrsIRPass,
    InjectTLIMappingsIRPass,
    InvalidateIRPass,
    LoopIdiomIRPass,
    MoveAutoInitIRPass,
    OpenMPCGSCCIRPass,
    OpenMPOptIRPass,
    RecomputeGlobalsAAIRPass,
    RelLookupTableConverterIRPass,
    RequireIRPass,
    TransformWarningIRPass,
    VerifyIRPass,
)
from pcc.ir_passes.alignment_from_assumptions import (
    AlignmentFromAssumptionsIRPass,
)
from pcc.ir_passes.float2int import Float2IntIRPass
from pcc.ir_passes.memcpyopt import MemCpyOptIRPass
from pcc.ir_passes.speculative_execution import SpeculativeExecutionIRPass
from pcc.ir_passes.parity import run_pcc_ir_pass


_IR = """
define i32 @f(i32 %x) {
entry:
  %a = add i32 %x, 1
  ret i32 %a
}
"""


class MetaPassTests(unittest.TestCase):
    def test_verify_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, VerifyIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_invalidate_preserves_none_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, InvalidateIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.none()")

    def test_require_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, RequireIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_annotation_remarks_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, AnnotationRemarksIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_ee_instrument_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, EEInstrumentIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_openmp_opt_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, OpenMPOptIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_openmp_opt_cgscc_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, OpenMPCGSCCIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_cg_profile_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, CGProfileIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_rel_lookup_table_converter_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, RelLookupTableConverterIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_coro_boundary_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, CoroBoundaryIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_transform_warning_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, TransformWarningIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_annotation2metadata_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, Annotation2MetadataIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_forceattrs_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, ForceAttrsIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_inject_tli_mappings_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, InjectTLIMappingsIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_recompute_globalsaa_preserves_none_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, RecomputeGlobalsAAIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.none()")

    def test_aggressive_instcombine_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, AggressiveInstCombineIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_alignment_from_assumptions_is_noop_on_plain_ir(self):
        # Real narrow transform now lives in its own module. On IR
        # without any ``llvm.assume`` calls it is a no-op and
        # preserves all analyses.
        out, pa = run_pcc_ir_pass(_IR, AlignmentFromAssumptionsIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_chr_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, ChrIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_float2int_is_noop_on_integer_only_ir(self):
        # Float2IntIRPass now folds ``fptosi(sitofp(%x))`` round-trips.
        # On the integer-only _IR fixture there is nothing to fold, so
        # the pass preserves all analyses.
        out, pa = run_pcc_ir_pass(_IR, Float2IntIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_loop_idiom_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, LoopIdiomIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_memcpyopt_is_noop_on_call_free_ir(self):
        # MemCpyOptIRPass now deletes no-op memcpy/memmove/memset
        # calls. On call-free IR it is a no-op and preserves all
        # analyses.
        out, pa = run_pcc_ir_pass(_IR, MemCpyOptIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_move_auto_init_preserves_all_and_keeps_ir(self):
        out, pa = run_pcc_ir_pass(_IR, MoveAutoInitIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")

    def test_speculative_execution_is_noop_on_straight_line_ir(self):
        # SpeculativeExecutionIRPass now performs a real hoist when a
        # qualifying diamond is present. On the straight-line _IR
        # fixture there is no conditional branch, so the pass is a
        # no-op and all analyses are preserved.
        out, pa = run_pcc_ir_pass(_IR, SpeculativeExecutionIRPass())
        self.assertIn("define i32 @f", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
