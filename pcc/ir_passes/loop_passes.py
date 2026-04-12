"""Phase 6 loop passes.

Each pass below corresponds to an LLVM loop pass. Some are now real
IR-level subset implementations; the rest still preserve the
framework contract until the remaining analyses land.

Upstream source anchors are on each class.
"""

from __future__ import annotations

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


from .loop_simplify import LoopSimplifyPass  # noqa: E402,F401


from .loop_instsimplify import LoopInstSimplifyPass  # noqa: E402,F401


from .loop_simplifycfg import LoopSimplifyCFGPass  # noqa: E402,F401


from .loop_rotate import LoopRotatePass  # noqa: E402,F401


from .loop_sink import LoopSinkPass  # noqa: E402,F401


from .licm import LICMPass  # noqa: E402,F401


from .indvars import IndVarSimplifyPass  # noqa: E402,F401


# Re-exported from :mod:`loop_deletion` — see that module for the real
# implementation (subset). Kept here so existing imports continue to
# work transparently after the scaffold upgrade.
from .loop_deletion import LoopDeletionPass  # noqa: E402,F401


from .simple_loop_unswitch import SimpleLoopUnswitchPass  # noqa: E402,F401


from .loop_unroll import LoopUnrollPass  # noqa: E402,F401


from .loop_distribute import LoopDistributePass  # noqa: E402,F401
