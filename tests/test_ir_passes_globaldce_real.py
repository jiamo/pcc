"""Parity corpus for GlobalDCEPass.

Upstream reference:
- /tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/IPO/GlobalDCE.cpp
"""

import shutil
import unittest

from pcc.ir_passes.ipo_passes import GlobalDCEPass
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


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

    def test_private_unreferenced_removed(self):
        ir = """
        @.dead = private unnamed_addr constant [2 x i8] c"ab"
        define void @f() { entry: ret void }
        """
        out, _ = run_pcc_ir_pass(ir, GlobalDCEPass())
        self.assertNotIn("@.dead", out)

    def test_internal_referenced_via_store_kept(self):
        ir = """
        @counter = internal global i32 0
        define void @f() {
        entry:
          store i32 1, ptr @counter
          ret void
        }
        """
        out, _ = run_pcc_ir_pass(ir, GlobalDCEPass())
        self.assertIn("@counter", out)


@unittest.skipUnless(_OPT, "requires opt")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str) -> None:
        report = assert_ir_parity(ir, GlobalDCEPass(), "globaldce")
        # GlobalDCE only touches unused internal/private globals; we
        # compare on the kept/removed shape rather than strict text.
        # Check: same set of non-dead globals survive on both sides.
        for name in ("@kept", "@used", "@external", "@counter"):
            if name in ir:
                self.assertEqual(
                    name in report.pcc_ir,
                    name in report.opt_ir,
                    f"disagreement on {name}:\n"
                    f"pcc:{report.pcc_ir}\nopt:{report.opt_ir}",
                )

    def test_unused_internal_matches(self):
        self._parity("""
@dead = internal global i32 0
@kept = internal global i32 0
define i32 @f() { entry:
  %v = load i32, ptr @kept
  ret i32 %v
}
""")

    def test_private_const_matches(self):
        self._parity("""
@.dead = private unnamed_addr constant [2 x i8] c"ab"
@.used = private unnamed_addr constant [2 x i8] c"cd"
define ptr @f() { entry:
  ret ptr @.used
}
""")

    def test_external_preserved_matches(self):
        self._parity("""
@external = global i32 0
@counter = internal global i32 0
define void @f() { entry:
  store i32 1, ptr @counter
  ret void
}
""")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
