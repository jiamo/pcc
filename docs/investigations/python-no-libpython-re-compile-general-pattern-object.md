# Investigation: no-libpython `re.compile` generalization needs a runtime compiled-pattern object

## Status
active

## Problem Description
`re.compile` is the single largest remaining no-libpython fallback hotspot on the
real NumPy first-import path: `@.cpy.attr.compile=71` (per the 2026-05-28
count-only diagnostic recorded in `docs/current-goal-state.md`; after that, the
two clean `os.path` gaps — `expanduser`, `realpath` — were closed 2026-05-29).
Reducing it advances `B-P0-PKG` (real package/import; NumPy-import fallback-surface
shrinkage). The `os.path` additive helpers are now exhausted; `re.compile` is the
next frontier but, unlike the `os.path` helpers, is NOT a clean additive slice.

## Repro
Count-only pure-Python diagnostic (per `current-goal-state.md`) compiling the real
`projects/numpy-2.4.4` import with `--python-libpython=auto`, dumping IR, and
counting `@.cpy.attr.compile,` occurrences -> 71. Each is a `re.compile(...)` call
(or a use of its result) that the current frontend lowering cannot keep native, so
it emits a CPython fallback.

## Test [N/A]
No new gate yet — this file characterizes the work. Implementation must add focused
IR-dispatch tests (`@py_re_*` present, no `cpy.fn.compile`) plus a differential
runtime test vs CPython, and re-run the B-P0-PKG gate set.

## Analysis (verified 2026-05-29)
Runtime regex layer (`pcc/py_runtime/src/py_re.c` + `pcc/py_runtime/py/py_re.py`):
- Engine present: `py_re_match[_flags]`, `py_re_search[_flags]`,
  `py_re_findall_flags`, `_re_match_impl`.
- The compiled form is a PER-METHOD BOUND CLOSURE, not a general object:
  `py_re_compile_method(pattern, flags, method_kind)` builds a 3-tuple
  `(pattern, flags, method_kind)`; `py_re_bound_method_call(captures, args)`
  dispatches it. `method_kind` is baked in at COMPILE time (1=search, 2=findall,
  else match). No `sub`/`split`/`finditer` bound-method support.
- VERIFIED no general compiled-pattern object exists: grep for
  `PY_TYPE_REGEX` / `PyRegexObject` / `PY_TYPE_PATTERN` / `pattern_object` in
  `py_runtime.h`, `py_internal.h`, `py_re.c` -> empty.

Frontend lowering (`pcc/py_frontend/codegen/native_text_modules.py`) is ALIAS-based:
`_native_re_compile_alias_info` only accepts `re.compile(StrLit, flags)` (literal
pattern); `_native_re_compile_alias_for_name` + `_native_re_compile_alias_uses_are_safe`
track which variable holds which pattern and require the method to be statically
known at the use site; `_native_re_class_compile_attr_string_value` adds class-level
literal-pattern storage for flags=0 used only as `re.split`/`findall` args.

Therefore the 71 fallbacks are, by construction, the cases the alias model cannot
reach:
1. Non-literal patterns (`re.compile(var)`, f-strings, concatenations).
2. The compiled result escaping compile-time alias tracking (passed to a function,
   stored in a list/dict/instance attribute, returned).
3. Methods beyond the baked-in match/search/findall (`sub`, `subn`, `split`,
   `finditer`, `fullmatch`; `.pattern`/`.flags` attribute reads).
4. A single compiled pattern used with MULTIPLE methods (the per-method closure
   bakes exactly one `method_kind`).

### Frontend type-integration (verified 2026-05-29)
The crux of dispatching `p.match(s)` on a compiled value `p` is that the
type-inferencer must type `p` as a regex. Verified state:
- The type vocabulary is in `pcc/py_frontend/py_ast.py`: `IntType`, `FloatType`,
  `StrType`, `BytesType`, `ListType`, `DictType`, `TupleType`, `FuncType`,
  `ClassType`, `ValueClassType`, etc. There is NO `RegexType` and NO generic
  opaque-native-object type to reuse (grep for `Opaque/Native/Object/Unknown` Type
  -> empty).
- `pcc/py_frontend/type_infer.py` has NO `re.compile`/regex handling (grep ->
  empty), consistent with the alias model living entirely in the codegen mixin.
So the general-object path is a full vertical slice, not a runtime-only add:
(a) new `RegexType` in `py_ast.py`; (b) `type_infer.py` typing `re.compile(...)`
as `RegexType` (and propagating it through assignment, params if cross-fn is
attempted, returns); (c) codegen dispatch of `.match/.search/.findall/.sub/.split/
.finditer/.fullmatch` and `.pattern/.flags` on a `RegexType` value to the runtime
object; plus (d) the runtime compiled-pattern object + engine glue below. The
ESCAPING case (#2 — compiled value passed to a function / stored in a container or
attribute) additionally needs cross-function/container `RegexType` flow, which is
the hardest part; a first cut may cover only same-scope non-escaping patterns
(#1 + #3 + #4) and leave escaping to a follow-up.

### Critical constraint / regression trap (verified by design analysis 2026-05-29)
This is the reason the general object is NOT an additive change. The native
compiled-pattern value (a 2-tuple `(pattern, flags)` or a tagged object) is NOT a
CPython `re.Pattern`. Today every `re.compile` use works because it falls back to
CPython end-to-end. If `type_infer` types `re.compile(...)` as `RegexType`
UNCONDITIONALLY and that value then escapes to code still going through the CPython
fallback (passed to a cpy-dispatched call, stored where a cpy consumer reads it,
returned across a cpy boundary), the consumer receives the native value instead of a
real `re.Pattern` and BREAKS — a SILENT REGRESSION of currently-passing cases.
Therefore the general object MUST COEXIST with the existing safety analysis: lower to
the native object + native dispatch ONLY when every use is native-safe (non-escaping,
known methods); otherwise keep the CPython fallback unchanged. The general object
WIDENS what counts as native-safe (compile-once into a real value -> a multi-method or
non-literal pattern can stay native) but does NOT remove the native-vs-fallback
decision. Validation MUST include a case where a compiled pattern escapes to a
fallback boundary, proving it still uses CPython and still matches. This regression
trap is why a naive "type re.compile as RegexType" patch is wrong, and why the slice
needs full-suite validation rather than the focused-gate pattern used for the additive
os.path slices.

## Proposals
- No.1 General runtime compiled-pattern object (`PyRegex`) + runtime method dispatch + generic frontend lowering   [pending]

## No.1 General runtime compiled-pattern object
### Code Change (design; not yet implemented)
1. Runtime object: add a compiled-pattern object carrying `pattern` (PyStr) +
   `flags` (i64) in BOTH tiers (`py_re.c` + `py_re.py`). Mirror discipline — see
   the os.path `realpath` C/py-mirror lesson in `current-goal-state.md`
   (2026-05-29): the high=py default archive links the `.py` mirror, so any new
   runtime symbol must exist in both `.c` and `.py`.
2. Runtime method dispatch: `py_re_pattern_match/search/findall/sub/split/
   finditer/fullmatch(self, text, ...)` reading pattern+flags from the object and
   calling the existing engine (`_re_match_impl`, `py_re_findall_flags`, plus new
   `sub`/`split`); plus `.pattern`/`.flags` attribute reads.
3. Frontend: lower `re.compile(<any str expr>, <flags>)` -> a `PyRegex` ctor call
   (drop the StrLit-only + alias-safe-use restriction); lower attribute/method
   access on a value typed as a PyRegex to the runtime dispatchers; keep the
   existing fast alias path as a specialization when the pattern/method are
   statically known (must not regress).
4. ABI (`runtime_abi.py`) + dispatch wiring; focused IR-dispatch tests + functional
   differential vs CPython (match/search/findall/sub/split, flags) + MANDATORY
   self-host bootstrap + fallback ratchet + real-NumPy boundary.
### pending
Not yet implemented — a focused multi-iteration effort (new runtime object type +
engine glue for `sub`/`split` + frontend type-flow for PyRegex values), deliberately
NOT crammed into the long 2026-05-29 session that closed the clean `os.path` slices.
Risk: touches shared frontend codegen + the regex runtime in both tiers; requires
the full B-P0-PKG gate set. The conservative existing alias lowering remains the
fast path.

## Update — numpy re.compile distribution (data-driven reframing, 2026-05-29)
The `@.cpy.attr.compile=71` RAW count overstates the `import numpy`-critical need.
Counting `re.compile` sites in the numpy 2.4.4 source (excl `tests/`/`testing/`) by
subpackage: **f2py=92, distutils=59, _core=41, _build_utils=6, lib=4, linalg=3,
_utils=3**. The bulk (f2py 92 + distutils 59 = 151) is in BUILD subpackages that
`import numpy` does NOT load (`numpy/__init__.py` has no eager distutils/f2py import
— verified empty grep). Of `_core`'s 41, most are build-time code
(`_core/code_generators/genapi.py`, `_core/src/highway/docs/`,
`_core/src/common/pythoncapi-compat/`); the actual runtime core has ~3
(`_core/_internal.py`: `format_re`/`sep_re`/`space_re` — MODULE-LEVEL literal
patterns for dtype-string parsing).

Reframing: the IMPORT-CRITICAL `re.compile` surface is small (~10–15, dominated by
module-level literal patterns used with match/search), NOT 71. The full general
`PyRegex` object (No.1) remains the correct end-state for f2py/distutils/dynamic
patterns, but a much SMALLER, LOWER-RISK slice — extending the existing literal-
pattern alias / class-level lowering to MODULE-LEVEL literal `re.compile` constants
used with supported methods — likely covers the `import numpy` path WITHOUT the
type-system `RegexType` + escaping reconciliation. That smaller slice should be
scoped against the real count diagnostic first (confirm exactly which import-path
modules' `re.compile` fall back) before committing to the full general object for the
import boundary. This does not contradict the diagnostic; it explains that the raw 71
is build-code-heavy, so import-boundary progress needs only the module-level literal
subset.

## Update — module-level literal re.compile ALREADY lowers native (probe, 2026-05-29)
Direct IR probe (`emit_llvm_only`) of the import-critical shape — a MODULE-LEVEL
literal `re.compile(r"\s*,\s*")` (the numpy `_core/_internal.py` `sep_re` shape) used
via `.search` — shows it ALREADY lowers NATIVE: `py_re_` present; `cpy.fn.compile`,
`cpy.get.compile`, `cpy.import.re`, and `cpy.attr.compile` all ABSENT. The existing
alias / class-level lowering already covers the import-critical module-level-literal
pattern.

CONCLUSION (import boundary): `re.compile` is NOT an `import numpy` blocker. The
import-critical shapes (module-level literal patterns + match/search/findall) are
already native, and the raw `@.cpy.attr.compile=71` from the broad diagnostic closure
is dominated by f2py (92) / distutils (59) BUILD subpackages that `import numpy` does
not load. The general `PyRegex` object (No.1) is therefore DE-PRIORITIZED for the
import goal — it remains the correct end-state for build-time (f2py/distutils) and
dynamic-pattern coverage, but it is NOT on the `import numpy` critical path. The
documented `import numpy` boundary remains `PCC-PKG-004` (CPython-extension ABI
rejection), per `docs/current-goal-state.md`. This investigation stays `active` as the
record for the (lower-priority) general-object enhancement; it should not be mistaken
for an import-boundary blocker.

## Report
(open — predecessor context: this continues the 2026-05-29 B-P0-PKG no-libpython
fallback-shrinkage track recorded in `docs/current-goal-state.md` after the
`os.path.expanduser` and `os.path.realpath` native slices.)
