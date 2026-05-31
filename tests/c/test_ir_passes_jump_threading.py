"""Smoke tests for JumpThreadingPass (subset placeholder)."""

import unittest

from pcc.ir_passes.jump_threading import JumpThreadingPass
from pcc.ir_passes.parity import run_pcc_ir_pass


class JumpThreadingTests(unittest.TestCase):
    def test_runs_without_error(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          ret i32 %x
        }
        """
        out, _ = run_pcc_ir_pass(ir, JumpThreadingPass())
        # Placeholder: currently a no-op, but the pass must round-trip.
        self.assertIn("define", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
