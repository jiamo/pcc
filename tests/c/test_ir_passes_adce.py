"""Parity corpus for ADCEPass.

ADCE differs from DCE mainly in that it uses reverse-flow liveness
starting from externally-observable sinks. Our subset currently
matches the "pure value-level dead code" cases — where ADCE and DCE
agree — so we gate the status on passing the same corpus against
``opt -passes=adce``.
"""

import shutil
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.adce import ADCEPass, adce_module_text
from pcc.ir_passes.parity import assert_ir_parity

_OPT = shutil.which("opt")


class ADCETests(unittest.TestCase):
    def test_unused_computation_removed(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          %b = mul i32 %a, 3
          ret i32 %x
        }
        """
        out, changed = adce_module_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("add i32", out)
        self.assertNotIn("mul i32", out)

    def test_used_computation_preserved(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          ret i32 %a
        }
        """
        _, changed = adce_module_text(ir)
        self.assertFalse(changed)

    def test_dead_pure_call_removed(self):
        ir = """
        declare i32 @pure(i32) memory(none) willreturn nounwind
        define i32 @f(i32 %x) {
        entry:
          %v = call i32 @pure(i32 %x)
          ret i32 0
        }
        """
        out, changed = adce_module_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("call i32 @pure", out)

    def test_dead_pure_void_call_removed(self):
        ir = """
        declare void @pure() memory(none) willreturn nounwind
        define i32 @f() {
        entry:
          call void @pure()
          ret i32 0
        }
        """
        out, changed = adce_module_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @pure", out)

    def test_dead_tail_pure_void_call_removed(self):
        ir = """
        declare void @pure() memory(none) willreturn nounwind
        define i32 @f() {
        entry:
          tail call void @pure()
          ret i32 0
        }
        """
        out, changed = adce_module_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("tail call void @pure", out)

    def test_dead_conditional_branch_to_same_merge_is_rewritten(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          br label %merge
        else:
          br label %merge
        merge:
          ret i32 %x
        }
        """
        out, changed = adce_module_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("br i1 %c", out)
        self.assertIn("br label %then", out)
        self.assertNotIn("%a = add i32 %x, 1", out)

    def test_dead_conditional_forwarder_chain_to_same_merge_is_rewritten(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          br label %mid.t
        mid.t:
          br label %merge
        else:
          br label %mid.e
        mid.e:
          br label %merge
        merge:
          ret i32 %x
        }
        """
        out, changed = adce_module_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("br i1 %c", out)
        self.assertIn("br label %then", out)
        self.assertNotIn("%a = add i32 %x, 1", out)

    def test_dead_conditional_direct_merge_and_forwarder_is_rewritten(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          br label %merge
        merge:
          ret i32 %x
        }
        """
        out, changed = adce_module_text(ir)
        self.assertTrue(changed)
        self.assertIn("br label %merge", out)
        self.assertNotIn("br i1 %c", out)
        self.assertNotIn("%a = add i32 %x, 1", out)

    def test_dead_conditional_rewrite_repairs_phi_incomings(self):
        ir = """
        define i32 @f(i1 %c) {
        entry:
          br label %pred
        pred:
          br i1 %c, label %other, label %merge
        other:
          br label %merge
        merge:
          %p = phi i32 [ 1, %pred ], [ 2, %other ]
          ret i32 %p
        }
        """
        out, changed = adce_module_text(ir)
        self.assertFalse(changed)
        self.assertIn("[ 1, %pred ]", out)
        llvm.parse_assembly(out).verify()


_CORPUS: list[tuple[str, str]] = [
    (
        "unused_add",
        """
define i32 @f(i32 %x) { entry:
  %dead = add i32 %x, 1
  ret i32 %x
}
""",
    ),
    (
        "dead_chain",
        """
define i32 @f(i32 %x) { entry:
  %a = add i32 %x, 1
  %b = mul i32 %a, 3
  %c = sub i32 %b, %a
  ret i32 %x
}
""",
    ),
    (
        "live_arith_preserved",
        """
define i32 @f(i32 %x) { entry:
  %a = add i32 %x, 1
  ret i32 %a
}
""",
    ),
    (
        "store_preserved",
        """
define void @f(ptr %p, i32 %x) { entry:
  store i32 %x, ptr %p
  ret void
}
""",
    ),
    (
        "side_effect_call_preserved",
        """
declare void @sink(i32)
define void @f(i32 %x) { entry:
  call void @sink(i32 %x)
  ret void
}
""",
    ),
    (
        "unused_select",
        """
define i32 @f(i1 %c, i32 %x, i32 %y) { entry:
  %dead = select i1 %c, i32 %x, i32 %y
  ret i32 %x
}
""",
    ),
    (
        "unused_icmp",
        """
define i32 @f(i32 %x) { entry:
  %t = icmp slt i32 %x, 10
  ret i32 %x
}
""",
    ),
    (
        "volatile_load_preserved",
        """
define i32 @f(ptr %p) { entry:
  %v = load volatile i32, ptr %p
  ret i32 %v
}
""",
    ),
    (
        "atomic_preserved",
        """
define i32 @f(ptr %p) { entry:
  %v = atomicrmw add ptr %p, i32 1 seq_cst
  ret i32 %v
}
""",
    ),
    (
        "dead_pure_call_removed",
        """
declare i32 @pure(i32) memory(none) willreturn nounwind
define i32 @f(i32 %x) { entry:
  %v = call i32 @pure(i32 %x)
  ret i32 0
}
""",
    ),
    (
        "dead_pure_void_call_removed",
        """
declare void @purev() memory(none) willreturn nounwind
define i32 @f() { entry:
  call void @purev()
  ret i32 0
}
""",
    ),
    (
        "dead_tail_pure_void_call_removed",
        """
declare void @purevt() memory(none) willreturn nounwind
define i32 @f() { entry:
  tail call void @purevt()
  ret i32 0
}
""",
    ),
    (
        "dead_gep_removed",
        """
define i32 @f(ptr %p, i32 %x) { entry:
  %g = getelementptr i32, ptr %p, i32 %x
  ret i32 %x
}
""",
    ),
    (
        "dead_bitcast_chain_removed",
        """
define i64 @f(ptr %p) { entry:
  %a = bitcast ptr %p to ptr
  %b = ptrtoint ptr %a to i64
  ret i64 0
}
""",
    ),
    (
        "dead_freeze_removed",
        """
define i32 @f(i32 %x) { entry:
  %f = freeze i32 %x
  ret i32 0
}
""",
    ),
    (
        "dead_zext_chain_removed",
        """
define i64 @f(i32 %x) { entry:
  %a = add i32 %x, 1
  %b = zext i32 %a to i64
  %c = shl i64 %b, 2
  ret i64 0
}
""",
    ),
    (
        "ret_arg_passthrough_kills_all",
        """
define i32 @f(i32 %x) { entry:
  %a = add i32 %x, 1
  %b = sub i32 %a, %x
  %c = mul i32 %b, 7
  %d = xor i32 %c, -1
  ret i32 %x
}
""",
    ),
    (
        "load_side_effect_preserved_but_add_dead",
        """
define i32 @f(ptr %p) { entry:
  %v = load i32, ptr %p
  %dead = add i32 %v, 17
  ret i32 %v
}
""",
    ),
    (
        "dead_conditional_same_merge",
        """
define i32 @f(i1 %c, i32 %x) {
entry:
  br i1 %c, label %then, label %else
then:
  %a = add i32 %x, 1
  br label %merge
else:
  br label %merge
merge:
  ret i32 %x
}
""",
    ),
    (
        "dead_conditional_forwarder_chain_same_merge",
        """
define i32 @f(i1 %c, i32 %x) {
entry:
  br i1 %c, label %then, label %else
then:
  %a = add i32 %x, 1
  br label %mid.t
mid.t:
  br label %merge
else:
  br label %mid.e
mid.e:
  br label %merge
merge:
  ret i32 %x
}
""",
    ),
    (
        "dead_conditional_direct_merge_and_forwarder",
        """
define i32 @f(i1 %c, i32 %x) {
entry:
  br i1 %c, label %then, label %merge
then:
  %a = add i32 %x, 1
  br label %merge
merge:
  ret i32 %x
}
""",
    ),
]


@unittest.skipUnless(_OPT, "requires opt")
class UpstreamParityTests(unittest.TestCase):
    def _assert_structural_parity(self, ir: str):
        report = assert_ir_parity(ir, ADCEPass(), "adce")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_unused_chain_matches_upstream(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          ret i32 %x
        }
        """
        report = assert_ir_parity(ir, ADCEPass(), "adce")
        self.assertNotIn("add i32", report.pcc_ir)

    def test_dead_pure_call_matches_upstream(self):
        self._assert_structural_parity("""
        declare i32 @pure(i32) memory(none) willreturn nounwind
        define i32 @f(i32 %x) {
        entry:
          %v = call i32 @pure(i32 %x)
          ret i32 0
        }
        """)

    def test_dead_pure_void_call_matches_upstream(self):
        self._assert_structural_parity("""
        declare void @pure() memory(none) willreturn nounwind
        define i32 @f() {
        entry:
          call void @pure()
          ret i32 0
        }
        """)

    def test_dead_tail_pure_void_call_matches_upstream(self):
        self._assert_structural_parity("""
        declare void @pure() memory(none) willreturn nounwind
        define i32 @f() {
        entry:
          tail call void @pure()
          ret i32 0
        }
        """)

    def test_dead_conditional_same_merge_matches_upstream(self):
        self._assert_structural_parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          br label %merge
        else:
          br label %merge
        merge:
          ret i32 %x
        }
        """)

    def test_dead_conditional_forwarder_chain_same_merge_matches_upstream(self):
        self._assert_structural_parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          br label %mid.t
        mid.t:
          br label %merge
        else:
          br label %mid.e
        mid.e:
          br label %merge
        merge:
          ret i32 %x
        }
        """)

    def test_dead_conditional_direct_merge_and_forwarder_matches_upstream(self):
        self._assert_structural_parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          br label %merge
        merge:
          ret i32 %x
        }
        """)


@unittest.skipUnless(_OPT, "requires opt")
class CorpusParityTests(unittest.TestCase):
    def _parity(self, ir: str, tag: str) -> None:
        report = assert_ir_parity(ir, ADCEPass(), "adce")
        self.assertEqual(
            report.diff.missing_functions,
            [],
            f"[{tag}] missing/extra fn mismatch:\n---pcc---\n{report.pcc_ir}\n"
            f"---opt---\n{report.opt_ir}\n---diff---\n{report.diff}",
        )
        self.assertEqual(
            report.diff.extra_functions,
            [],
            f"[{tag}] missing/extra fn mismatch:\n---pcc---\n{report.pcc_ir}\n"
            f"---opt---\n{report.opt_ir}\n---diff---\n{report.diff}",
        )
        self.assertEqual(
            report.diff.function_diffs,
            [],
            f"[{tag}] structural mismatch:\n---pcc---\n{report.pcc_ir}\n"
            f"---opt---\n{report.opt_ir}\n---diff---\n{report.diff}",
        )
        self.assertIsNone(
            report.diff.global_count_diff,
            f"[{tag}] global mismatch:\n---pcc---\n{report.pcc_ir}\n"
            f"---opt---\n{report.opt_ir}\n---diff---\n{report.diff}",
        )

    def test_each_case_matches_upstream(self):
        for tag, body in _CORPUS:
            with self.subTest(case=tag):
                self._parity(body, tag)

    def test_combined_corpus_matches_upstream(self):
        parts: list[str] = []
        for tag, body in _CORPUS:
            part = body.replace("@f(", f"@{tag}(")
            part = part.replace("@sink(", f"@sink_{tag}(")
            parts.append(part)
        self._parity("\n".join(parts), "combined")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
