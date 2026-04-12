"""Phase 8 vectorization passes — migration-scaffold batch.

Upstream vectorization is a major subsystem (~15k lines in
LLVM-20); a full port is out of scope for the current sprint. We
land framework-compatible scaffolds so downstream pipelines can
reference these pass names stably while the real implementations
are built incrementally.

Upstream source anchors are on each class.
"""

from __future__ import annotations

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


from .vector_combine import VectorCombinePass  # noqa: E402,F401


from .loop_vectorize import LoopVectorizePass  # noqa: E402,F401


from .slp_vectorizer import SLPVectorizerPass  # noqa: E402,F401


from .late_scalar import LateScalarPass  # noqa: E402,F401
