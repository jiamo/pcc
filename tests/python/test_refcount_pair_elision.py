"""Finite correctness controls for PERF-P3-RC-ELISION."""

from __future__ import annotations

from pcc.ir_passes.refcount_pair_elision import elide_refcount_pairs
from pcc.py_frontend.ir_pass_pipeline import run_python_ir_pass_pipeline


_PRELUDE = """\
declare ptr @pcc_gc_retain(ptr)
declare void @pcc_gc_release(ptr)
declare void @may_retain_or_finalize(ptr)
declare void @pcc_debug_check_release(ptr)
"""


def _function(body: str) -> str:
    return _PRELUDE + """
define i1 @probe(ptr %source, ptr %other, ptr %slot) {
entry:
""" + body + """
  ret i1 false
}
"""


def test_backend0_elides_adjacent_branchless_temporary_pair():
    source = _function("""
  %owned = call ptr @pcc_gc_retain(ptr %source)
  %temporary = call ptr @pcc_gc_retain(ptr %owned)
  call void @pcc_gc_release(ptr %temporary)
  call void @pcc_gc_release(ptr %owned)
""")
    rewritten, count = elide_refcount_pairs(source, gc_backend=0)
    assert count == 1
    assert rewritten.count("call ptr @pcc_gc_retain") == 1
    assert rewritten.count("call void @pcc_gc_release") == 1
    assert "%temporary" not in rewritten


def test_backend0_elides_one_nondestructive_single_use_observation():
    source = _function("""
  %owned = call ptr @pcc_gc_retain(ptr %source)
  %temporary = call ptr @pcc_gc_retain(ptr %owned)
  %same = icmp eq ptr %temporary, %other
  call void @pcc_gc_release(ptr %temporary)
  call void @pcc_gc_release(ptr %owned)
  ret i1 %same
""").replace("  ret i1 false\n", "")
    rewritten, count = elide_refcount_pairs(source, gc_backend=0)
    assert count == 1
    assert "%same = icmp eq ptr %owned, %other" in rewritten
    assert "%temporary" not in rewritten


def test_non_refcount_collectors_never_elide_the_pair():
    source = _function("""
  %owned = call ptr @pcc_gc_retain(ptr %source)
  %temporary = call ptr @pcc_gc_retain(ptr %owned)
  call void @pcc_gc_release(ptr %temporary)
  call void @pcc_gc_release(ptr %owned)
""")
    for backend in (1, 2, 3, 4):
        rewritten, count = elide_refcount_pairs(source, gc_backend=backend)
        assert rewritten == source
        assert count == 0


def test_explicit_pipeline_pass_is_backend0_only(monkeypatch):
    source = _function("""
  %owned = call ptr @pcc_gc_retain(ptr %source)
  %temporary = call ptr @pcc_gc_retain(ptr %owned)
  call void @pcc_gc_release(ptr %temporary)
  call void @pcc_gc_release(ptr %owned)
""")
    monkeypatch.setenv("PCC_GC_BACKEND", "0")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "text")
    rewritten = run_python_ir_pass_pipeline(
        source,
        pass_names=("pcc-rc-elision",),
        module_name="rc_pair_fixture",
    )
    assert rewritten.count("call ptr @pcc_gc_retain") == 1
    assert rewritten.count("call void @pcc_gc_release") == 1


def test_store_escape_call_and_second_use_are_controls():
    controls = (
        """
  %owned = call ptr @pcc_gc_retain(ptr %source)
  %temporary = call ptr @pcc_gc_retain(ptr %owned)
  store ptr %temporary, ptr %slot
  call void @pcc_gc_release(ptr %temporary)
  call void @pcc_gc_release(ptr %owned)
""",
        """
  %owned = call ptr @pcc_gc_retain(ptr %source)
  %temporary = call ptr @pcc_gc_retain(ptr %owned)
  call void @may_retain_or_finalize(ptr %temporary)
  call void @pcc_gc_release(ptr %temporary)
  call void @pcc_gc_release(ptr %owned)
""",
        """
  %owned = call ptr @pcc_gc_retain(ptr %source)
  %temporary = call ptr @pcc_gc_retain(ptr %owned)
  %same = icmp eq ptr %temporary, %other
  %again = ptrtoint ptr %temporary to i64
  call void @pcc_gc_release(ptr %temporary)
  call void @pcc_gc_release(ptr %owned)
""",
    )
    for body in controls:
        source = _function(body)
        rewritten, count = elide_refcount_pairs(source, gc_backend=0)
        assert rewritten == source
        assert count == 0


def test_borrowed_source_without_outer_owned_guard_is_a_control():
    source = _function("""
  %temporary = call ptr @pcc_gc_retain(ptr %source)
  call void @pcc_gc_release(ptr %temporary)
""")
    rewritten, count = elide_refcount_pairs(source, gc_backend=0)
    assert rewritten == source
    assert count == 0


def test_branch_exception_edge_root_and_debug_guard_are_controls():
    controls = (
        """
  %owned = call ptr @pcc_gc_retain(ptr %source)
  %temporary = call ptr @pcc_gc_retain(ptr %owned)
  br label %done
done:
  call void @pcc_gc_release(ptr %temporary)
  call void @pcc_gc_release(ptr %owned)
""",
        """
  %owned = call ptr @pcc_gc_retain(ptr %source)
  %temporary = call ptr @pcc_gc_retain(ptr %owned)
  %value = load ptr, ptr %temporary
  call void @pcc_gc_release(ptr %temporary)
  call void @pcc_gc_release(ptr %owned)
""",
        """
  %owned = call ptr @pcc_gc_retain(ptr %source)
  %temporary = call ptr @pcc_gc_retain(ptr %owned)
  call void @pcc_debug_check_release(ptr %temporary)
  call void @pcc_gc_release(ptr %temporary)
  call void @pcc_gc_release(ptr %owned)
""",
    )
    for body in controls:
        source = _function(body)
        rewritten, count = elide_refcount_pairs(source, gc_backend=0)
        assert rewritten == source
        assert count == 0
