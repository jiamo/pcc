"""Structural guards against repeating native-runtime builds in pytest.

The runtime probes intentionally compile real C and pcc-Python archives.  The
guard is about build ownership: a configuration is built once per pytest
process/fixture, probe sources remain per-test, and no test deletes shared
repository build products to force a rebuild.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
TESTS = ROOT / "tests"
THIS_FILE = Path(__file__).absolute()

_RUNTIME_COPY_BUILD_STRATEGIES = {
    # These are real build variants, not copies of the default archives:
    # per-strategy refcount macros, optional TSan instrumentation, and the
    # runtime-tripwire macro. Each is cached at the narrowest safe module/file
    # scope while preserving its distinct compile contract.
    "tests/python/test_gc_concurrent_collection.py": "@cache_runtime_build",
    "tests/python/test_gc_refcount_strategies.py": "@cache_runtime_build",
    "tests/python/test_runtime_tripwires.py": 'fixture(scope="module")',
}

_STATELESS_GC_COMPILE_SUITES = (
    "tests/python/test_gc_api.py",
    "tests/python/test_gc_effectiveness.py",
    "tests/python/test_gc_finalizer_corner.py",
    "tests/python/test_gc_performance.py",
    "tests/python/test_gc_regression_bugs.py",
    "tests/python/test_gc_resurrection.py",
    "tests/python/test_gc_trashcan.py",
)

_HEAVY_EXTERNAL_CORPUS_SUITES = (
    "tests/c/test_c_testsuite.py",
    "tests/c/test_c_testsuite_self.py",
    "tests/c/test_gcc_torture_execute.py",
    "tests/c/test_gcc_torture_self.py",
)

_FULL_BOOTSTRAP_INTEGRATION_GATES = tuple(
    f"tests/python/gc/test_pcc_bootstrap_full_gc{backend}.py" for backend in range(5)
)

_RUNTIME_SOURCE_COPY_SUITES = (
    *_RUNTIME_COPY_BUILD_STRATEGIES,
    "tests/python/test_runtime_oracle_diff.py",
    "tests/runtime_build_cache.py",
)

_NESTED_PYTEST_STRATEGIES = {
    "tests/python/test_gc_backend_under_env.py": "One inner pytest owns the complete frontend/GC slice",
    "tests/test_gc_bootstrap_xdist_group.py": "test_full_gc_bootstraps_remain_independently_schedulable",
}


def _python_test_sources():
    for path in TESTS.rglob("*.py"):
        if path.absolute() != THIS_FILE:
            yield path, path.read_text(encoding="utf-8")


def test_all_copy_and_force_build_tests_have_an_audited_reuse_strategy():
    discovered = {
        path.relative_to(ROOT).as_posix()
        for path, source in _python_test_sources()
        if "copytree(" in source and '"-B"' in source
    }
    assert discovered == set(_RUNTIME_COPY_BUILD_STRATEGIES)
    for relative, marker in _RUNTIME_COPY_BUILD_STRATEGIES.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert marker in source, f"{relative} lost audited build strategy {marker!r}"


def test_nested_pytest_invocations_are_finite_and_audited():
    # File-level matching intentionally covers both an inline argv and the
    # common ``cmd = [...]`` followed by ``subprocess.run(cmd)`` form.
    discovered = {
        path.relative_to(ROOT).as_posix()
        for path, source in _python_test_sources()
        if "subprocess.run(" in source
        and ('"pytest",' in source or "'pytest'," in source)
    }

    assert discovered == set(_NESTED_PYTEST_STRATEGIES)
    for relative, marker in _NESTED_PYTEST_STRATEGIES.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert marker in source, f"{relative} lost audited strategy {marker!r}"


def test_l1_codegen_static_method_table_matches_host_contract():
    import inspect

    from pcc.py_frontend.codegen._l1_codegen_static_methods import (
        L1_CODEGEN_STATIC_METHODS,
    )
    from pcc.py_frontend.codegen.host_contract import L1_CODEGEN_HOST_METHODS
    from pcc.py_frontend.codegen.layer1 import L1CodeGen

    generated_names = tuple(entry["name"] for entry in L1_CODEGEN_STATIC_METHODS)
    assert generated_names == L1_CODEGEN_HOST_METHODS
    assert len(generated_names) == len(set(generated_names))

    expected = []
    for method_name in L1_CODEGEN_HOST_METHODS:
        signature = inspect.signature(getattr(L1CodeGen, method_name))
        call_sig = []
        param_types = []
        kw_only_emitted = False
        for param in signature.parameters.values():
            kind = "pos"
            if param.kind is inspect.Parameter.VAR_POSITIONAL:
                kind = "*args"
            elif param.kind is inspect.Parameter.VAR_KEYWORD:
                kind = "**kwargs"
            elif param.kind is inspect.Parameter.KEYWORD_ONLY:
                if not kw_only_emitted:
                    call_sig.append(
                        {
                            "name": "",
                            "kind": "kw_only",
                            "annotation": ("dyn",),
                            "default": None,
                            "has_default": False,
                        }
                    )
                    kw_only_emitted = True
            call_sig.append(
                {
                    "name": param.name,
                    "kind": kind,
                    "annotation": ("dyn",),
                    "default": None,
                    "has_default": param.default is not inspect.Parameter.empty,
                }
            )
            param_types.append(("dyn",))
        expected.append(
            {
                "name": method_name,
                "kind": "instance",
                "return_ty": ("dyn",),
                "param_types": tuple(param_types),
                "call_sig": tuple(call_sig),
                "box_int_abi": False,
            }
        )

    assert L1_CODEGEN_STATIC_METHODS == tuple(expected)


def test_runtime_source_copies_exclude_repository_build_products():
    required_ignores = ('"_native"', '"__pycache__"', '"build_*"', '"*.a.target"')
    violations = []
    for relative in _RUNTIME_SOURCE_COPY_SUITES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        missing = [token for token in required_ignores if token not in source]
        if missing:
            violations.append(f"{relative}: missing {', '.join(missing)}")
    assert violations == []


def test_runtime_oracle_cache_key_tracks_fake_libc_headers():
    source = (ROOT / "tests/python/test_runtime_oracle_diff.py").read_text(
        encoding="utf-8"
    )
    assert 'REPO_ROOT / "utils" / "fake_libc_include"' in source


def test_tests_do_not_delete_shared_runtime_archives_or_skip_under_xdist():
    forbidden = (
        "_wipe_repo_runtime_archive",
        "mutate libpy_runtime",
        'rmtree(RUNTIME / "build"',
    )
    violations = []
    for path, source in _python_test_sources():
        for token in forbidden:
            if token in source:
                violations.append(f"{path.relative_to(ROOT)}: {token}")
        if "PYTEST_XDIST_WORKER" in source and "pytest.skip(" in source:
            violations.append(f"{path.relative_to(ROOT)}: worker-specific pytest skip")
    assert violations == []


def test_tests_do_not_build_or_link_mutable_repository_c_runtime_archive():
    """Native probes must consume the immutable content-addressed fixture."""

    forbidden = (
        "pcc/py_runtime/" + "libpy_runtime.a",
        'RUNTIME_DIR / "' + 'libpy_runtime.a"',
        '["make", "-C", "pcc/py_runtime", "' + 'libpy_runtime.a"]',
        '["make", "-C", str(RUNTIME), "' + 'libpy_runtime.a"]',
        'str(RUNTIME / "' + 'libpy_runtime.a")',
    )
    violations = []
    for path, source in _python_test_sources():
        if path == ROOT / "tests/runtime_build_cache.py":
            continue
        for token in forbidden:
            if token in source:
                violations.append(f"{path.relative_to(ROOT)}: {token}")
    assert violations == []

    fixtures = (ROOT / "tests/python/conftest.py").read_text(encoding="utf-8")
    cache = (ROOT / "tests/runtime_build_cache.py").read_text(encoding="utf-8")
    assert "def c_runtime_archive" in fixtures
    assert "def cached_c_runtime" in cache
    assert "def cached_threaded_c_runtime" in cache
    assert "def cached_pcc_python_runtime" in cache
    assert cache.count('f"PCC_WITH_THREADS={1 if threaded else 0}"') == 2
    assert '_PCC_RUNTIME_CACHE_MARKER_SCHEMA = "pcc.runtime-build-cache.v4"' in cache
    assert '_C_RUNTIME_CACHE_KEY_SCHEMA = "pcc.c-runtime-build-cache.v2"' in cache
    assert "_runtime_archive_stale" not in fixtures
    assert "_PCC_PY_RUNTIME_ARCHIVE" not in fixtures
    assert "fcntl.flock" in cache
    assert "os.replace(work_runtime, runtime)" in cache


def test_default_xdist_keeps_bounded_workers_for_compiler_heavy_suite():
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "-n 6 --dist=loadgroup -m 'not integration'" in config
    conftest = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert "def pytest_xdist_auto_num_workers" not in conftest
    assert (
        'os.environ.setdefault("PCC_OUTER_PARALLELISM", str(worker_count))' in conftest
    )
    assert "items[:] = warmup + remaining" in conftest
    matrix = (ROOT / "tests/python/test_gc_backend_under_env.py").read_text(
        encoding="utf-8"
    )
    assert 'name=f"pcc_heavy_{frontend_backend}"' in matrix


def test_boc_speedup_proofs_share_one_measured_performance_lane():
    from tests.python import test_boc_benchmarks as ring
    from tests.python import test_boc_threading_proof as bank

    assert ring.MIN_RING_SPEEDUP == 1.5
    assert bank.MIN_SPEEDUP == 2.5
    assert ring.pytestmark.mark.kwargs["name"] == "pcc_heavy_llvm"
    assert bank.pytestmark.mark.kwargs["name"] == "pcc_heavy_llvm"
    assert callable(ring.test_boc_ring_correctness)
    assert callable(bank.test_pcc_threads_complete_all_workers)

    ring_gate = next(
        mark
        for mark in ring.test_boc_ring_correctness_and_speedup.pytestmark
        if mark.name == "pcc_gate"
    )
    bank_gate = next(
        mark
        for mark in bank.test_pcc_threads_give_real_parallel_speedup.pytestmark
        if mark.name == "pcc_gate"
    )
    assert ring_gate.kwargs["env"] == "PCC_RUN_BOC_SPEEDUP"
    assert bank_gate.kwargs["env"] == "PCC_RUN_BOC_SPEEDUP"


def test_lock_owned_self_host_warmer_has_independent_inner_budget(monkeypatch):
    from tests.python import test_self_host_oracle_diff as oracle

    monkeypatch.setenv("PCC_OUTER_PARALLELISM", "6")
    monkeypatch.delenv("PCC_PY_FRONTEND_JOBS", raising=False)
    assert oracle._child_env()["PCC_PY_FRONTEND_JOBS"] == "4"

    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "3")
    assert oracle._child_env()["PCC_PY_FRONTEND_JOBS"] == "3"


def test_gc_meta_matrix_retains_required_modes_without_accidental_duplicates():
    from tests.python import test_gc_backend_under_env as matrix
    from tests.python import test_runtime_oracle_diff as runtime_oracle
    from tests.python import test_self_host_oracle_diff as self_host_oracle

    for target in matrix._FRONTEND_INDEPENDENT_TARGETS:
        assert matrix._self_frontend_target(target) is None
    assert (
        matrix._self_frontend_target(matrix._CONCURRENT_COLLECTION_FILE)
        == matrix._CONCURRENT_COLLECTION_PCC_PYTHON_TARGET
    )
    assert len(matrix._self_frontend_target(matrix._GENERATIONAL_TARGETS)) < len(
        matrix._GENERATIONAL_TARGETS
    )
    assert len(matrix._self_frontend_target(matrix._RELOCATING_TARGETS)) < len(
        matrix._RELOCATING_TARGETS
    )

    cases = {
        (frontend, gc_backend): set(matrix._target_args(targets))
        for parameter in matrix._iter_cases()
        for frontend, gc_backend, targets in (parameter.values,)
    }
    heavy_groups = {
        (frontend, gc_backend): next(
            mark.kwargs["name"]
            for mark in parameter.marks
            if mark.name == "xdist_group"
        )
        for parameter in matrix._iter_cases()
        for frontend, gc_backend, _targets in (parameter.values,)
    }
    gc4_contract = "tests/python/test_gc_backend4_production.py"
    assert gc4_contract in cases[("llvm", "4")]
    assert gc4_contract in cases[("self", "4")]
    assert set(heavy_groups.values()) == {
        "pcc_heavy_llvm",
        "pcc_heavy_self",
    }
    assert all(
        group == f"pcc_heavy_{frontend}"
        for (frontend, _gc_backend), group in heavy_groups.items()
    )
    assert runtime_oracle.pytestmark.mark.kwargs["name"] == "pcc_heavy_self"
    assert self_host_oracle.pytestmark.mark.kwargs["name"] == "pcc_heavy_self"


def test_stateless_gc_compile_suites_are_not_forced_onto_one_worker():
    for relative in _STATELESS_GC_COMPILE_SUITES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "xdist_group" not in source, relative


def test_csmith_seeds_remain_independently_schedulable():
    source = (ROOT / "tests/c/test_csmith.py").read_text(encoding="utf-8")
    assert "xdist_group" not in source
    # flat per-seed parametrize over the vetted corpus — one xdist item per
    # seed, never grouped (corpus rationale lives in test_csmith.py)
    assert (
        '@pytest.mark.parametrize("seed", CSMITH_SEED_CORPUS[:DEFAULT_SEEDS])'
        in source
    )


def test_heavy_external_c_corpora_are_in_the_integration_gate():
    for relative in _HEAVY_EXTERNAL_CORPUS_SUITES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "pytestmark = pytest.mark.integration" in source, relative

    promotion = (ROOT / "scripts/run_self_backend_promotion_gate.py").read_text(
        encoding="utf-8"
    )
    for relative in (
        "tests/c/test_c_testsuite_self.py",
        "tests/c/test_gcc_torture_self.py",
    ):
        start = promotion.index(relative) - 80
        assert '"-m", "integration"' in promotion[start : start + 160]


def test_full_three_stage_gc_bootstraps_are_in_the_integration_gate():
    for relative in _FULL_BOOTSTRAP_INTEGRATION_GATES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "pytestmark = pytest.mark.integration" in source, relative

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    commands = [
        line
        for line in agents.splitlines()
        if "test_pcc_bootstrap_full_gc" in line and "pytest" in line
    ]
    assert commands
    assert all("-m integration" in line for line in commands)


def test_full_runtime_c_source_emit_gate_is_integration_only():
    relative = "tests/python/test_py_runtime_pcc_emit.py"
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert "pytest.mark.integration" in source
    assert "pcc_runtime_emit" in source

    board = (ROOT / "docs/goal/task-board.yaml").read_text(encoding="utf-8")
    commands = [
        line
        for line in board.splitlines()
        if "test_py_runtime_pcc_emit.py" in line and "pytest" in line
    ]
    assert commands
    assert all("-m integration" in line for line in commands)


def test_gc_root_graph_fixture_is_not_rebuilt_by_every_xdist_worker():
    source = (
        ROOT / "tests/python/gc_production_contract/test_root_graphs.py"
    ).read_text(encoding="utf-8")
    assert 'xdist_group(name="gc_root_graphs")' in source


def test_fallback_closure_fixture_is_not_rebuilt_by_every_xdist_worker():
    source = (ROOT / "tests/python/test_fallback_baseline.py").read_text(
        encoding="utf-8"
    )
    assert 'xdist_group(name="fallback_baseline")' in source


def test_pcc1_threaded_runtime_fixture_is_not_rebuilt_by_every_xdist_worker():
    source = (ROOT / "tests/python/test_pcc1_threading_gc_runtime.py").read_text(
        encoding="utf-8"
    )
    assert 'xdist_group(name="pcc1_threaded_gc")' in source


def test_self_host_oracle_stages_are_shared_across_xdist_workers():
    source = (ROOT / "tests/python/test_self_host_oracle_diff.py").read_text(
        encoding="utf-8"
    )
    cache = (ROOT / "tests/runtime_build_cache.py").read_text(encoding="utf-8")
    conftest = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert "cached_self_host_oracle_dir()" in source
    assert "self_host_source_key()" in source
    assert "self_backend_object_cache_key()" in source
    assert "PCC_PY_FRONTEND_IR_CACHE_IDENTITY" in source
    assert "PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY" in source
    assert "fcntl.flock" in source
    assert "os.replace(temporary, pcc1)" in source
    assert "os.replace(temporary, pcc2)" in source
    assert "os.replace(temporary, pcc3)" in source
    assert "pcc.self-host-test-artifact.v1" in cache
    assert "self-host-oracle" in cache
    assert "test_000_self_host_oracle_stage_cache_warmup" in conftest


def test_uv_locked_wheel_bundles_a_current_source_pcc1():
    source = (ROOT / "tests/integration/test_uv_locked_pcc_sync.py").read_text(
        encoding="utf-8"
    )

    assert "find_current_pcc1(REPO)" in source
    assert 'REPO / "build" / "bootstrap" / "pcc1"' not in source
