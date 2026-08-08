"""Parity tests for DCEPass.

Corpus is deliberately large so `equivalent` status can be claimed:
every case round-trips through upstream ``opt -passes=dce`` and pcc
produces the same post-pass IR shape.
"""

import pytest
import unittest

from pcc.ir_passes.dce import DCEPass, dce_module_text
from pcc.ir_passes.parity import assert_ir_parity


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class DCETests(unittest.TestCase):
    def test_unused_add_is_removed(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %unused = add i32 %x, 1
          ret i32 %x
        }
        """
        out, changed = dce_module_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("add i32", out)

    def test_chain_of_dead_is_removed(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          %b = add i32 %a, 2
          %c = add i32 %b, 3
          ret i32 %x
        }
        """
        out, changed = dce_module_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("add i32", out)

    def test_side_effecting_call_preserved(self):
        ir = """
        declare void @sink(i32)
        define void @f(i32 %x) {
        entry:
          call void @sink(i32 %x)
          ret void
        }
        """
        out, changed = dce_module_text(ir)
        self.assertFalse(changed)
        self.assertIn("call void @sink", out)

    def test_dead_pure_call_removed(self):
        ir = """
        declare i32 @pure(i32) memory(none) willreturn nounwind
        define i32 @f(i32 %x) {
        entry:
          %dead = call i32 @pure(i32 %x)
          ret i32 %x
        }
        """
        out, changed = dce_module_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("call i32 @pure", out)

    def test_dead_readnone_call_without_willreturn_preserved(self):
        ir = """
        declare i32 @pure(i32) readnone
        define i32 @f(i32 %x) {
        entry:
          %dead = call i32 @pure(i32 %x)
          ret i32 %x
        }
        """
        out, changed = dce_module_text(ir)
        self.assertFalse(changed)
        self.assertIn("call i32 @pure", out)

    def test_store_not_removed(self):
        ir = """
        define void @f(i32 %x) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          ret void
        }
        """
        out, changed = dce_module_text(ir)
        self.assertIn("store i32", out)


# ---------------------------------------------------------------------------
# Full parity corpus against ``opt -passes=dce``
# ---------------------------------------------------------------------------


_CORPUS: list[tuple[str, str]] = [
    (
        "unused_scalar_binop",
        """
define i32 @f(i32 %x) {
entry:
  %dead = add i32 %x, 1
  ret i32 %x
}
""",
    ),
    (
        "dead_chain",
        """
define i32 @f(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = mul i32 %a, 2
  %c = sub i32 %b, %a
  ret i32 %x
}
""",
    ),
    (
        "unused_icmp",
        """
define i32 @f(i32 %x) {
entry:
  %t = icmp slt i32 %x, 10
  ret i32 %x
}
""",
    ),
    (
        "unused_select",
        """
define i32 @f(i1 %c, i32 %x, i32 %y) {
entry:
  %dead = select i1 %c, i32 %x, i32 %y
  ret i32 %x
}
""",
    ),
    (
        "live_arithmetic_preserved",
        """
define i32 @f(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = mul i32 %a, 2
  ret i32 %b
}
""",
    ),
    (
        "store_preserved",
        """
define void @f(ptr %p, i32 %x) {
entry:
  store i32 %x, ptr %p
  ret void
}
""",
    ),
    (
        "volatile_load_preserved",
        """
define i32 @f(ptr %p) {
entry:
  %v = load volatile i32, ptr %p
  ret i32 %v
}
""",
    ),
    (
        "volatile_store_preserved",
        """
define void @f(ptr %p) {
entry:
  store volatile i32 0, ptr %p
  ret void
}
""",
    ),
    (
        "pure_call_dead_removed",
        """
declare i32 @pure(i32) memory(none) willreturn nounwind
define i32 @f(i32 %x) {
entry:
  %dead = call i32 @pure(i32 %x)
  ret i32 %x
}
""",
    ),
    (
        "pure_call_used_preserved",
        """
declare i32 @pure(i32) memory(none) willreturn nounwind
define i32 @f(i32 %x) {
entry:
  %used = call i32 @pure(i32 %x)
  ret i32 %used
}
""",
    ),
    (
        "readnone_only_preserved",
        """
declare i32 @rn(i32) readnone
define i32 @f(i32 %x) {
entry:
  %dead = call i32 @rn(i32 %x)
  ret i32 %x
}
""",
    ),
    (
        "nounwind_only_preserved",
        """
declare i32 @nu(i32) nounwind
define i32 @f(i32 %x) {
entry:
  %dead = call i32 @nu(i32 %x)
  ret i32 %x
}
""",
    ),
    (
        "atomic_preserved",
        """
define i32 @f(ptr %p) {
entry:
  %old = atomicrmw add ptr %p, i32 1 seq_cst
  ret i32 %old
}
""",
    ),
    (
        "cmpxchg_preserved",
        """
define void @f(ptr %p) {
entry:
  %pair = cmpxchg ptr %p, i32 0, i32 1 seq_cst seq_cst
  ret void
}
""",
    ),
    (
        "fence_preserved",
        """
define void @f() {
entry:
  fence seq_cst
  ret void
}
""",
    ),
    (
        "gep_unused_removed",
        """
define ptr @f(ptr %base, i32 %i) {
entry:
  %dead = getelementptr i32, ptr %base, i32 %i
  ret ptr %base
}
""",
    ),
    (
        "gep_used_preserved",
        """
define ptr @f(ptr %base, i32 %i) {
entry:
  %p = getelementptr i32, ptr %base, i32 %i
  ret ptr %p
}
""",
    ),
    (
        "bitcast_dead_removed",
        """
define ptr @f(ptr %p) {
entry:
  %dead = bitcast ptr %p to ptr
  ret ptr %p
}
""",
    ),
    (
        "multi_block_dead",
        """
define i32 @f(i32 %x, i1 %c) {
entry:
  br i1 %c, label %t, label %f
t:
  %a = add i32 %x, 1
  br label %join
f:
  %b = sub i32 %x, 1
  br label %join
join:
  %dead = add i32 %x, 2
  ret i32 %x
}
""",
    ),
    (
        "phi_unused_removed",
        """
define i32 @f(i32 %x, i1 %c) {
entry:
  br i1 %c, label %t, label %f
t:
  br label %join
f:
  br label %join
join:
  %dead = phi i32 [ %x, %t ], [ 0, %f ]
  ret i32 %x
}
""",
    ),
]


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str, tag: str = ""):
        report = assert_ir_parity(ir, DCEPass(), "dce")
        self.assertTrue(
            report.is_equivalent,
            f"[{tag}] mismatch:\n---pcc---\n{report.pcc_ir}\n"
            f"---opt---\n{report.opt_ir}\n---diff---\n{report.diff}",
        )

    def test_unused_add_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %x) { entry:
  %unused = add i32 %x, 1
  ret i32 %x
}
""")

    def test_dead_pure_call_matches_upstream(self):
        self._parity("""
declare i32 @pure(i32) memory(none) willreturn nounwind
define i32 @f(i32 %x) { entry:
  %dead = call i32 @pure(i32 %x)
  ret i32 %x
}
""")

    def test_readnone_without_willreturn_matches_upstream(self):
        self._parity("""
declare i32 @pure(i32) readnone
define i32 @f(i32 %x) { entry:
  %dead = call i32 @pure(i32 %x)
  ret i32 %x
}
""")


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class CorpusParityTests(unittest.TestCase):
    """Every entry in :data:`_CORPUS` matches ``opt -passes=dce``.

    The corpus is consulted both case-by-case and as a single module.
    When all match, this pass qualifies for ``equivalent`` status.
    """

    def _parity(self, ir: str, tag: str) -> None:
        report = assert_ir_parity(ir, DCEPass(), "dce")
        self.assertTrue(
            report.is_equivalent,
            f"[{tag}] mismatch:\n---pcc---\n{report.pcc_ir}\n"
            f"---opt---\n{report.opt_ir}\n---diff---\n{report.diff}",
        )

    def test_each_case_matches_upstream(self):
        for tag, body in _CORPUS:
            with self.subTest(case=tag):
                self._parity(body, tag)

    def test_combined_corpus_matches_upstream(self):
        """Concatenate all cases (with renamed fns) into one module."""
        combined_parts: list[str] = []
        for tag, body in _CORPUS:
            part = body.replace("@f", f"@{tag}")
            part = part.replace("@pure", f"@pure_{tag}")
            part = part.replace("@rn", f"@rn_{tag}")
            part = part.replace("@nu", f"@nu_{tag}")
            combined_parts.append(part)
        self._parity("\n".join(combined_parts), "combined")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
