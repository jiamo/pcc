"""LLVM-IR-level pass framework.

This is the pass runtime for the migration away from source-level
approximations described in
``docs/plans/all-pass-llvm-ir-1to1-master-plan.md``.

Upstream reference (the canonical source-of-truth for every pass and
analysis in this package is the LLVM-20.1.8 tree):

- pass manager / preserved analyses:
  /tmp/llvm-src/llvm-20.1.8.src/include/llvm/IR/PassManager.h
- analysis manager:
  /tmp/llvm-src/llvm-20.1.8.src/include/llvm/IR/PassManagerInternal.h
- dominator tree:
  /tmp/llvm-src/llvm-20.1.8.src/lib/IR/Dominators.cpp
- loop info:
  /tmp/llvm-src/llvm-20.1.8.src/lib/Analysis/LoopInfo.cpp

``pcc`` does not re-implement LLVM's pass manager template stack. It
uses a small Python runtime that walks llvmlite-parsed modules and
functions, supports an analysis cache, and runs passes in a configured
order. Each pass declares the analyses it requires and the analyses it
preserves; invalidation is edge-triggered when a pass reports
transformations.
"""

from __future__ import annotations

from .manager import (
    AnalysisKey,
    AnalysisManager,
    FunctionPass,
    IRPassManager,
    LoopPass,
    ModulePass,
    PreservedAnalyses,
)

# Phase 2 analyses
from .alias_analysis import AliasAnalysis, AliasAnalysisResult, AliasResult
from .constant_lattice import LatticeValue, evaluate_binary, evaluate_compare, meet
from .dominator_tree import (
    CFG,
    DominatorTree,
    DominatorTreeResult,
    PostDominatorTreeResult,
    compute_dominator_tree,
    compute_post_dominator_tree,
)
from .loop_info import Loop, LoopInfo, LoopInfoResult, compute_loop_info
from .memory_ssa import MemoryAccess, MemorySSAForm, MemorySSAResult, build_memory_ssa
from .ssa_utils import DefUseIndex, DefUseResult, build_def_use_index

# Phase 3-8 passes (real + scaffolds)
from .adce import ADCEPass
from .bdce import BDCEPass
from .correlated_propagation import CorrelatedValuePropagationPass
from .constraint_elimination import ConstraintEliminationPass
from .dce import DCEPass
from .dse import DSEPass
from .early_cse import EarlyCSEPass
from .elim_avail_extern import ElimAvailExternPass
from .gvn import GVNPass
from .infer_alignment import InferAlignmentPass
from .instcombine import InstCombinePass
from .instsimplify import InstSimplifyPass
from .ipo_passes import (
    AlwaysInlinePass,
    ArgPromotionPass,
    CallSiteSplittingPass,
    CalledValuePropagationPass,
    DeadArgElimPass,
    FunctionAttrsPass,
    GlobalDCEPass,
    GlobalOptPass,
    IPSCCPPass,
    InlinePass,
)
from .jump_threading import JumpThreadingPass
from .libcalls_shrinkwrap import LibcallsShrinkwrapPass
from .lower_constant_intrinsics import LowerConstantIntrinsicsPass
from .lower_expect import LowerExpectPass
from .loop_load_elim import LoopLoadElimPass
from .loop_instsimplify import LoopInstSimplifyPass
from .loop_passes import (
    IndVarSimplifyPass,
    LICMPass,
    LoopDeletionPass,
    LoopDistributePass,
    LoopSinkPass,
    LoopRotatePass,
    LoopSimplifyPass,
    LoopSimplifyCFGPass,
    LoopUnrollPass,
    SimpleLoopUnswitchPass,
)
from .mem2reg import Mem2RegPass
from .meta_passes import (
    AggressiveInstCombineIRPass,
    Annotation2MetadataIRPass,
    AnnotationRemarksIRPass,
    CGProfileIRPass,
    ChrIRPass,
    CoroBoundaryIRPass,
    EEInstrumentIRPass,
    ForceAttrsIRPass,
    InjectTLIMappingsIRPass,
    InvalidateIRPass,
    LoopIdiomIRPass,
    MoveAutoInitIRPass,
    OpenMPCGSCCIRPass,
    OpenMPOptIRPass,
    RecomputeGlobalsAAIRPass,
    RelLookupTableConverterIRPass,
    RequireIRPass,
    TransformWarningIRPass,
    VerifyIRPass,
)
from .alignment_from_assumptions import AlignmentFromAssumptionsIRPass
from .float2int import Float2IntIRPass
from .memcpyopt import MemCpyOptIRPass
from .speculative_execution import SpeculativeExecutionIRPass
from .mldst_motion import MergedLoadStoreMotionPass
from .newgvn import NewGVNPass
from .reassociate import ReassociatePass
from .sccp import SCCPPass
from .simplifycfg import SimplifyCFGPass
from .sroa import SROAPass
from .tailcallelim import TailCallElimPass
from .trivial_simplify import TrivialArithIdentitiesPass
from .vectorize_passes import (
    LateScalarPass,
    LoopVectorizePass,
    SLPVectorizerPass,
    VectorCombinePass,
)

__all__ = [
    # Core framework
    "AnalysisKey",
    "AnalysisManager",
    "FunctionPass",
    "IRPassManager",
    "LoopPass",
    "ModulePass",
    "PreservedAnalyses",
    # Phase 2 analyses
    "AliasAnalysis",
    "AliasAnalysisResult",
    "AliasResult",
    "CFG",
    "DefUseIndex",
    "DefUseResult",
    "DominatorTree",
    "DominatorTreeResult",
    "LatticeValue",
    "Loop",
    "LoopInfo",
    "LoopInfoResult",
    "MemoryAccess",
    "MemorySSAForm",
    "MemorySSAResult",
    "PostDominatorTreeResult",
    "build_def_use_index",
    "build_memory_ssa",
    "compute_dominator_tree",
    "compute_loop_info",
    "compute_post_dominator_tree",
    "evaluate_binary",
    "evaluate_compare",
    "meet",
    # Phase 3 local/canonical scalar passes
    "InstSimplifyPass",
    "SimplifyCFGPass",
    "DCEPass",
    "BDCEPass",
    "InstCombinePass",
    "InferAlignmentPass",
    "TrivialArithIdentitiesPass",
    # Phase 4 sparse-propagation + dead-code
    "DSEPass",
    "JumpThreadingPass",
    "CorrelatedValuePropagationPass",
    "ConstraintEliminationPass",
    "ReassociatePass",
    "SCCPPass",
    "ADCEPass",
    "LibcallsShrinkwrapPass",
    "LowerConstantIntrinsicsPass",
    "LowerExpectPass",
    # Phase 5 value numbering + memory
    "EarlyCSEPass",
    "ElimAvailExternPass",
    "GVNPass",
    "NewGVNPass",
    "Mem2RegPass",
    "AggressiveInstCombineIRPass",
    "AlignmentFromAssumptionsIRPass",
    "Annotation2MetadataIRPass",
    "AnnotationRemarksIRPass",
    "CGProfileIRPass",
    "ChrIRPass",
    "CoroBoundaryIRPass",
    "EEInstrumentIRPass",
    "Float2IntIRPass",
    "ForceAttrsIRPass",
    "InjectTLIMappingsIRPass",
    "InvalidateIRPass",
    "LoopIdiomIRPass",
    "MemCpyOptIRPass",
    "MoveAutoInitIRPass",
    "OpenMPCGSCCIRPass",
    "OpenMPOptIRPass",
    "RecomputeGlobalsAAIRPass",
    "RelLookupTableConverterIRPass",
    "RequireIRPass",
    "SpeculativeExecutionIRPass",
    "TransformWarningIRPass",
    "VerifyIRPass",
    "SROAPass",
    "TailCallElimPass",
    "MergedLoadStoreMotionPass",
    "LoopLoadElimPass",
    "LoopInstSimplifyPass",
    # Phase 6 loop
    "LoopSimplifyPass",
    "LoopRotatePass",
    "LoopSinkPass",
    "LICMPass",
    "IndVarSimplifyPass",
    "LoopDeletionPass",
    "LoopSimplifyCFGPass",
    "SimpleLoopUnswitchPass",
    "LoopUnrollPass",
    "LoopDistributePass",
    # Phase 7 IPO / CGSCC
    "InlinePass",
    "AlwaysInlinePass",
    "GlobalOptPass",
    "GlobalDCEPass",
    "ArgPromotionPass",
    "DeadArgElimPass",
    "IPSCCPPass",
    "FunctionAttrsPass",
    "CalledValuePropagationPass",
    "CallSiteSplittingPass",
    # Phase 8 vectorize + late scalar
    "VectorCombinePass",
    "LoopVectorizePass",
    "SLPVectorizerPass",
    "LateScalarPass",
]
