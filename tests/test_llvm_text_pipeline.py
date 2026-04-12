import llvmlite.binding as llvm
import pytest

from pcc.evaluater.c_evaluator import (
    _apply_external_llvm_pipeline_to_text,
    _resolve_external_llvm_pipeline_spec,
)
from pcc.passes.llvm_builtin_registry import LLVM_DEFAULT_PROFILE_PASSES
from pcc.passes import (
    PassContext,
    default_profile_pass_names,
    find_opt_binary,
    leaf_pass_names,
    parse_pipeline,
    prune_disabled_passes,
    serialize_pipeline,
    unique_managed_pass_names,
)


def test_parse_and_serialize_nested_llvm_pipeline_round_trips():
    text = (
        "function<eager-inv>(instcombine<max-iterations=1;no-verify-fixpoint>,"
        "loop(loop-idiom,loop-deletion),verify),globaldce"
    )
    nodes = parse_pipeline(text)

    assert serialize_pipeline(nodes) == text
    assert leaf_pass_names(nodes) == (
        "instcombine",
        "loop-idiom",
        "loop-deletion",
        "verify",
        "globaldce",
    )


def test_prune_disabled_passes_drops_leafs_and_empty_wrappers():
    nodes = parse_pipeline(
        "function(instcombine,adce),verify,function(instcombine)"
    )
    pruned = prune_disabled_passes(nodes, {"instcombine"})

    assert serialize_pipeline(pruned) == "function(adce),verify"
    assert leaf_pass_names(pruned) == ("adce", "verify")


def test_external_pipeline_default_profile_tracks_opt_level():
    assert _resolve_external_llvm_pipeline_spec(2, "default") == "default<O2>"
    assert _resolve_external_llvm_pipeline_spec(3, "1") == "default<O3>"
    assert _resolve_external_llvm_pipeline_spec(0, "off") == ""


def test_checked_in_llvm_default_pass_registry_is_visible_in_repo():
    assert "instcombine" in LLVM_DEFAULT_PROFILE_PASSES[2]
    assert "loop-vectorize" in LLVM_DEFAULT_PROFILE_PASSES[2]
    assert "slp-vectorizer" in LLVM_DEFAULT_PROFILE_PASSES[2]
    assert "argpromotion" in LLVM_DEFAULT_PROFILE_PASSES[3]


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
def test_default_profile_pass_names_exposes_concrete_llvm_registry():
    names = default_profile_pass_names(2)

    assert "instcombine" in names
    assert "simplifycfg" in names
    assert "loop-vectorize" in names
    assert "function" not in names
    assert "cgscc" not in names
    assert "instcombine" in unique_managed_pass_names(include_llvm=True)


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
def test_external_llvm_pipeline_runs_and_records_concrete_passes(monkeypatch):
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()

    monkeypatch.setenv("PCC_LLVM_PIPELINE", "instcombine,simplifycfg")
    monkeypatch.setenv("PCC_LLVM_DISABLE_PASSES", "instcombine")

    ctx = PassContext()
    ir_text = """
; ModuleID = 'x'
source_filename = "x"

define i32 @main() {
entry:
  br i1 true, label %yes, label %no

yes:
  ret i32 0

no:
  ret i32 1
}
""".strip()

    optimized, status = _apply_external_llvm_pipeline_to_text(
        ir_text, 2, pass_ctx=ctx
    )

    assert status == "external-text-pipeline"
    assert ctx.pass_metrics["instcombine"].skips >= 1
    assert ctx.pass_metrics["simplifycfg"].runs >= 1
    llvm.parse_assembly(optimized)
