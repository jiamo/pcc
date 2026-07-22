# Evidence: external bad-smell audit triage + layout-contract lock (2026-07-23)

## Input

`../bad_smell.md` (external whole-repo smell audit). Per repo discipline every
claim was re-verified against source before acting; several were wrong or
misread deliberate design.

## Verdicts

Claim 1 (hardcoded struct offsets in pcc-Python ports, mirror-drift risk):
CONFIRMED and highest-value. 140+ `ptr_add`/`load_i64` hand-offset sites
across `pcc/py_runtime/py/*.py`. **Landed this slice:**
`tests/python/test_runtime_layout_contract.py` — compiles a C probe against
`py_internal.h` and asserts offsetof/sizeof for all 12 mirrored structs
(34 offsets + 3 sizes, incl. PyClassObject 96/104/112/120). Passing (1
passed). C-side drift now fails loudly with instructions to fix the ports
together with the table.

Claim 1b (5 GC backends in one file): TRUE but known/deliberate direction;
covered by existing G-track structure, no new action.

Claim 2a (numpy special cases in py_capi_shim.c): WRONG in character — the
`numpy` mentions are motivational comments on generic C-API symbols; zero
`strcmp`/name-keyed branches found. This matches, not violates, the B-track
rule (fix generic mechanism, label the motivating consumer).

Claim 2b (self-module "privilege branches" in codegen): SPLIT VERDICT. The
`pcc.parse.py_lift` sites in `call_expression_lowering.py` are
`PCC_DEBUG_BOOTSTRAP_TRACE`-gated debug probes (env-gated, no semantics; the
env var is documented in docs/debugging-playbook.md across 8 files) — smell is
module-name hardcoding, not privilege. The `class_gen.py` sites
(PY_AST_FIELD_NAME_OVERRIDES, L1_CODEGEN_HOST_ATTRS) ARE semantic name-keyed
special cases. Both tracked in AUD-P2-SELF-MODULE-SPECIAL-CASES-IN-CODEGEN.

Claim 3 (god files / mixin stack): TRUE as description; layer1 facade split
already landed and further splits are existing direction; no new row.

Claim 4 (gpu_gc/dist/tilelang are CPU oracles, metal probe "fake"):
MISREAD — this is documented fail-closed oracle design (AGENTS.md GPU claim
levels; `metal_adapter.py`'s never-exists probe exists precisely to avoid a
false "device present" claim). No action.

Claim 5a (py_list.c TODO raise placeholders): CONFIRMED, 6 sites. Tracked as
BUG-P1-PY-LIST-TODO-RAISE-PLACEHOLDERS (runtime-semantics change: C + port
mirror + 5-GC + bootstrap gates required, not a drive-by edit).

Claim 5b (CMS queue/store buffer caps cause data loss): UNPROVEN — push
returns 0 on full ring and callers handle it; no loss demonstrated. No action
without a failing repro.

Claim 5c (swallowed `except Exception: return None` in codegen helpers):
plausible but behavior-changing to fix; folded into the P2 hygiene rows
rather than edited blind.

Claim 6 (cli_core/cli_bootstrap duplication): CONFIRMED (~11 duplicated defs
each side) but naive dedup would mutate the stage1 bootstrap closure; tracked
as AUD-P2-CLI-SHARED-HELPER-DUPLICATION with closure-safety requirements.

## Gates run

- `uv run pytest -q -n0 tests/python/test_runtime_layout_contract.py` → 1 passed.
