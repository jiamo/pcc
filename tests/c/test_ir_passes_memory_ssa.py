"""Tests for MemorySSA (staged subset)."""

import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.memory_ssa import build_memory_ssa


def _fn(ir_text: str, name: str) -> llvm.ValueRef:
    m = llvm.parse_assembly(ir_text)
    m.verify()
    for fn in m.functions:
        if fn.name == name:
            return fn
    raise LookupError(name)


_STRAIGHT = """
define i32 @f() {
entry:
  %p = alloca i32
  store i32 1, ptr %p
  store i32 2, ptr %p
  %v = load i32, ptr %p
  ret i32 %v
}
"""


_BRANCH = """
define i32 @g(i32 %c) {
entry:
  %p = alloca i32
  %cond = icmp ne i32 %c, 0
  br i1 %cond, label %then, label %else
then:
  store i32 1, ptr %p
  br label %join
else:
  store i32 2, ptr %p
  br label %join
join:
  %v = load i32, ptr %p
  ret i32 %v
}
"""


class MemorySSATests(unittest.TestCase):
    def test_live_on_entry_present(self):
        fn = _fn(_STRAIGHT, "f")
        form = build_memory_ssa(fn)
        self.assertEqual(form.accesses[0].kind, "liveOnEntry")

    def test_two_stores_and_one_load_detected(self):
        fn = _fn(_STRAIGHT, "f")
        form = build_memory_ssa(fn)
        defs = [a for a in form.accesses if a.kind == "def"]
        uses = [a for a in form.accesses if a.kind == "use"]
        self.assertEqual(len(defs), 2)
        self.assertEqual(len(uses), 1)

    def test_load_clobber_is_preceding_store(self):
        fn = _fn(_STRAIGHT, "f")
        form = build_memory_ssa(fn)
        uses = [a for a in form.accesses if a.kind == "use"]
        load = uses[0]
        clobber = form.by_id[load.clobber_id]
        self.assertEqual(clobber.kind, "def")
        # It should be the *second* store, not the first.
        defs = [a for a in form.accesses if a.kind == "def"]
        self.assertEqual(load.clobber_id, defs[1].id)

    def test_branch_introduces_phi(self):
        fn = _fn(_BRANCH, "g")
        form = build_memory_ssa(fn)
        phis = [a for a in form.accesses if a.kind == "phi"]
        self.assertEqual(len(phis), 1)
        phi = phis[0]
        self.assertEqual(phi.block, "join")
        # phi has incoming from both `then` and `else`.
        self.assertEqual(set(phi.phi_incoming.keys()), {"then", "else"})

    def test_call_counts_as_def(self):
        ir = """
        declare void @sink(ptr)
        define i32 @f() {
        entry:
          %p = alloca i32
          store i32 1, ptr %p
          call void @sink(ptr %p)
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        fn = _fn(ir, "f")
        form = build_memory_ssa(fn)
        defs = [a for a in form.accesses if a.kind == "def"]
        # store + call both count as defs.
        self.assertEqual(len(defs), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
