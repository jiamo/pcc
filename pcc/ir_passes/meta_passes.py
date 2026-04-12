"""IR meta / barrier passes.

These passes do not rewrite IR structure. They model LLVM pipeline
control points such as verification, analysis invalidation, and
remark-only boundaries.
"""

from __future__ import annotations

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class VerifyIRPass(ModulePass):
    """IR verifier pass.

    Mirrors the effect of LLVM's ``verify`` utility pass: validate the
    current module and preserve analyses when nothing changed.
    """

    name = "pcc-verify"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        module.verify()
        return PreservedAnalyses.all()


class InvalidateIRPass(ModulePass):
    """Analysis invalidation barrier.

    Does not mutate IR, but conservatively invalidates all cached
    analyses to match the purpose of the upstream invalidation barrier.
    """

    name = "pcc-invalidate"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.none()


class RequireIRPass(ModulePass):
    """Requirement / barrier pass.

    This pass is structural no-op in pcc's IR runtime. It preserves all
    analyses and exists to keep the explicit pass surface aligned with
    LLVM textual pipelines.
    """

    name = "pcc-require"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class AnnotationRemarksIRPass(ModulePass):
    """Remark-only pass boundary.

    Upstream ``annotation-remarks`` surfaces optimization remarks
    without changing IR. We model that as a no-op IR pass.
    """

    name = "pcc-annotation-remarks"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class EEInstrumentIRPass(ModulePass):
    """Execution-engine instrumentation boundary."""

    name = "pcc-ee-instrument"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class OpenMPOptIRPass(ModulePass):
    """OpenMP optimization inventory boundary."""

    name = "pcc-openmp-opt"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class OpenMPCGSCCIRPass(ModulePass):
    """OpenMP CGSCC inventory boundary."""

    name = "pcc-openmp-opt-cgscc"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class CGProfileIRPass(ModulePass):
    """Call-graph profiling summary boundary."""

    name = "pcc-cg-profile"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class RelLookupTableConverterIRPass(ModulePass):
    """Relative lookup-table inventory boundary."""

    name = "pcc-rel-lookup-table-converter"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class CoroBoundaryIRPass(ModulePass):
    """Coroutine inventory / boundary pass."""

    name = "pcc-coro-boundary"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class TransformWarningIRPass(ModulePass):
    """Transformation-warning remarks boundary."""

    name = "pcc-transform-warning"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class Annotation2MetadataIRPass(ModulePass):
    """Annotation-to-metadata boundary.

    Upstream ``annotation2metadata`` (``llvm/lib/Transforms/IPO/
    Annotation2Metadata.cpp``) lifts entries of
    ``@llvm.global.annotations`` onto the referenced functions as
    ``!annotation`` metadata. The subset here preserves IR structure
    and analyses, deferring real metadata attachment to future work.
    """

    name = "pcc-annotation2metadata"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class ForceAttrsIRPass(ModulePass):
    """Force-function-attributes boundary.

    Upstream ``forceattrs`` (``llvm/lib/Transforms/IPO/ForceFunctionAttrs.cpp``)
    applies attributes from a command-line list to matching functions.
    In the default pipeline this is a no-op, so the IR-level subset
    models the same behavior.
    """

    name = "pcc-forceattrs"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class InjectTLIMappingsIRPass(ModulePass):
    """Inject target-library mappings boundary.

    Upstream ``inject-tli-mappings`` seeds ``vector-function-abi-variant``
    metadata for recognized libm calls. pcc models this as an analysis
    boundary that does not mutate IR.
    """

    name = "pcc-inject-tli-mappings"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


class RecomputeGlobalsAAIRPass(ModulePass):
    """Recompute globals-AA boundary.

    Upstream ``recompute-globalsaa`` forces a fresh ``GlobalsAA``
    computation. pcc does not cache a globals alias analysis across
    this boundary, so invalidating all analyses reproduces the effect.
    """

    name = "pcc-recompute-globalsaa"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.none()


class AggressiveInstCombineIRPass(ModulePass):
    """Aggressive-instcombine peephole boundary.

    Upstream ``aggressive-instcombine`` runs an expanded peephole set on
    top of ``InstCombine``. The subset here is an IR-structure-preserving
    boundary: it defers to the regular ``InstCombine`` for the narrow
    pattern set pcc currently recognizes.
    """

    name = "pcc-aggressive-instcombine"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


# AlignmentFromAssumptionsIRPass was a hollow marker here; the real
# narrow transform now lives in
# :mod:`pcc.ir_passes.alignment_from_assumptions`.


class ChrIRPass(ModulePass):
    """Control-flow-hoist / CHR boundary.

    Upstream ``chr`` (Control Height Reduction) biases profiled hot
    branches. pcc currently does not consume block-profile metadata
    inside its IR pipeline, so the subset is an analysis-preserving
    boundary.
    """

    name = "pcc-chr"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


# Float2IntIRPass was a hollow marker here; the real narrow round-trip
# identity transform lives in :mod:`pcc.ir_passes.float2int`.


class LoopIdiomIRPass(ModulePass):
    """Loop-idiom recognition boundary.

    Upstream ``loop-idiom`` recognizes loops that implement
    ``memset``/``memcpy``/``popcount`` and rewrites them as intrinsic
    calls. The subset here is an analysis-preserving boundary covering
    the cases where no such idiom is present.
    """

    name = "pcc-loop-idiom"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


# MemCpyOptIRPass was a hollow marker here; the real narrow transform
# lives in :mod:`pcc.ir_passes.memcpyopt`.


class MoveAutoInitIRPass(ModulePass):
    """Move-auto-init boundary.

    Upstream ``move-auto-init`` sinks compiler-inserted
    ``memset(alloca, 0, ...)`` calls closer to their first use. The
    subset here is an analysis-preserving boundary: pcc does not emit
    auto-init stores by default.
    """

    name = "pcc-move-auto-init"

    def run(self, module: llvm.ModuleRef, am: AnalysisManager) -> PreservedAnalyses:
        return PreservedAnalyses.all()


# SpeculativeExecutionIRPass was previously a hollow marker here. It
# has been replaced with a real narrow hoist transform implemented in
# :mod:`pcc.ir_passes.speculative_execution`.
