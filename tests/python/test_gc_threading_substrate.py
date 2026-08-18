"""Facade for the gc-threading-substrate suite.

The original single-module file exceeded 16k lines and was split
into themed ``gcsubstrate_*`` modules plus ``_gc_substrate_common``.
This facade star-imports every piece so existing pytest node ids
(tests/python/test_gc_threading_substrate.py::test_...) and the
external importers below keep working unchanged.
"""
from _gc_substrate_common import *  # noqa: F401,F403
# Underscore names are skipped by star-import; re-export explicitly
# for ``from test_gc_threading_substrate import _compile_runtime_probe``.
from _gc_substrate_common import (  # noqa: F401
    REPO_ROOT,
    _build_threaded_runtime,
    _compile_runtime_probe,
    _runtime_variant,
)
from gcsubstrate_a_threading_no_park import *  # noqa: F401,F403
from gcsubstrate_b_root_store_scheduler_queue import *  # noqa: F401,F403
from gcsubstrate_c_safepoints_selectors_cext_claims import *  # noqa: F401,F403
from gcsubstrate_d_store_ptr_capi_pins import *  # noqa: F401,F403
from gcsubstrate_e_iterators_tuple_dict_set_reads import *  # noqa: F401,F403
from gcsubstrate_f_backend4_growth_publication import *  # noqa: F401,F403
from gcsubstrate_g_cext_traverse_remembered import *  # noqa: F401,F403
from gcsubstrate_h_tracing_stw_threads import *  # noqa: F401,F403
from gcsubstrate_i_container_commit_contracts import *  # noqa: F401,F403
from gcsubstrate_j_concurrent_tracer_races import *  # noqa: F401,F403
from gcsubstrate_k_collect_during_containers import *  # noqa: F401,F403
