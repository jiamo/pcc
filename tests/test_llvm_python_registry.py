import pytest
import re

from pcc.evaluater.c_evaluator import (
    _apply_external_llvm_pipeline_to_text,
    _compile_preprocessed_translation_unit_artifact,
)
from pcc.passes.llvm_builtin_registry import LLVM_DEFAULT_PROFILE_PASSES
from pcc.passes import (
    PassContext,
    PassPipeline,
    expand_registered_pass_name,
    find_opt_binary,
    llvm_python_translation,
    unique_managed_pass_names,
)


def _ir_needle_count(ir_text: str, needle: str) -> int:
    if needle == 'call i32 @"wrap"':
        pattern = re.compile(
            r'\b(?:tail\s+)?call\s+i32(?:\s+\([^@]*\))?\s+@(?:"wrap"|wrap)\('
        )
        return len(pattern.findall(ir_text))
    return ir_text.count(needle)


def _pipeline_without_high_passes(*pass_names):
    pipeline = PassPipeline.default()
    disabled = set(pass_names)
    pipeline.high_tier = [
        pass_ for pass_ in pipeline.high_tier if pass_.name not in disabled
    ]
    return pipeline


def test_ir_backed_passes_expose_ir_pass_class():
    # Passes that got an IR-level implementation via the
    # all-pass-llvm-ir-1to1-master-plan.md migration must expose the
    # ``ir_pass_class`` field and carry a non-deprecated status.
    import importlib
    for llvm_name in ("instsimplify", "dce", "adce", "sccp", "gvn",
                      "reassociate", "dse", "early-cse", "globaldce",
                      "elim-avail-extern", "annotation-remarks",
                      "cg-profile", "ee-instrument",
                      "coro-annotation-elide", "coro-cleanup", "coro-early",
                      "coro-elide", "coro-split",
                      "simplifycfg", "bdce", "instcombine",
                      "callsite-splitting", "loop-rotate",
                      "loop-simplifycfg", "loop-instsimplify",
                      "extra-simple-loop-unswitch-passes",
                      "loop-sink", "lower-expect", "inferattrs",
                      "libcalls-shrinkwrap", "require", "invalidate", "verify",
                      "openmp-opt", "openmp-opt-cgscc",
                      "rel-lookup-table-converter", "transform-warning",
                      "lower-constant-intrinsics",
                      "infer-alignment",
                      "annotation2metadata", "forceattrs",
                      "inject-tli-mappings", "recompute-globalsaa",
                      "aggressive-instcombine",
                      "alignment-from-assumptions",
                      "chr", "float2int", "loop-idiom",
                      "memcpyopt", "move-auto-init",
                      "speculative-execution",
                      "tailcallelim"):
        entry = llvm_python_translation(llvm_name)
        assert entry is not None, f"{llvm_name} missing from registry"
        assert entry.ir_pass_class, f"{llvm_name} has no ir_pass_class"
        assert entry.status in {"subset", "equivalent", "migration-scaffold"}, (
            f"{llvm_name} has stale status {entry.status!r}"
        )
        # Importable?
        module_path, class_name = entry.ir_pass_class.split(":", 1)
        module = importlib.import_module(module_path)
        assert hasattr(module, class_name), (
            f"{llvm_name} references {entry.ir_pass_class} but not importable"
        )


def test_llvm_python_translation_registry_is_source_backed():
    # Per docs/plans/all-pass-llvm-ir-1to1-master-plan.md, the effective
    # registry layers an IR-level backing over the underlying
    # source-level entries. Even after that overlay promotes every
    # pass to ``subset`` or ``equivalent``, the source-level
    # translation story (``python_passes`` + ``upstream_sources``)
    # must still be preserved for the diagnostic / fallback surface.
    entry = llvm_python_translation("aggressive-instcombine")

    assert entry is not None
    assert entry.python_passes == ("canonicalize", "expr-reassociation", "copy-propagation")
    assert entry.status in {"subset", "equivalent"}
    assert entry.upstream_sources == (
        "llvm/lib/Transforms/AggressiveInstCombine/AggressiveInstCombine.cpp",
    )


@pytest.mark.parametrize(
    "llvm_name",
    [
        "aggressive-instcombine",
        "adce",
        "alignment-from-assumptions",
        "argpromotion",
        "annotation-remarks",
        "bdce",
        "cg-profile",
        "called-value-propagation",
        "callsite-splitting",
        "chr",
        "constraint-elimination",
        "constmerge",
        "correlated-propagation",
        "coro-annotation-elide",
        "coro-cleanup",
        "coro-early",
        "coro-elide",
        "coro-split",
        "deadargelim",
        "div-rem-pairs",
        "dse",
        "early-cse",
        "ee-instrument",
        "elim-avail-extern",
        "infer-alignment",
        "extra-simple-loop-unswitch-passes",
        "float2int",
        "forceattrs",
        "function-attrs",
        "globaldce",
        "globalopt",
        "gvn",
        "inferattrs",
        "invalidate",
        "inject-tli-mappings",
        "inline",
        "indvars",
        "instcombine",
        "instsimplify",
        "jump-threading",
        "libcalls-shrinkwrap",
        "licm",
        "loop-deletion",
        "loop-distribute",
        "loop-idiom",
        "loop-instsimplify",
        "loop-load-elim",
        "loop-rotate",
        "loop-sink",
        "loop-simplifycfg",
        "loop-unroll",
        "loop-unroll-full",
        "loop-vectorize",
        "lower-expect",
        "lower-constant-intrinsics",
        "mem2reg",
        "memcpyopt",
        "mldst-motion",
        "move-auto-init",
        "newgvn",
        "openmp-opt",
        "openmp-opt-cgscc",
        "recompute-globalsaa",
        "reassociate",
        "rel-lookup-table-converter",
        "require",
        "rpo-function-attrs",
        "sccp",
        "simplifycfg",
        "simple-loop-unswitch",
        "slp-vectorizer",
        "sroa",
        "speculative-execution",
        "always-inline",
        "tailcallelim",
        "transform-warning",
        "vector-combine",
        "verify",
        "annotation2metadata",
    ],
)
def test_selected_explicit_boundary_passes_have_source_approximation(llvm_name):
    # These passes previously carried `status="full"` as a placeholder for
    # "has a source-level story." Per all-pass-llvm-ir-1to1-master-plan.md
    # that label is retired; the honest status until an IR-level
    # replacement lands is ``deprecated-source-approximation``.
    # `equivalent` and `subset` become valid statuses only after the
    # upstream-anchored IR-level implementation + parity harness land.
    entry = llvm_python_translation(llvm_name)
    assert entry.status in {
        "deprecated-source-approximation",
        "subset",
        "equivalent",
        "fallback-only",
        "migration-scaffold",
    }, f"{llvm_name} has unexpected status {entry.status!r}"


def test_registered_pass_name_expands_to_python_and_llvm_layers():
    assert expand_registered_pass_name("function-attrs") == (
        "func-attr",
        "function-attrs",
    )
    assert expand_registered_pass_name("rpo-function-attrs") == (
        "func-attr",
        "rpo-function-attrs",
    )
    assert expand_registered_pass_name("tailcallelim") == (
        "tail-call",
        "tailcallelim",
    )
    assert expand_registered_pass_name("lower-expect") == ("lower-expect",)
    assert expand_registered_pass_name("globaldce") == (
        "global-dce",
        "globaldce",
    )
    assert expand_registered_pass_name("verify") == ("verify",)
    assert expand_registered_pass_name("lower-constant-intrinsics") == (
        "lower-constant-intrinsics",
    )
    assert expand_registered_pass_name("alignment-from-assumptions") == (
        "alignment-from-assumptions",
    )
    assert expand_registered_pass_name("forceattrs") == (
        "func-attr",
        "forceattrs",
    )
    assert expand_registered_pass_name("libcalls-shrinkwrap") == (
        "libcalls-shrinkwrap",
    )
    assert expand_registered_pass_name("instsimplify") == (
        "canonicalize",
        "instsimplify",
    )
    assert expand_registered_pass_name("reassociate") == (
        "expr-reassociation",
        "reassociate",
    )


def test_rpo_function_attrs_has_ir_equivalent_backing():
    entry = llvm_python_translation("rpo-function-attrs")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.function_attrs:FunctionAttrsPass"
    )


def test_callsite_splitting_has_ir_equivalent_backing():
    entry = llvm_python_translation("callsite-splitting")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.callsite_splitting:CallSiteSplittingPass"
    )


def test_loop_rotate_has_ir_equivalent_backing():
    entry = llvm_python_translation("loop-rotate")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.loop_rotate:LoopRotatePass"
    )


def test_loop_simplifycfg_has_ir_equivalent_backing():
    entry = llvm_python_translation("loop-simplifycfg")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.loop_simplifycfg:LoopSimplifyCFGPass"
    )


def test_loop_simplify_has_ir_equivalent_backing():
    entry = llvm_python_translation("loop-simplify")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.loop_simplify:LoopSimplifyPass"
    )


def test_loop_instsimplify_has_ir_equivalent_backing():
    entry = llvm_python_translation("loop-instsimplify")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.loop_instsimplify:LoopInstSimplifyPass"
    )


def test_loop_sink_has_ir_equivalent_backing():
    entry = llvm_python_translation("loop-sink")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.loop_sink:LoopSinkPass"
    )


def test_elim_avail_extern_has_ir_equivalent_backing():
    entry = llvm_python_translation("elim-avail-extern")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.elim_avail_extern:ElimAvailExternPass"
    )


def test_tailcallelim_has_ir_equivalent_backing():
    entry = llvm_python_translation("tailcallelim")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.tailcallelim:TailCallElimPass"
    )


def test_lower_expect_has_ir_equivalent_backing():
    entry = llvm_python_translation("lower-expect")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.lower_expect:LowerExpectPass"
    )


def test_inferattrs_has_ir_equivalent_backing():
    entry = llvm_python_translation("inferattrs")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.function_attrs:FunctionAttrsPass"
    )


def test_libcalls_shrinkwrap_has_ir_equivalent_backing():
    entry = llvm_python_translation("libcalls-shrinkwrap")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.libcalls_shrinkwrap:LibcallsShrinkwrapPass"
    )


def test_annotation_remarks_has_ir_equivalent_backing():
    entry = llvm_python_translation("annotation-remarks")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.meta_passes:AnnotationRemarksIRPass"
    )


def test_cg_profile_has_ir_equivalent_backing():
    entry = llvm_python_translation("cg-profile")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:CGProfileIRPass"


def test_ee_instrument_has_ir_equivalent_backing():
    entry = llvm_python_translation("ee-instrument")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:EEInstrumentIRPass"


@pytest.mark.parametrize(
    "llvm_name",
    [
        "coro-annotation-elide",
        "coro-cleanup",
        "coro-early",
        "coro-elide",
        "coro-split",
    ],
)
def test_coro_boundary_passes_have_ir_equivalent_backing(llvm_name):
    entry = llvm_python_translation(llvm_name)
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:CoroBoundaryIRPass"


def test_lower_constant_intrinsics_has_ir_equivalent_backing():
    entry = llvm_python_translation("lower-constant-intrinsics")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.lower_constant_intrinsics:LowerConstantIntrinsicsPass"
    )


def test_require_has_ir_equivalent_backing():
    entry = llvm_python_translation("require")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:RequireIRPass"


def test_invalidate_has_ir_equivalent_backing():
    entry = llvm_python_translation("invalidate")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:InvalidateIRPass"


def test_verify_has_ir_equivalent_backing():
    entry = llvm_python_translation("verify")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:VerifyIRPass"


def test_openmp_opt_has_ir_equivalent_backing():
    entry = llvm_python_translation("openmp-opt")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:OpenMPOptIRPass"


def test_openmp_opt_cgscc_has_ir_equivalent_backing():
    entry = llvm_python_translation("openmp-opt-cgscc")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:OpenMPCGSCCIRPass"


def test_rel_lookup_table_converter_has_ir_equivalent_backing():
    entry = llvm_python_translation("rel-lookup-table-converter")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.meta_passes:RelLookupTableConverterIRPass"
    )


def test_extra_simple_loop_unswitch_has_ir_equivalent_backing():
    entry = llvm_python_translation("extra-simple-loop-unswitch-passes")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.simple_loop_unswitch:SimpleLoopUnswitchPass"
    )


def test_simple_loop_unswitch_has_ir_equivalent_backing():
    entry = llvm_python_translation("simple-loop-unswitch")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.simple_loop_unswitch:SimpleLoopUnswitchPass"
    )


def test_loop_distribution_and_vector_leaf_passes_have_ir_equivalent_backing():
    for name, ir_class in (
        ("loop-distribute", "pcc.ir_passes.loop_distribute:LoopDistributePass"),
        ("loop-vectorize", "pcc.ir_passes.loop_vectorize:LoopVectorizePass"),
        ("slp-vectorizer", "pcc.ir_passes.slp_vectorizer:SLPVectorizerPass"),
    ):
        entry = llvm_python_translation(name)
        assert entry is not None
        assert entry.status == "equivalent"
        assert entry.implementation_tier == "mixed"
        assert entry.ir_pass_class == ir_class


def test_transform_warning_has_ir_equivalent_backing():
    entry = llvm_python_translation("transform-warning")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:TransformWarningIRPass"


def test_aggressive_instcombine_has_ir_equivalent_backing():
    entry = llvm_python_translation("aggressive-instcombine")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.meta_passes:AggressiveInstCombineIRPass"
    )


def test_alignment_from_assumptions_has_ir_equivalent_backing():
    entry = llvm_python_translation("alignment-from-assumptions")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.alignment_from_assumptions:AlignmentFromAssumptionsIRPass"
    )


def test_annotation2metadata_has_ir_equivalent_backing():
    entry = llvm_python_translation("annotation2metadata")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.meta_passes:Annotation2MetadataIRPass"
    )


def test_chr_has_ir_equivalent_backing():
    entry = llvm_python_translation("chr")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:ChrIRPass"


def test_float2int_has_ir_equivalent_backing():
    entry = llvm_python_translation("float2int")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.float2int:Float2IntIRPass"


def test_forceattrs_has_ir_equivalent_backing():
    entry = llvm_python_translation("forceattrs")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:ForceAttrsIRPass"


def test_infer_alignment_has_ir_equivalent_backing():
    entry = llvm_python_translation("infer-alignment")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.infer_alignment:InferAlignmentPass"


def test_inject_tli_mappings_has_ir_equivalent_backing():
    entry = llvm_python_translation("inject-tli-mappings")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.meta_passes:InjectTLIMappingsIRPass"
    )


def test_loop_idiom_has_ir_equivalent_backing():
    entry = llvm_python_translation("loop-idiom")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:LoopIdiomIRPass"


def test_memcpyopt_has_ir_equivalent_backing():
    entry = llvm_python_translation("memcpyopt")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.memcpyopt:MemCpyOptIRPass"


def test_move_auto_init_has_ir_equivalent_backing():
    entry = llvm_python_translation("move-auto-init")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert entry.ir_pass_class == "pcc.ir_passes.meta_passes:MoveAutoInitIRPass"


def test_recompute_globalsaa_has_ir_equivalent_backing():
    entry = llvm_python_translation("recompute-globalsaa")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.meta_passes:RecomputeGlobalsAAIRPass"
    )


def test_speculative_execution_has_ir_equivalent_backing():
    entry = llvm_python_translation("speculative-execution")
    assert entry is not None
    assert entry.status == "equivalent"
    assert entry.implementation_tier == "mixed"
    assert (
        entry.ir_pass_class
        == "pcc.ir_passes.speculative_execution:SpeculativeExecutionIRPass"
    )


@pytest.mark.parametrize(
    ("llvm_name", "expected"),
    [
        (
            "aggressive-instcombine",
            (
                "canonicalize",
                "expr-reassociation",
                "copy-propagation",
                "aggressive-instcombine",
            ),
        ),
        ("adce", ("ssa-adce", "dce", "adce")),
        ("argpromotion", ("inline-opt", "sroa", "alloc-decision", "argpromotion")),
        ("bdce", ("ssa-adce", "dce", "bdce")),
        (
            "called-value-propagation",
            ("inline-opt", "copy-propagation", "called-value-propagation"),
        ),
        ("callsite-splitting", ("inline-opt", "control-flow", "callsite-splitting")),
        ("constraint-elimination", ("control-flow", "canonicalize", "constraint-elimination")),
        ("correlated-propagation", ("control-flow", "canonicalize", "dce", "correlated-propagation")),
        ("deadargelim", ("deadargelim-analysis", "inline-opt", "dce", "deadargelim")),
        ("instcombine", ("canonicalize", "expr-reassociation", "copy-propagation", "instcombine")),
        ("early-cse", ("local-value-numbering", "copy-propagation", "early-cse")),
        ("dse", ("ssa-dse", "ssa-adce", "dce", "memory-opt-ir", "dse")),
        ("elim-avail-extern", ("elim-avail-extern-src", "elim-avail-extern")),
        ("globalopt", ("canonicalize", "dce", "inline-opt", "globalopt")),
        (
            "gvn",
            (
                "ssa-gvn",
                "ssa-gvn-rewrite",
                "gvn",
                "local-value-numbering",
                "copy-propagation",
            ),
        ),
        ("jump-threading", ("control-flow", "jump-threading")),
        ("instsimplify", ("canonicalize", "instsimplify")),
        ("indvars", ("indvars",)),
        ("loop-idiom", ("loop-opt", "loop-idiom")),
        ("loop-instsimplify", ("loop-opt", "canonicalize", "loop-instsimplify")),
        ("loop-rotate", ("loop-rotate",)),
        ("loop-load-elim", ("memory-opt-ir", "loop-load-elim")),
        ("loop-sink", ("loop-opt", "loop-sink")),
        ("loop-simplifycfg", ("control-flow", "dce", "loop-simplifycfg")),
        ("infer-alignment", ("align", "infer-alignment")),
        ("rpo-function-attrs", ("func-attr", "rpo-function-attrs")),
        ("forceattrs", ("func-attr", "forceattrs")),
        ("licm", ("licm",)),
        ("simple-loop-unswitch", ("simple-loop-unswitch",)),
        ("loop-deletion", ("loop-deletion",)),
        ("loop-unroll", ("loop-unroll",)),
        ("loop-unroll-full", ("loop-unroll-full",)),
        ("inline", ("inline-opt", "inline")),
        ("always-inline", ("inline-opt", "always-inline")),
        ("lower-expect", ("lower-expect",)),
        ("mem2reg", ("alloc-decision", "sroa", "mem2reg")),
        ("memcpyopt", ("memory-opt-ir", "memcpyopt")),
        ("mldst-motion", ("memory-opt-ir", "mldst-motion")),
        (
            "newgvn",
            (
                "ssa-gvn",
                "ssa-gvn-rewrite",
                "gvn",
                "local-value-numbering",
                "copy-propagation",
                "newgvn",
            ),
        ),
        ("ipsccp", ("canonicalize", "dce", "inline-opt", "ipsccp")),
        ("globaldce", ("global-dce", "globaldce")),
        ("libcalls-shrinkwrap", ("libcalls-shrinkwrap",)),
        ("openmp-opt", ("openmp-opt",)),
        ("float2int", ("float2int",)),
        ("div-rem-pairs", ("div-rem-pairs",)),
        ("constmerge", ("constmerge",)),
        ("reassociate", ("expr-reassociation", "reassociate")),
        (
            "sccp",
            (
                "ssa-sccp",
                "ssa-sccp-rewrite",
                "ssa-branch-prune",
                "canonicalize",
                "dce",
                "sccp",
            ),
        ),
        ("simplifycfg", ("canonicalize", "control-flow", "dce", "simplifycfg")),
        ("slp-vectorizer", ("slp-vectorizer",)),
        ("sroa", ("sroa",)),
        ("speculative-execution", ("control-flow", "canonicalize", "speculative-execution")),
    ],
)
def test_more_registered_llvm_names_expand_to_python_equivalents(
    llvm_name, expected
):
    assert expand_registered_pass_name(llvm_name) == expected


def test_unique_managed_pass_names_exposes_registered_llvm_aliases_at_o0():
    names = unique_managed_pass_names(opt_level=0, include_llvm=False)

    assert "function-attrs" in names
    assert "tailcallelim" in names
    assert "reassociate" in names
    assert "aggressive-instcombine" in names
    assert "adce" in names
    assert "argpromotion" in names
    assert "bdce" in names
    assert "called-value-propagation" in names
    assert "callsite-splitting" in names
    assert "constraint-elimination" in names
    assert "correlated-propagation" in names
    assert "deadargelim" in names
    assert "early-cse" in names
    assert "elim-avail-extern" in names
    assert "instcombine" in names
    assert "dse" in names
    assert "globalopt" in names
    assert "inline" in names
    assert "ipsccp" in names
    assert "jump-threading" in names
    assert "mem2reg" in names
    assert "memcpyopt" in names
    assert "mldst-motion" in names
    assert "globaldce" in names
    assert "loop-idiom" in names
    assert "loop-instsimplify" in names
    assert "loop-load-elim" in names
    assert "loop-sink" in names
    assert "loop-simplifycfg" in names
    assert "lower-expect" in names
    assert "lower-constant-intrinsics" in names
    assert "alignment-from-assumptions" in names
    assert "verify" in names
    assert "openmp-opt" in names
    assert "div-rem-pairs" in names
    assert "constmerge" in names
    assert "instsimplify" in names
    assert "sccp" in names
    assert "simplifycfg" in names
    assert "sroa" in names
    assert "speculative-execution" in names


def test_all_default_llvm_leaf_passes_have_explicit_python_registrations():
    defaults = []
    for opt_level in sorted(LLVM_DEFAULT_PROFILE_PASSES):
        for pass_name in LLVM_DEFAULT_PROFILE_PASSES[opt_level]:
            if pass_name not in defaults:
                defaults.append(pass_name)

    missing = [
        pass_name
        for pass_name in defaults
        if llvm_python_translation(pass_name).python_passes == ()
    ]

    assert missing == []


def test_registered_disable_alias_can_turn_off_python_function_attrs(monkeypatch):
    code = "int leaf(int x){return x+1;} int main(void){return leaf(1);}"

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert "nounwind" in default_artifact["ir_text"]
    assert "nofree" in default_artifact["ir_text"]
    assert "willreturn" in default_artifact["ir_text"]

    monkeypatch.setenv("PCC_DISABLE_PASSES", "function-attrs")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "nounwind" not in disabled_artifact["ir_text"]
    assert "nofree" not in disabled_artifact["ir_text"]
    assert "willreturn" not in disabled_artifact["ir_text"]
    metric = disabled_artifact["pass_report"]["passes"]["func-attr"]
    assert metric["skips"] >= 1


def test_registered_disable_alias_can_turn_off_python_forceattrs(monkeypatch):
    code = "int leaf(int x){return x+1;} int main(void){return leaf(1);}"

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert "nounwind" in default_artifact["ir_text"]
    assert "nofree" in default_artifact["ir_text"]
    assert "willreturn" in default_artifact["ir_text"]

    monkeypatch.setenv("PCC_DISABLE_PASSES", "forceattrs")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "nounwind" not in disabled_artifact["ir_text"]
    assert "nofree" not in disabled_artifact["ir_text"]
    assert "willreturn" not in disabled_artifact["ir_text"]
    metric = disabled_artifact["pass_report"]["passes"]["func-attr"]
    assert metric["skips"] >= 1


def test_registered_disable_alias_can_turn_off_python_tail_call_marking(monkeypatch):
    code = "int leaf(int x){return x+1;} int main(void){return leaf(1);}"

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert "tail call" in default_artifact["ir_text"]

    monkeypatch.setenv("PCC_DISABLE_PASSES", "tailcallelim")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "tail call" not in disabled_artifact["ir_text"]
    metric = disabled_artifact["pass_report"]["passes"]["tail-call"]
    assert metric["skips"] >= 1


@pytest.mark.parametrize(
    ("alias", "code", "needle", "default_count", "disabled_count", "metric_names"),
    [
        (
            "aggressive-instcombine",
            "int f(int x){ return (x + 0) + (2 + 3); }",
            " add ",
            1,
            3,
            ("canonicalize", "expr-reassociation", "copy-propagation"),
        ),
        (
            "adce",
            "int f(int a){ int x = a + 1; return a; }",
            " add ",
            0,
            1,
            ("ssa-adce", "dce"),
        ),
        (
            "argpromotion",
            (
                "int leaf(int x){ return x + 1; } "
                "int wrap(int x){ return leaf(x); } "
                "int f(void){ struct S { int a; int b; }; struct S s; "
                "s.a=7; s.b=8; int y=s.a; return wrap(y); }"
            ),
            'call i32 @"wrap"',
            0,
            1,
            ("inline-opt", "sroa", "alloc-decision"),
        ),
        (
            "bdce",
            "int f(int a){ int x = a + 1; return a; }",
            " add ",
            0,
            1,
            ("ssa-adce", "dce"),
        ),
        (
            "called-value-propagation",
            (
                "int id(int x){ return x; } "
                "int wrap(int x){ return id(x); } "
                "int f(void){ int y=7; int z=y; return wrap(z); }"
            ),
            'call i32 @"wrap"',
            0,
            1,
            ("inline-opt", "copy-propagation"),
        ),
        (
            "callsite-splitting",
            (
                "int id(int x){ return x; } "
                "int wrap(int x){ return id(x); } "
                "int f(int c,int x){ if (c) return wrap(x); return wrap(x + 1); }"
            ),
            'call i32 @"wrap"',
            0,
            2,
            ("inline-opt", "control-flow"),
        ),
        (
            "instcombine",
            "int f(int x){ return (x + 0) + (2 + 3); }",
            " add ",
            1,
            3,
            ("canonicalize", "expr-reassociation", "copy-propagation"),
        ),
        (
            "dse",
            "int f(int a){ int x=0; x=a+1; x=a+2; return x; }",
            "store ",
            2,
            4,
            ("ssa-dse", "ssa-adce", "dce", "memory-opt-ir"),
        ),
            (
                "globalopt",
                (
                    "static int id(int x){ return x; } "
                    "static int wrap(int x){ return id(x); } "
                    "int f(void){ int y=7; int z=y; return wrap(z); }"
                ),
                "alloca",
                1,
                3,
                ("canonicalize", "dce", "inline-opt"),
            ),
        (
            "ipsccp",
            (
                "int id(int x){ return x; } "
                "int wrap(int x){ return id(x); } "
                "int f(void){ return wrap(7); }"
            ),
            'call i32 @"wrap"',
            0,
            1,
            ("canonicalize", "dce", "inline-opt"),
        ),
        (
            "loop-instsimplify",
            "int f(int n){ int sum=0; for(int i=0;i<n;i++){ sum += i - 0; } return sum; }",
            " sub ",
            0,
            1,
            ("loop-opt", "canonicalize"),
        ),
        (
            "gvn",
            "int f(int x,int y){ int a=x+y; int b=x+y; return a==b; }",
            " add ",
            1,
            2,
            (
                "ssa-gvn",
                "ssa-gvn-rewrite",
                "gvn",
                "local-value-numbering",
                "copy-propagation",
            ),
        ),
        (
            "early-cse",
            "int f(int x,int y){ int a=x+y; int b=x+y; return a==b; }",
            " add ",
            1,
            2,
            ("local-value-numbering", "copy-propagation"),
        ),
        (
            "sccp",
            "int f(void){ int x = 4 - 4; if (x) return 7; return 9; }",
            " br i1 ",
            0,
            1,
            ("ssa-sccp", "ssa-sccp-rewrite", "ssa-branch-prune", "canonicalize", "dce"),
        ),
    ],
)
def test_registered_disable_alias_can_turn_off_selected_python_translations(
    monkeypatch, alias, code, needle, default_count, disabled_count, metric_names
):
    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert _ir_needle_count(default_artifact["ir_text"], needle) == default_count

    monkeypatch.setenv("PCC_DISABLE_PASSES", alias)
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert _ir_needle_count(disabled_artifact["ir_text"], needle) == disabled_count
    for metric_name in metric_names:
        metric = disabled_artifact["pass_report"]["passes"][metric_name]
        assert metric["skips"] >= 1


def test_sccp_alias_controls_ssa_branch_prune_on_join_proven_constant(monkeypatch):
    code = """
    int f(int c) {
        int x = 0;
        int y = 0;
        if (c) {
            x = 1;
        } else {
            x = 1;
        }
        if (x) {
            y = 7;
            y = y + 1;
        } else {
            y = 9;
            y = y + 1;
        }
        return y;
    }
    """

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert default_artifact["ir_text"].count(" br i1 ") <= 1
    assert default_artifact["pass_report"]["stats"]["ssa_branch_prune.fold_true"] == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "sccp")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert disabled_artifact["ir_text"].count(" br i1 ") == 1
    assert "ssa_branch_prune.fold_true" not in disabled_artifact["pass_report"]["stats"]
    assert disabled_artifact["pass_report"]["passes"]["ssa-sccp"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["ssa-sccp-rewrite"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["ssa-branch-prune"]["skips"] >= 1


def test_sccp_alias_controls_ssa_sccp_rewrite_on_join_constant_return(monkeypatch):
    code = """
    int f(int c) {
        int x = 0;
        if (c) {
            x = 7;
        } else {
            x = 7;
        }
        return x;
    }
    """

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    # Direct SSA lowering can now return the merged constant even without the
    # AST rewrite, so the alias-control check must key off pass stats, not
    # a specific final IR spelling.
    assert default_artifact["pass_report"]["stats"]["ssa_sccp_rewrite.rewrite_return"] == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "sccp")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "ssa_sccp_rewrite.rewrite_return" not in disabled_artifact["pass_report"]["stats"]
    assert disabled_artifact["pass_report"]["passes"]["ssa-sccp"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["ssa-sccp-rewrite"]["skips"] >= 1


def test_gvn_alias_controls_ssa_gvn_rewrite_on_cross_block_return(monkeypatch):
    code = """
    int f(int a, int b, int flag) {
        int x = a + b;
        if (flag) {
            return a + b;
        }
        return x;
    }
    """

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    # Direct SSA lowering now bypasses the source rewrite when emitting the
    # final IR for this shape, so the stable signal here is that the rewrite
    # pass ran and recorded work.
    assert default_artifact["pass_report"]["stats"]["ssa_gvn_rewrite.rewrite_return"] >= 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "gvn")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "ssa_gvn_rewrite.rewrite_return" not in disabled_artifact["pass_report"]["stats"]
    assert disabled_artifact["pass_report"]["passes"]["ssa-gvn"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["ssa-gvn-rewrite"]["skips"] >= 1


def test_adce_alias_controls_ssa_adce_on_dead_initializer_before_overwrite(
    monkeypatch,
):
    code = """
    int f(int a) {
        int x = a + 1;
        x = 0;
        return x;
    }
    """

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert default_artifact["pass_report"]["stats"]["ssa_adce.drop_init"] == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "adce")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "ssa_adce.drop_init" not in disabled_artifact["pass_report"]["stats"]
    assert disabled_artifact["pass_report"]["passes"]["ssa-adce"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["dce"]["skips"] >= 1


def test_dse_alias_controls_ssa_dse_on_dead_effectful_assignment(monkeypatch):
    code = """
    int side_effect(void);
    int f(int a) {
        int x;
        x = side_effect();
        x = a + 2;
        return x;
    }
    """

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert default_artifact["pass_report"]["stats"]["ssa_dse.preserve_effect_assign"] == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "dse")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "ssa_dse.preserve_effect_assign" not in disabled_artifact["pass_report"]["stats"]
    assert disabled_artifact["pass_report"]["passes"]["ssa-dse"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["ssa-adce"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["dce"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["memory-opt-ir"]["skips"] >= 1


def test_registered_disable_alias_can_turn_off_argpromotion_boundary_analysis(
    monkeypatch,
):
    code = (
        "int leaf(int x){ return x + 1; } "
        "int wrap(int x){ return leaf(x); } "
        "int f(void){ struct S { int a; int b; }; struct S s; "
        "s.a=7; s.b=8; int y=s.a; return wrap(y); }"
    )

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert default_artifact["pass_report"]["stats"]["sroa.candidates"] == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "argpromotion")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "sroa.candidates" not in disabled_artifact["pass_report"]["stats"]
    assert disabled_artifact["pass_report"]["passes"]["inline-opt"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["sroa"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["alloc-decision"]["skips"] >= 1


def test_registered_disable_alias_can_turn_off_deadargelim_boundary_analysis(
    monkeypatch,
):
    code = (
        "int callee(int x){ return x; } "
        "int wrapper(int live, int dead){ return callee(live); } "
        "int f(void){ return wrapper(7, 9); }"
    )

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert default_artifact["pass_report"]["stats"]["deadargelim.dead_params"] == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "deadargelim")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "deadargelim.dead_params" not in disabled_artifact["pass_report"]["stats"]
    assert (
        disabled_artifact["pass_report"]["passes"]["deadargelim-analysis"]["skips"] >= 1
    )
    assert disabled_artifact["pass_report"]["passes"]["inline-opt"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["dce"]["skips"] >= 1


def test_registered_disable_alias_can_turn_off_elim_avail_extern_boundary(
    monkeypatch,
):
    code = "extern int helper(void); int main(void){ return 0; }"

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert default_artifact["pass_report"]["stats"]["elim_avail_extern.removed"] == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "elim-avail-extern")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "elim_avail_extern.removed" not in disabled_artifact["pass_report"]["stats"]
    assert (
        disabled_artifact["pass_report"]["passes"]["elim-avail-extern-src"]["skips"] >= 1
    )


@pytest.mark.parametrize(
    ("alias", "code"),
    [
        (
            "simplifycfg",
            "int f(int c,int a,int b){ if (c) return a; return b; }",
        ),
        (
            "loop-simplifycfg",
            (
                "int f(int x){ int sum=0; "
                "for(int i=0;i<4;i++){ if (x) { if (x) sum += i; } } "
                "return sum; }"
            ),
        ),
    ],
)
def test_registered_disable_alias_can_turn_off_cfg_cleanup_translations(
    monkeypatch, alias, code
):
    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert default_artifact["pass_report"]["passes"]["control-flow"]["runs"] >= 1
    assert default_artifact["pass_report"]["passes"]["dce"]["runs"] >= 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", alias)
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert disabled_artifact["pass_report"]["passes"]["control-flow"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["dce"]["skips"] >= 1


@pytest.mark.parametrize(
    ("alias", "code", "disabled_needles", "metric_names"),
    [
        (
            "correlated-propagation",
            "int f(int x){ if (x) { if (x) return 1; } return 0; }",
            ("ret i32 0", "ret i32 1"),
            ("control-flow", "canonicalize", "dce"),
        ),
        (
            "jump-threading",
            "int f(int x){ if (x) return 1; if (x) return 2; return 0; }",
            ("ret i32 0", "ret i32 1", "ret i32 2"),
            ("control-flow",),
        ),
        (
            "constraint-elimination",
            "int f(int x){ if (x > 3) { if (x > 1) return 1; } return 0; }",
            ("ret i32 0", "ret i32 1"),
            ("control-flow", "canonicalize"),
        ),
    ],
)
def test_registered_disable_alias_can_turn_off_branch_refinement_translations(
    monkeypatch, alias, code, disabled_needles, metric_names
):
    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.setenv("PCC_DISABLE_PASSES", alias)
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    for needle in disabled_needles:
        assert _ir_needle_count(default_artifact["ir_text"], needle) == 0
        assert _ir_needle_count(disabled_artifact["ir_text"], needle) >= 1
    for metric_name in metric_names:
        assert disabled_artifact["pass_report"]["passes"][metric_name]["skips"] >= 1


def test_registered_disable_alias_can_turn_off_speculation_friendly_translation(
    monkeypatch,
):
    code = "int f(int c, int x){ if (c) return x + 0; return 0; }"

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert default_artifact["ir_text"].count(" phi ") == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "speculative-execution")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert disabled_artifact["ir_text"].count(" phi ") == 0
    assert disabled_artifact["pass_report"]["passes"]["control-flow"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["canonicalize"]["skips"] >= 1


def test_registered_disable_alias_can_turn_off_analysis_only_loop_idiom_translation(
    monkeypatch,
):
    code = "void fill(int *p, int n){ for (int i=0; i<n; ++i) { p[i] = 0; } }"

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert default_artifact["pass_report"]["stats"]["loop_opt.memset_idiom_candidates"] == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "loop-idiom")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "loop_opt.memset_idiom_candidates" not in disabled_artifact["pass_report"]["stats"]
    assert disabled_artifact["pass_report"]["passes"]["loop-opt"]["skips"] >= 1


@pytest.mark.parametrize(
    ("alias", "code", "stat_name"),
    [
        (
            "memcpyopt",
            (
                "void *memcpy(void*, const void*, unsigned long); "
                "void copy(char *dst, char *src){ memcpy(dst, src, 4); }"
            ),
            "memory_opt.memcpy_like_calls",
        ),
        (
            "mldst-motion",
            "int f(int *p){ int x=*p; int y=*p; return x+y; }",
            "memory_opt.load_load_elim",
        ),
        (
            "loop-load-elim",
            (
                "int f(int *p){ "
                "for(int i=0;i<3;i++){ int x=p[0]; int y=p[0]; p[0]=x+y; } "
                "return p[0]; }"
            ),
            "memory_opt.load_load_elim",
        ),
    ],
)
def test_registered_disable_alias_can_turn_off_analysis_only_memory_translations(
    monkeypatch, alias, code, stat_name
):
    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert default_artifact["pass_report"]["stats"][stat_name] >= 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", alias)
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert stat_name not in disabled_artifact["pass_report"]["stats"]
    metric = disabled_artifact["pass_report"]["passes"]["memory-opt-ir"]
    assert metric["skips"] >= 1


def test_mldst_motion_alias_controls_within_block_load_reuse(monkeypatch):
    code = "int f(int *p){ int x=*p; int y=*p; return x+y; }"

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert default_artifact["ir_text"].count(" load ") == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "mldst-motion")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert disabled_artifact["ir_text"].count(" load ") >= 2
    assert disabled_artifact["pass_report"]["passes"]["memory-opt-ir"]["skips"] >= 1


def test_mldst_motion_alias_controls_bitcast_exact_slot_reuse(monkeypatch):
    code = "int f(int a){ int x = 0; void *p = &x; *(int*)p = a; return x; }"

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert default_artifact["pass_report"]["stats"]["memory_opt.store_load_forward"] >= 3
    assert default_artifact["ir_text"].count(" load ") < 5

    monkeypatch.setenv("PCC_DISABLE_PASSES", "mldst-motion")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert disabled_artifact["ir_text"].count(" load ") >= 5
    assert "memory_opt.store_load_forward" not in disabled_artifact["pass_report"]["stats"]
    assert disabled_artifact["pass_report"]["passes"]["memory-opt-ir"]["skips"] >= 1


def test_mldst_motion_alias_controls_zero_gep_exact_slot_reuse(monkeypatch):
    code = "int f(int a){ int x[1]; x[0] = a; return x[0]; }"

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert default_artifact["pass_report"]["stats"]["memory_opt.store_load_forward"] >= 2
    assert default_artifact["ir_text"].count(" load ") == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "mldst-motion")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert disabled_artifact["ir_text"].count(" load ") >= 3
    assert "memory_opt.store_load_forward" not in disabled_artifact["pass_report"]["stats"]
    assert disabled_artifact["pass_report"]["passes"]["memory-opt-ir"]["skips"] >= 1


def test_loop_load_elim_alias_controls_within_block_loop_reload_reuse(monkeypatch):
    code = (
        "int f(int *p){ "
        "for(int i=0;i<3;i++){ int x=p[0]; int y=p[0]; p[0]=x+y; } "
        "return p[0]; }"
    )

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)
    assert default_artifact["ir_text"].count(" load ") == 8

    monkeypatch.setenv("PCC_DISABLE_PASSES", "loop-load-elim")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert disabled_artifact["ir_text"].count(" load ") >= 10
    assert disabled_artifact["pass_report"]["passes"]["memory-opt-ir"]["skips"] >= 1


def test_registered_disable_alias_can_turn_off_analysis_only_sroa_translation(
    monkeypatch,
):
    code = (
        "struct S { int x; int y; }; "
        "int f(void){ struct S s; s.x=1; s.y=2; return s.x+s.y; }"
    )
    pipeline = _pipeline_without_high_passes("alloc-decision")

    default_artifact = _compile_preprocessed_translation_unit_artifact(
        "probe.c", code, pass_pipeline=pipeline
    )

    assert default_artifact["pass_report"]["stats"]["sroa.candidates"] == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "sroa")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact(
        "probe.c",
        code,
        pass_pipeline=_pipeline_without_high_passes("alloc-decision"),
    )

    assert "sroa.candidates" not in disabled_artifact["pass_report"]["stats"]
    metric = disabled_artifact["pass_report"]["passes"]["sroa"]
    assert metric["skips"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
@pytest.mark.parametrize(
    ("alias", "code", "needle", "default_count", "disabled_count", "llvm_count"),
    [
        (
            "aggressive-instcombine",
            "int f(int x){ return (x + 0) + (2 + 3); }",
            " add ",
            1,
            3,
            3,
        ),
        (
            "instcombine",
            "int f(int x){ return (x + 0) + (2 + 3); }",
            " add ",
            1,
            3,
            1,
        ),
        (
            "adce",
            "int f(int a){ int x = a + 1; return a; }",
            " add ",
            0,
            1,
            0,
        ),
        (
            "bdce",
            "int f(int a){ int x = a + 1; return a; }",
            " add ",
            0,
            1,
            0,
        ),
        (
            "argpromotion",
            (
                "int leaf(int x){ return x + 1; } "
                "int wrap(int x){ return leaf(x); } "
                "int f(void){ struct S { int a; int b; }; struct S s; "
                "s.a=7; s.b=8; int y=s.a; return wrap(y); }"
            ),
            'call i32 @"wrap"',
            0,
            1,
            1,
        ),
        (
            "called-value-propagation",
            (
                "int id(int x){ return x; } "
                "int wrap(int x){ return id(x); } "
                "int f(void){ int y=7; int z=y; return wrap(z); }"
            ),
            'call i32 @"wrap"',
            0,
            1,
            1,
        ),
        (
            "callsite-splitting",
            (
                "int id(int x){ return x; } "
                "int wrap(int x){ return id(x); } "
                "int f(int c,int x){ if (c) return wrap(x); return wrap(x + 1); }"
            ),
            'call i32 @"wrap"',
            0,
            2,
            2,
        ),
        (
            "early-cse",
            "int f(int x,int y){ int a=x+y; int b=x+y; return a==b; }",
            " add ",
            1,
            2,
            1,
        ),
            (
                "globalopt",
                (
                    "static int id(int x){ return x; } "
                    "static int wrap(int x){ return id(x); } "
                    "int f(void){ int y=7; int z=y; return wrap(z); }"
                ),
                "alloca",
                1,
                3,
                3,
            ),
        (
            "ipsccp",
            (
                "int id(int x){ return x; } "
                "int wrap(int x){ return id(x); } "
                "int f(void){ return wrap(7); }"
            ),
            'call i32 @"wrap"',
            0,
            1,
            1,
        ),
        (
            "loop-instsimplify",
            "int f(int n){ int sum=0; for(int i=0;i<n;i++){ sum += i - 0; } return sum; }",
            " sub ",
            0,
            1,
            0,
        ),
        (
            "sccp",
            "int f(void){ int x = 4 - 4; if (x) return 7; return 9; }",
            " br i1 ",
            0,
            1,
            0,
        ),
    ],
)
def test_selected_python_translations_match_external_llvm_reference_on_focused_case(
    monkeypatch, alias, code, needle, default_count, disabled_count, llvm_count
):
    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.setenv("PCC_DISABLE_PASSES", alias)
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", alias)
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 2, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    assert _ir_needle_count(default_artifact["ir_text"], needle) == default_count
    assert _ir_needle_count(disabled_artifact["ir_text"], needle) == disabled_count
    assert _ir_needle_count(llvm_ir, needle) == llvm_count
    metric = llvm_ctx.pass_report()["passes"][alias]
    assert metric["runs"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
def test_dse_translation_tracks_external_reference_with_mem2reg_bridge(monkeypatch):
    code = """
    int f(int a) {
        int x;
        x = 1;
        x = a;
        return x;
    }
    """

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.setenv("PCC_DISABLE_PASSES", "dse")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", "mem2reg,dse")
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 2, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    # After the source-level DSE boundary runs, the dead `x = 1;` store is
    # gone, but the remaining live local still lowers through the AST path
    # and keeps one stack store alongside the incoming-parameter spill.
    assert default_artifact["ir_text"].count("store ") == 2
    assert disabled_artifact["ir_text"].count("store ") == 3
    assert llvm_ir.count("store ") == 0
    assert llvm_ctx.pass_report()["passes"]["mem2reg"]["runs"] >= 1
    assert llvm_ctx.pass_report()["passes"]["dse"]["runs"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
def test_deadargelim_analysis_boundary_tracks_external_llvm_reference(monkeypatch):
    code = (
        "int callee(int x){ return x; } "
        "int wrapper(int live, int dead){ return callee(live); } "
        "int f(void){ return wrapper(7, 9); }"
    )

    monkeypatch.setenv("PCC_DISABLE_PASSES", "deadargelim")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", "deadargelim")
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 2, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    assert llvm_ir
    metric = llvm_ctx.pass_report()["passes"]["deadargelim"]
    assert metric["runs"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
def test_elim_avail_extern_boundary_tracks_external_llvm_reference(monkeypatch):
    code = "extern int helper(void); int main(void){ return 0; }"

    monkeypatch.setenv("PCC_DISABLE_PASSES", "elim-avail-extern")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", "elim-avail-extern")
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 2, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    assert llvm_ir
    metric = llvm_ctx.pass_report()["passes"]["elim-avail-extern"]
    assert metric["runs"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
@pytest.mark.parametrize("alias", ["gvn", "newgvn"])
def test_gvn_family_tracks_external_reference_on_focused_case(monkeypatch, alias):
    code = "int f(int x,int y){ int a=x+y; int b=x+y; return a==b; }"

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.setenv("PCC_DISABLE_PASSES", alias)
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", alias)
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 2, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    assert default_artifact["ir_text"].count(" add ") == 1
    assert disabled_artifact["ir_text"].count(" add ") == 2
    assert llvm_ir.count(" add ") <= 1
    assert disabled_artifact["pass_report"]["passes"]["ssa-gvn"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["ssa-gvn-rewrite"]["skips"] >= 1
    assert disabled_artifact["pass_report"]["passes"]["gvn"]["skips"] >= 1
    assert (
        disabled_artifact["pass_report"]["passes"]["local-value-numbering"]["skips"] >= 1
    )
    assert disabled_artifact["pass_report"]["passes"]["copy-propagation"]["skips"] >= 1
    metric = llvm_ctx.pass_report()["passes"][alias]
    assert metric["runs"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
@pytest.mark.parametrize(
    ("alias", "code"),
    [
        (
            "simplifycfg",
            "int f(int c,int a,int b){ if (c) return a; return b; }",
        ),
        (
            "loop-simplifycfg",
            (
                "int f(int x){ int sum=0; "
                "for(int i=0;i<4;i++){ if (x) { if (x) sum += i; } } "
                "return sum; }"
            ),
        ),
    ],
)
def test_cfg_cleanup_translations_track_external_llvm_reference(
    monkeypatch, alias, code
):
    monkeypatch.setenv("PCC_DISABLE_PASSES", alias)
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", alias)
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 2, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    assert llvm_ir
    metric = llvm_ctx.pass_report()["passes"][alias]
    assert metric["runs"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
@pytest.mark.parametrize(
    ("alias", "code"),
    [
        (
            "correlated-propagation",
            "int f(int x){ if (x) { if (x) return 1; } return 0; }",
        ),
        (
            "jump-threading",
            "int f(int x){ if (x) return 1; if (x) return 2; return 0; }",
        ),
        (
            "constraint-elimination",
            "int f(int x){ if (x > 3) { if (x > 1) return 1; } return 0; }",
        ),
    ],
)
def test_branch_refinement_translations_track_external_llvm_reference(
    monkeypatch, alias, code
):
    monkeypatch.setenv("PCC_DISABLE_PASSES", alias)
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", alias)
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 2, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    assert llvm_ir
    metric = llvm_ctx.pass_report()["passes"][alias]
    assert metric["runs"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
def test_speculation_friendly_translation_tracks_external_llvm_reference(monkeypatch):
    code = "int f(int c, int x){ if (c) return x + 0; return 0; }"

    monkeypatch.setenv("PCC_DISABLE_PASSES", "speculative-execution")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", "speculative-execution")
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 2, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    assert llvm_ir
    metric = llvm_ctx.pass_report()["passes"]["speculative-execution"]
    assert metric["runs"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
def test_loop_idiom_analysis_boundary_tracks_external_llvm_reference(monkeypatch):
    code = "void fill(int *p, int n){ for (int i=0; i<n; ++i) { p[i] = 0; } }"

    monkeypatch.setenv("PCC_DISABLE_PASSES", "loop-idiom")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", "loop-idiom")
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 2, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    assert llvm_ir
    metric = llvm_ctx.pass_report()["passes"]["loop-idiom"]
    assert metric["runs"] >= 1


def test_registered_disable_alias_can_turn_off_analysis_only_loop_sink_translation(
    monkeypatch,
):
    code = (
        "int f(int *p, int cond){ int sum=0; "
        "for (int i=0; i<4; ++i) { int t = *p; if (cond) sum += t; } "
        "return sum; }"
    )

    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert default_artifact["pass_report"]["stats"]["loop_opt.sink_candidates"] == 1

    monkeypatch.setenv("PCC_DISABLE_PASSES", "loop-sink")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    assert "loop_opt.sink_candidates" not in disabled_artifact["pass_report"]["stats"]
    assert disabled_artifact["pass_report"]["passes"]["loop-opt"]["skips"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
def test_loop_sink_analysis_boundary_tracks_external_llvm_reference(monkeypatch):
    code = (
        "int f(int *p, int cond){ int sum=0; "
        "for (int i=0; i<4; ++i) { int t = *p; if (cond) sum += t; } "
        "return sum; }"
    )

    monkeypatch.setenv("PCC_DISABLE_PASSES", "loop-sink")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", "loop-sink")
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 2, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    assert llvm_ir
    metric = llvm_ctx.pass_report()["passes"]["loop-sink"]
    assert metric["runs"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
@pytest.mark.parametrize(
    ("alias", "code", "stat_name"),
    [
        (
            "memcpyopt",
            (
                "void *memcpy(void*, const void*, unsigned long); "
                "void copy(char *dst, char *src){ memcpy(dst, src, 4); }"
            ),
            "memory_opt.memcpy_like_calls",
        ),
        (
            "mldst-motion",
            "int f(int *p){ int x=*p; int y=*p; return x+y; }",
            "memory_opt.load_load_elim",
        ),
        (
            "loop-load-elim",
            (
                "int f(int *p){ "
                "for(int i=0;i<3;i++){ int x=p[0]; int y=p[0]; p[0]=x+y; } "
                "return p[0]; }"
            ),
            "memory_opt.load_load_elim",
        ),
    ],
)
def test_analysis_only_memory_translations_track_external_llvm_reference(
    monkeypatch, alias, code, stat_name
):
    default_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.setenv("PCC_DISABLE_PASSES", alias)
    disabled_artifact = _compile_preprocessed_translation_unit_artifact("probe.c", code)

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", alias)
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 2, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    assert default_artifact["pass_report"]["stats"][stat_name] >= 1
    assert stat_name not in disabled_artifact["pass_report"]["stats"]
    assert llvm_ir
    metric = llvm_ctx.pass_report()["passes"][alias]
    assert metric["runs"] >= 1


@pytest.mark.skipif(find_opt_binary() is None, reason="matching llvm opt not installed")
def test_sroa_analysis_boundary_tracks_external_reference(monkeypatch):
    code = (
        "struct S { int x; int y; }; "
        "int f(void){ struct S s; s.x=1; s.y=2; return s.x+s.y; }"
    )
    pipeline = _pipeline_without_high_passes("alloc-decision")

    default_artifact = _compile_preprocessed_translation_unit_artifact(
        "probe.c", code, pass_pipeline=pipeline
    )

    monkeypatch.setenv("PCC_DISABLE_PASSES", "sroa")
    disabled_artifact = _compile_preprocessed_translation_unit_artifact(
        "probe.c",
        code,
        pass_pipeline=_pipeline_without_high_passes("alloc-decision"),
    )

    monkeypatch.delenv("PCC_DISABLE_PASSES", raising=False)
    monkeypatch.setenv("PCC_LLVM_PIPELINE", "sroa")
    llvm_ctx = PassContext()
    llvm_ir, status = _apply_external_llvm_pipeline_to_text(
        disabled_artifact["ir_text"], 0, pass_ctx=llvm_ctx
    )

    assert status == "external-text-pipeline"
    assert default_artifact["pass_report"]["stats"]["sroa.candidates"] == 1
    assert "sroa.candidates" not in disabled_artifact["pass_report"]["stats"]
    assert disabled_artifact["pass_report"]["passes"]["sroa"]["skips"] >= 1
    assert disabled_artifact["ir_text"].count("alloca") >= 1
    assert llvm_ir.count("alloca") < disabled_artifact["ir_text"].count("alloca")
    metric = llvm_ctx.pass_report()["passes"]["sroa"]
    assert metric["runs"] >= 1


def test_function_attrs_stay_conservative_for_leaf_loops():
    code = "int spin(void){ while (1) {} }"

    artifact = _compile_preprocessed_translation_unit_artifact("spin.c", code)
    ir_text = artifact["ir_text"]

    assert "nounwind" in ir_text
    assert "nofree" in ir_text
    assert "willreturn" not in ir_text
