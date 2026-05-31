"""Real-transform tests for JumpThreadingPass.

Upstream reference:
- /tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/JumpThreading.cpp
"""

import shutil
import unittest

from pcc.ir_passes.jump_threading import JumpThreadingPass, jump_thread_text
from pcc.ir_passes.parity import run_pcc_ir_pass


_OPT = shutil.which("opt")


_PHI_THREADABLE = """
define i32 @f(i1 %p) {
entry:
  br i1 %p, label %if.true, label %if.false
if.true:
  br label %merge
if.false:
  br label %merge
merge:
  %c = phi i1 [ true, %if.true ], [ false, %if.false ]
  br i1 %c, label %ret.one, label %ret.zero
ret.one:
  ret i32 1
ret.zero:
  ret i32 0
}
"""


class JumpThreadingTests(unittest.TestCase):
    def test_threading_fires_on_constant_phi(self):
        out, changed = jump_thread_text(_PHI_THREADABLE)
        self.assertTrue(changed)
        # After threading, if.true should branch directly to ret.one
        # and if.false to ret.zero.
        self.assertIn("br label %ret.one", out)
        self.assertIn("br label %ret.zero", out)

    def test_no_threading_on_variable_phi(self):
        ir = """
        define i32 @f(i1 %p, i1 %q) {
        entry:
          br i1 %p, label %if.true, label %if.false
        if.true:
          br label %merge
        if.false:
          br label %merge
        merge:
          %c = phi i1 [ %q, %if.true ], [ false, %if.false ]
          br i1 %c, label %ret.one, label %ret.zero
        ret.one:
          ret i32 1
        ret.zero:
          ret i32 0
        }
        """
        _, changed = jump_thread_text(ir)
        self.assertFalse(changed)

    def test_pass_integration(self):
        out, _ = run_pcc_ir_pass(_PHI_THREADABLE, JumpThreadingPass())
        # After threading + verification, the IR should still parse.
        self.assertIn("define i32 @f", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
