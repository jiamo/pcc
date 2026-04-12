"""Tests for GVNPass (subset)."""

import shutil
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.dominator_tree import compute_dominator_tree
from pcc.ir_passes.gvn import GVNPass, gvn_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


def _doms(ir: str) -> dict[str, dict[str, list[str]]]:
    module = llvm.parse_assembly(ir)
    module.verify()
    out: dict[str, dict[str, list[str]]] = {}
    for fn in module.functions:
        if fn.is_declaration:
            continue
        dom = compute_dominator_tree(fn)
        out[fn.name] = {block: dom.dominators(block) for block in dom.all_blocks()}
    return out


_CORPUS_IR = """
define i32 @same_block_redundant_add(i32 %x, i32 %y) {
entry:
  %a = add i32 %x, %y
  %b = add i32 %x, %y
  %c = add i32 %a, %b
  ret i32 %c
}

define i32 @commuted_add(i32 %x, i32 %y) {
entry:
  %a = add i32 %x, %y
  %b = add i32 %y, %x
  %c = add i32 %a, %b
  ret i32 %c
}

define i32 @dominated_redundant_add(i1 %c, i32 %x, i32 %y) {
entry:
  %a = add i32 %x, %y
  br i1 %c, label %then, label %else
then:
  %b = add i32 %x, %y
  %c1 = add i32 %a, %b
  ret i32 %c1
else:
  ret i32 0
}

define i32 @dominated_redundant_load(i1 %c, ptr %p) {
entry:
  %a = load i32, ptr %p
  br i1 %c, label %then, label %else
then:
  %b = load i32, ptr %p
  %s = add i32 %a, %b
  ret i32 %s
else:
  ret i32 0
}

define i1 @dominated_redundant_icmp(i1 %c, i32 %x, i32 %y) {
entry:
  %a = icmp eq i32 %x, %y
  br i1 %c, label %then, label %else
then:
  %b = icmp eq i32 %x, %y
  %r = and i1 %a, %b
  ret i1 %r
else:
  ret i1 false
}

define i32 @dominated_redundant_bitcast_load(i1 %c, ptr %p) {
entry:
  %q = bitcast ptr %p to ptr
  %a = load i32, ptr %p
  br i1 %c, label %then, label %else
then:
  %b = load i32, ptr %q
  %s = add i32 %a, %b
  ret i32 %s
else:
  ret i32 0
}

define i32 @dominated_redundant_gep0_load(i1 %c, ptr %p) {
entry:
  %q = getelementptr i32, ptr %p, i32 0
  %a = load i32, ptr %p
  br i1 %c, label %then, label %else
then:
  %b = load i32, ptr %q
  %s = add i32 %a, %b
  ret i32 %s
else:
  ret i32 0
}

define i32 @dominated_redundant_load_survives_noalias_store(i1 %c, ptr %p) {
entry:
  %q = alloca i32
  %a = load i32, ptr %p
  store i32 1, ptr %q
  br i1 %c, label %then, label %else
then:
  %b = load i32, ptr %p
  %s = add i32 %a, %b
  ret i32 %s
else:
  ret i32 0
}

@g = global i32 0

define i32 @dominated_redundant_global_load(i1 %c) {
entry:
  %a = load i32, ptr @g
  br i1 %c, label %then, label %else
then:
  %b = load i32, ptr @g
  %s = add i32 %a, %b
  ret i32 %s
else:
  ret i32 0
}

define i32 @dominated_redundant_global_bitcast_load(i1 %c) {
entry:
  %q = bitcast ptr @g to ptr
  %a = load i32, ptr @g
  br i1 %c, label %then, label %else
then:
  %b = load i32, ptr %q
  %s = add i32 %a, %b
  ret i32 %s
else:
  ret i32 0
}

define i32 @dominated_redundant_global_gep0_load(i1 %c) {
entry:
  %q = getelementptr i32, ptr @g, i32 0
  %a = load i32, ptr @g
  br i1 %c, label %then, label %else
then:
  %b = load i32, ptr %q
  %s = add i32 %a, %b
  ret i32 %s
else:
  ret i32 0
}
"""


class GVNTests(unittest.TestCase):
    def test_same_block_redundant_add_eliminated(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %x, %y
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = add i32 %x, %y", out)
        self.assertIn("%c = add i32 %a, %a", out)

    def test_commuted_add_eliminated(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %y, %x
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = add i32 %y, %x", out)
        self.assertIn("%c = add i32 %a, %a", out)

    def test_dominated_redundant_load_eliminated_when_memory_stable(self):
        ir = """
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %p
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %p", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_dominated_redundant_icmp_eliminated(self):
        ir = """
        define i1 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          %a = icmp eq i32 %x, %y
          br i1 %c, label %then, label %else
        then:
          %b = icmp eq i32 %x, %y
          %r = and i1 %a, %b
          ret i1 %r
        else:
          ret i1 false
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = icmp eq i32 %x, %y", out)
        self.assertNotIn("%r = and i1 %a, %a", out)
        self.assertIn("ret i1 %a", out)

    def test_bitcast_alias_load_eliminated(self):
        ir = """
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = bitcast ptr %p to ptr
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_zero_gep_alias_load_eliminated(self):
        ir = """
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = getelementptr i32, ptr %p, i32 0
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_multi_zero_gep_alias_load_eliminated(self):
        ir = """
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = getelementptr { i32 }, ptr %p, i32 0, i32 0
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_global_multi_zero_gep_alias_load_eliminated(self):
        ir = """
        @g = global { i32 } { i32 0 }
        define i32 @f(i1 %c) {
        entry:
          %q = getelementptr { i32 }, ptr @g, i32 0, i32 0
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_different_typed_loads_not_merged(self):
        ir = """
        define i64 @f(i1 %c, ptr %p) {
        entry:
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i64, ptr %p
          ret i64 %b
        else:
          ret i64 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%a = load i32, ptr %p", out)
        self.assertIn("%b = load i64, ptr %p", out)

    def test_redundant_select_then_sub_self_folds_to_zero(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          %a = select i1 %c, i32 %x, i32 %y
          %b = select i1 %c, i32 %x, i32 %y
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertIn("ret i32 0", out)
        self.assertNotIn("%b = select i1 %c, i32 %x, i32 %y", out)

    def test_redundant_zext_then_sub_self_folds_to_zero(self):
        ir = """
        define i32 @f(i1 %c) {
        entry:
          %a = zext i1 %c to i32
          %b = zext i1 %c to i32
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertIn("ret i32 0", out)
        self.assertNotIn("%b = zext i1 %c to i32", out)

    def test_redundant_gep_eliminated(self):
        ir = """
        define ptr @f(ptr %p) {
        entry:
          %a = getelementptr i32, ptr %p, i32 0
          %b = getelementptr i32, ptr %p, i32 0
          ret ptr %b
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertIn("ret ptr %p", out)
        self.assertNotIn("%b = getelementptr i32, ptr %p, i32 0", out)

    def test_same_value_phi_is_folded(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %t, label %e
        t:
          br label %j
        e:
          br label %j
        j:
          %p = phi i32 [ %x, %t ], [ %x, %e ]
          %q = add i32 %p, 1
          ret i32 %q
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%p = phi i32", out)
        self.assertIn("%q = add i32 %x, 1", out)

    def test_load_not_eliminated_across_store(self):
        ir = """
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %a = load i32, ptr %p
          store i32 1, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %p
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        _, changed = gvn_text(ir, _doms(ir))
        self.assertFalse(changed)

    def test_load_eliminated_across_noalias_alloca_store(self):
        ir = """
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = alloca i32
          %a = load i32, ptr %p
          store i32 1, ptr %q
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %p
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %p", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_global_load_eliminated(self):
        ir = """
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr @g
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr @g", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_global_bitcast_alias_load_eliminated(self):
        ir = """
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %q = bitcast ptr @g to ptr
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_global_zero_gep_alias_load_eliminated(self):
        ir = """
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %q = getelementptr i32, ptr @g, i32 0
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_out_of_order_blocks_still_use_dominating_binop(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %else, label %pre
        use:
          %b = add i32 %x, %y
          %r = add i32 %a, %b
          ret i32 %r
        pre:
          %a = add i32 %x, %y
          br label %use
        else:
          ret i32 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = add i32 %x, %y", out)
        self.assertIn("%r = add i32 %a, %a", out)

    def test_out_of_order_blocks_still_use_dominating_load(self):
        ir = """
        define i32 @f(i1 %c, ptr %p) {
        entry:
          br i1 %c, label %else, label %pre
        use:
          %b = load i32, ptr %p
          %r = add i32 %a, %b
          ret i32 %r
        pre:
          %a = load i32, ptr %p
          br label %use
        else:
          ret i32 0
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %p", out)
        self.assertIn("%r = add i32 %a, %a", out)

    def test_out_of_order_blocks_still_use_dominating_icmp(self):
        ir = """
        define i1 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %else, label %pre
        use:
          %b = icmp eq i32 %x, %y
          %r = and i1 %a, %b
          ret i1 %r
        pre:
          %a = icmp eq i32 %x, %y
          br label %use
        else:
          ret i1 false
        }
        """
        out, changed = gvn_text(ir, _doms(ir))
        self.assertTrue(changed)
        self.assertNotIn("%b = icmp eq i32 %x, %y", out)
        self.assertIn("ret i1 %a", out)

    def test_pass_end_to_end(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %y, %x
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        p = GVNPass()
        out, _ = run_pcc_ir_pass(ir, p)
        self.assertIn("%c = add i32 %a, %a", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, GVNPass(), "gvn")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_commuted_add(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %y, %x
          %c = add i32 %a, %b
          ret i32 %c
        }
        """)

    def test_dominated_redundant_load(self):
        self._parity("""
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %p
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_dominated_redundant_icmp(self):
        self._parity("""
        define i1 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          %a = icmp eq i32 %x, %y
          br i1 %c, label %then, label %else
        then:
          %b = icmp eq i32 %x, %y
          %r = and i1 %a, %b
          ret i1 %r
        else:
          ret i1 false
        }
        """)

    def test_dominated_redundant_bitcast_load(self):
        self._parity("""
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = bitcast ptr %p to ptr
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_dominated_redundant_gep0_load(self):
        self._parity("""
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = getelementptr i32, ptr %p, i32 0
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_dominated_redundant_multi_zero_gep_load(self):
        self._parity("""
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = getelementptr { i32 }, ptr %p, i32 0, i32 0
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_dominated_redundant_load_survives_noalias_store(self):
        self._parity("""
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = alloca i32
          %a = load i32, ptr %p
          store i32 1, ptr %q
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %p
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_dominated_redundant_global_load(self):
        self._parity("""
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr @g
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_dominated_redundant_global_bitcast_load(self):
        self._parity("""
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %q = bitcast ptr @g to ptr
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_dominated_redundant_global_gep0_load(self):
        self._parity("""
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %q = getelementptr i32, ptr @g, i32 0
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_dominated_redundant_global_multi_zero_gep_load(self):
        self._parity("""
        @g = global { i32 } { i32 0 }
        define i32 @f(i1 %c) {
        entry:
          %q = getelementptr { i32 }, ptr @g, i32 0, i32 0
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_module_corpus_matches_upstream(self):
        self._parity(_CORPUS_IR)

    def test_different_typed_loads_do_not_merge(self):
        self._parity("""
        define i64 @f(i1 %c, ptr %p) {
        entry:
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i64, ptr %p
          ret i64 %b
        else:
          ret i64 0
        }
        """)

    def test_redundant_select_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          %a = select i1 %c, i32 %x, i32 %y
          %b = select i1 %c, i32 %x, i32 %y
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_redundant_zext_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c) {
        entry:
          %a = zext i1 %c to i32
          %b = zext i1 %c to i32
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_redundant_gep_matches_upstream(self):
        self._parity("""
        define ptr @f(ptr %p) {
        entry:
          %a = getelementptr i32, ptr %p, i32 0
          %b = getelementptr i32, ptr %p, i32 0
          ret ptr %b
        }
        """)

    def test_same_value_phi_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %t, label %e
        t:
          br label %j
        e:
          br label %j
        j:
          %p = phi i32 [ %x, %t ], [ %x, %e ]
          %q = add i32 %p, 1
          ret i32 %q
        }
        """)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
