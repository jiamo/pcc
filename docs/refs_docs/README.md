# refs_docs

Local snapshot of every URL referenced in `pcc_multi_year_roadmap.md`,
fetched 2026-05-05.

| File | Source |
|---|---|
| `cpython_gc.c` | https://raw.githubusercontent.com/python/cpython/main/Python/gc.c |
| `cpython_gc_doc.html` | https://docs.python.org/3/library/gc.html |
| `cpython_obmalloc.c` | https://raw.githubusercontent.com/python/cpython/main/Objects/obmalloc.c |
| `github_modular_stdlib.html` | https://github.com/modular/modular/tree/main/mojo/stdlib |
| `github_ocaml_runtime.html` | https://github.com/ocaml/ocaml/tree/trunk/runtime |
| `go_gc_guide.html` | https://go.dev/doc/gc-guide |
| `go_mbarrier.go.html` | https://go.dev/src/runtime/mbarrier.go |
| `go_mgc.go.html` | https://go.dev/src/runtime/mgc.go |
| `lua_lgc.c` | https://raw.githubusercontent.com/lua/lua/master/lgc.c |
| `lua_manual_5.4.html` | https://www.lua.org/manual/5.4/manual.html |
| `mojo_ownership.html` | https://docs.modular.com/mojo/manual/values/ownership/ |
| `mojo_roadmap.html` | https://docs.modular.com/mojo/roadmap/ |
| `mojo_traits.html` | https://docs.modular.com/mojo/manual/traits/ |
| `ocaml_effects.html` | https://ocaml.org/manual/5.4/effects.html |
| `ocaml_garbage_collector.html` | https://ocaml.org/docs/garbage-collector |
| `ocaml_parallelism.html` | https://ocaml.org/manual/5.4/parallelism.html |
| `openjdk_jep439_zgc.html` | https://openjdk.org/jeps/439 |
| `pcc_README.md` | repo `README.md` @ c902fc2f (self-ref) |
| `pcc_bench_pcc1.py` | repo `bench/bench_pcc1.py` @ c902fc2f |
| `pcc_c_codegen.py` | repo `pcc/codegen/c_codegen.py` @ c902fc2f |
| `pcc_c_evaluator.py` | repo `pcc/evaluater/c_evaluator.py` @ c902fc2f |
| `pcc_gc-semantics-gap.md` | repo `docs/issues/gc-semantics-gap.md` @ c902fc2f |
| `pcc_pipeline.py` | repo `pcc/py_frontend/pipeline.py` @ c902fc2f |

The pcc self-references at commit `c902fc2f` were extracted via
`git show c902fc2f:<path>` because the public mirror at
`github.com/ikshengmin/pcc` is unreachable.

To refresh, re-run `bash /tmp/dl_refs.sh` (saved in scratch) and the
`git show` lines for the pcc self-refs.


## `gc-research/` — full GC algorithm source snapshots

The five GC algorithm reference implementations that pcc's runtime
backends mirror, organized one subdirectory per language/algorithm.
Previously lived under `/tmp/gc-research/` (ephemeral, lost on host
reboot).  Now in tree.

See `gc-research/README.md` for the backend ↔ subdir mapping:

| pcc backend | Algorithm | Subdir |
|---|---|---|
| #0 | refcount + STW cycle           | `gc-research/python/`       |
| #1 | incremental tricolor mark-sweep | `gc-research/lua/`         |
| #2 | concurrent mark-sweep          | `gc-research/go-greentea/`  |
| #3 | generational                   | `gc-research/ocaml/`        |
| #4 | colored relocating             | `gc-research/zgc/`          |

Each `Phase G*` xfail marker in `tests/test_gc_*.py` cross-links to
the matching reference file under `gc-research/`; when an xfail flips
to `xpassed` (`X` in pytest output), the corresponding reference no
longer needs to drive a port.


## `value-model/` — value object source snapshots

Reference implementations for identity-free/value-object work that is not part
of the GC backend mapping.

| Topic | Subdir |
|---|---|
| OpenJDK Project Valhalla value classes / flattened objects | `value-model/valhalla/` |

Use this when working on the pcc Python value model plan, especially
`ValueClassType`, `ValuePayload`, boxing/object projection, flattened fields,
flat arrays, and identity-sensitive operation diagnostics.
