# P6C.6 Three-Stage Bootstrap Spike Report

**Date:** 2026-04-21
**Status:** spike complete — scope significantly larger than a single epic
**Recommendation:** break into 5 sub-milestones; ~8-12 weeks total

## Goal

The Strategy C acceptance gate (#138):

```bash
# stage 1: CPython runs pcc, compiles pcc.py → pcc1
python -m pcc pcc.py -o pcc1
# stage 2: pcc1 (native) compiles pcc.py → pcc2
./pcc1 pcc.py -o pcc2
# stage 3: pcc2 compiles pcc.py → pcc3
./pcc2 pcc.py -o pcc3
# verify
cmp pcc2 pcc3
```

`cmp pcc2 pcc3` byte-identical = self-host proven. Currently impossible
because most of pcc's own source doesn't compile through pcc's frontend.

## Survey (2026-04-21)

Ran `python -m pcc <file> --emit-llvm /tmp/t.ll` over 80 representative
pcc source files. Results:

| Category | Files | Blocker |
|---|---|---|
| ✅ Compile | 5 | Mostly empty `__init__.py` + lightweight facades |
| ❌ Type inference errors | 27 | "add an explicit cast or relax the annotation" |
| ❌ Missing builtins | 3 | `iter`, `list`, `tuple` not recognized |
| ❌ Comprehensions | 1 | `_list_comp` sentinel not lowered |
| ❌ Keyword args | 1 | "Layer 1 function calls do not handle keyword args" |
| ❌ Other NotImplementedError | 43 | Mix of codegen gaps (decorators, exception details, etc.) |
| **Total** | **80** | **~6% compile rate** |

## Gap categories (by required work)

### A. Language features — missing codegen lowering

1. **Keyword args** (1+ uses, likely 50+ total in pcc source):
   - `f(a, b=1, c=2)` — not supported beyond `print(sep=,end=)`.
   - Requires either: full keyword lowering OR a refactor pass that
     rewrites keyword calls to positional.
   - Estimated: 1-2 weeks for full lowering, 2-3 days for refactor shim.

2. **Comprehensions** (many):
   - `[f(x) for x in xs]`, `{k: v for ...}`, `(x for x in xs)`.
   - My native parser emits sentinel `_list_comp(...)` calls (see
     `pcc/parse/py_lift.py`). Codegen needs to lower those into explicit
     for loops + append.
   - Estimated: 1 week for basic list/dict/set comprehensions;
     generator expressions (lazy) are harder — likely 2+ weeks.

3. **`*args` / `**kwargs`** in function definitions:
   - Currently pcc only accepts fixed-arity defs.
   - Required for the compat shim layer (`set_body(*elements)`) and
     several pcc codegen helpers. Audit flagged 1 but there are more
     `*args` call sites in pcc source that would block self-host.
   - Estimated: 1 week.

### B. Standard library — missing stubs

pcc imports from stdlib (and its own `pcc.py_stdlib/` stubs). Several
are unstubbed or unimplemented:
- `typing` module (for type annotations) — partial stub
- `collections` (`ChainMap`, `OrderedDict`, `defaultdict`, `Counter`,
  `namedtuple`, `deque`) — existing stub works for some
- `re` — existing stub scaffolded but hollow
- `itertools` — stub scaffolded
- `json` — module-level dict/list operations heavy
- `subprocess` — `os.fork` / `posix_spawn` FFI needed

Estimated: 2-3 weeks for the subset pcc actually exercises.

### C. Type inference strictness (27 files affected)

Heavy use of `Optional[T]`, dynamic dict / list, `None` sentinels
that pcc's type inference can't narrow. Error: "add an explicit cast
or relax the annotation".

Pattern examples:
```python
x: Optional[Foo] = None  # then later: x = f()  — error
d: dict[str, Any] = {}   # Any not supported
cls_get = type(self).__getattribute__  # function-as-value
```

Two approaches:
1. **Relax pcc's type checker** — accept wider types, fall through to
   DynType + boxed objects at L3. ~2 weeks work.
2. **Refactor pcc source** — annotate more concretely, add casts. ~1 week
   for ~27 files.

### D. Legacy subsystems

Three entire subtrees are legacy opt-out paths and don't need to
compile through pcc's frontend:

- `pcc/ply/` (4700 LoC PLY library) — excluded via audit EXCLUDE_DIRS
- `pcc/lex/c_lexer.py` — legacy PLY-based C lexer
- `pcc/parse/c_parser.py` — legacy PLY-based C parser
- `pcc/parse/plyparser.py` — legacy glue

When pcc self-host runs, it shouldn't try to compile these files. Need
to teach the bootstrap script to skip them.

### E. Packaging / entry-point

`pcc/pcc.py` (the CLI) uses `click` extensively — complex decorator
syntax. Full `click` replacement (~500 LoC) + lowering of its decorator
patterns is a separate 1-2 week task. Can be stubbed for bootstrap by
auto-generating a minimal argparse-based CLI entry.

## Proposed sub-milestones

### #138.1 — Language codegen gaps (3-4 weeks)

Implement:
- Keyword args (full lowering)
- Comprehensions (list/set/dict) via AST desugaring
- `*args`/`**kwargs` in function defs

Gate: `pcc f.py --emit-llvm /tmp/out.ll` works on 50+ of 80 pcc source files.

### #138.2 — Stdlib completion (2-3 weeks)

Fill in `pcc/py_stdlib/` stubs pcc actually uses. Priority: `typing`,
`collections`, `re`, `itertools`, `subprocess`.

Gate: all `pcc/*` imports resolve during self-compile.

### #138.3 — Type inference relaxation OR source refactor (1-2 weeks)

Choose one (likely source refactor is faster):
- Option A: broaden type inference to accept more patterns
- Option B: refactor pcc's own source to be more strictly typed

Gate: no "add an explicit cast" errors on pcc source.

### #138.4 — Packaging (1 week)

Replace `click` with argparse in the pcc CLI entrypoint.

Gate: `pcc pcc.py -o pcc1` runs to completion and produces an executable.

### #138.5 — Three-stage verify (1 week)

- `scripts/bootstrap.sh` 自动化三阶段
- `cmp pcc2 pcc3` 字节相同 gate

Gate: Strategy C acceptance achieved.

## Total estimate

**8-12 weeks** of focused work across 5 milestones. Each is
independently spec-able and can be landed one-at-a-time with clean
gates.

## Immediate next steps (lowest cost)

If continuing today/this week:

1. **Write `scripts/bootstrap.sh`** — even if it can't complete, it
   provides the run framework for #138.5.
2. **Fix 1 keyword-args gap** — pick a specific codegen site, get that
   family of tests passing. Proves the pattern.
3. **Fix 1 comprehension gap** — lower `[f(x) for x in xs]` to explicit
   loop. Proves the pattern.

Each 2-3 days. Completing all three gets us from 6% → maybe 30% source
file coverage. Still not self-host but measurable forward motion.

## Progress log (2026-04-21, post-#138.1-push)

Landed against #138.1 (16 commits):

- kwargs lowering for user funcs / class init / methods / static /
  classmethod / `__call__` / `super()` calls, incl. defaults
- list / set / dict comprehensions (single + multi-generator,
  optional if-guards) over range / list / CPython-iterables,
  handling both native `_list_comp` / `_set_comp` / `_dict_comp`
  and CPython-AST `__listcomp__` / `__setcomp__` / `__dictcomp__`
  sentinel shapes
- tuple-unpack assignment (`a, b = x, y` TupleExpr form and
  `a, b = foo()` runtime form via `py_tuple_get`)
- `for (a, b) in items:` target destructuring via normalisation
- ternary `x if c else y` IfExpr with phi-join
- `isinstance(x, (A, B))` tuple form
- for-loop over list/tuple by index (`py_{list,tuple}_len/get`)
- container type subsumption (`tuple[dyn, bool]` → `tuple[str, bool]`,
  recursive over TupleType / ListType / DictType)
- comprehension-result typing (list/dict) flows into for-loop iter
- typed-container method fast paths (pcc-native, libpython-free):
  - `list`: append / extend / insert / pop / remove / index
  - `dict`: get / get(default) / keys / values / items
  - `str`: upper / lower / strip / split / join / replace / find /
    startswith / endswith
- method-call chained-result typing (`s.strip().upper()` stays native)
- CPython method fallback for DynType receivers (foreign classes
  like `llvm.ModuleRef.verify()`) with class_gen skipping unknown
  base classes
- bare-name builtin-exception raise (`raise NotImplementedError`)

Self-compile survey: 5/80 → 10/80 files (×2). py_corpus 123/152
across all phases (phase1 28/30, phase2 22/35, phase3 34/47, phase4
37/37, phase6c 2/2). Produced binaries remain libpython-free
(only libSystem + libc++ in `otool -L`).

Top remaining #138.1 categories:

- `*args` / `**kwargs` in function defs — not yet supported
- generator expression `_gen_comp` sentinel — not yet lowered
- `int()` / `set()` builtin conversions — need runtime helpers or
  CPython fallback (pulls libpython)
- CPython method kwargs plumb-through (8 files)
- `.splitlines()` — no native helper; needs stub

## #138.4 (packaging: click → argparse) — partial, full swap deferred

Taken:

- No-op decorator whitelist in layer1 (``click.command`` /
  ``click.option`` / ``click.argument`` / ``click.pass_context`` /
  ``click.group`` / ``functools.wraps``). ``_decorator_qualname``
  recognises Name / Attr chain / Call(chain, …) decorator shapes.
- ``click`` added to ``_COMPILE_TIME_ONLY_MODULES`` in both layer1
  and pipeline's link-needs scanner — import emits nothing and the
  binary stays libpython-free.
- ``for k in d:`` (DictType) and ``for x in obj:`` (DynType) lower
  via pcc-native helpers so the rest of pcc.py's lowering can
  proceed without runtime libpython dependency.

Remaining for a full swap:

- A click-compiled binary can't parse ``--option`` flags at
  runtime, so end-user CLI still needs click + CPython. Full
  argparse rewrite is the same 1-week task it was.
- pcc.py at codegen now hits ``tuple(iter)`` / ``set(iter)`` /
  ``int(str)`` / ``format()`` — builtin constructors and str
  format, each requiring a pcc-native runtime helper or a
  CPython-fallback path that preserves the no-libpython rule.

These aren't strictly packaging; they're follow-ups for the
``#138.x-long-tail`` bucket.

## #138.5 (three-stage bootstrap) — blocked on multi-file compile

``scripts/bootstrap.sh`` stage 1 (CPython hosted ``python -m pcc``)
hits the cross-module import wall::

    pcc/__main__.py:
        from .pcc import main
        main()

pcc today compiles a single ``.py`` file per invocation; the
``from .pcc import main`` statement routes through
``py_cpy_import`` and the subsequent bare ``main()`` call hits
``Layer 1 unknown function 'main'`` because the user-symbol
registry is per-compilation-unit.

Bootstrap therefore needs either:

1. **Multi-file compile mode** — feed ``pcc/*.py`` to a single
   invocation and link their emitted object files together.
   Fundamentally new feature, probably ~1-2 weeks.
2. **Separate compilation + linker step** — emit a ``.o`` per
   module and teach the linker to resolve cross-module user
   symbols. Same scope.

Neither is achievable without #138.4 being done first (bootstrap
entry still pulls click via pcc.py), so #138.5 stays blocked on
#138.4 and the multi-file epic.

## #138.3 (type inference relaxation) — closed with above work

Landed:

- container type subsumption (`tuple[dyn, bool]` → `tuple[str, bool]`
  and similar recursive subtyping over Tuple/List/Dict)
- comprehension result typing (list/dict result type flows into
  for-loops and subscript reads)
- method-call chained result typing (`s.strip().upper()` stays
  StrType rather than falling to Dyn)
- compile-time `isinstance(x, BuiltinType)` when the operand's
  static type is known (skips runtime dispatch, returns const `i1`)

PyFrontendError (type-mismatch) dropped from ~10 files in the
survey to 0 after these changes — no type-inference error category
left in the blocker list. Remaining failures are codegen
(NotImplementedError on specific constructs) or linking.

## #138.2 (stdlib stubs) — partial push

- Routed ``__future__`` and ``typing`` imports as compile-time-only
  (emit no runtime IR, don't trigger libpython link). `layer1`
  drops the import; `pipeline._module_needs_libpython` treats them
  as non-libpython. Result: programs that only lean on typing stay
  libpython-free (verified via `otool -L`).
- Remaining stdlib modules (``dataclasses``, ``re``, ``collections``,
  ``enum``, ``functools``, ``os``, etc.) already have stubs in
  ``pcc/py_stdlib/`` but the frontend routes them through
  ``py_cpy_import`` instead of compiling the stub as an extra
  module. That "stub-as-module" feature is the next-level
  unblocker and hasn't been built this round.
- ``dataclasses`` can't be blanket compile-time-only: ``@dataclass``
  is compile-time but ``field(default_factory=list)`` is runtime,
  and pcc's codegen doesn't yet consume ``field(...)`` at compile
  time. Needs either a field-handling fast path or stub routing.

## Alternative: minimize self-host scope

Strategy C's original goal is "no libpython at runtime". The spec
doesn't require pcc to compile *itself*; it requires pcc to compile
**user C programs without libpython in the resulting binary**.

Current state **already satisfies that**: `pcc foo.c` compiles a native
binary that has no libpython dep (verify via `ldd pcc-produced-exe`).

If that interpretation holds, P6C.6's bootstrap (compile pcc.py itself)
may be an over-scoped gate. Worth revisiting with the user before
committing 8-12 weeks to #138.

## Decision needed

Escalate to user:
- **Path A**: Full self-host (compile pcc.py → pcc2 → pcc3). 8-12 weeks.
- **Path B**: Minimum Strategy C (user programs have no libpython dep).
  Largely done today. 0-1 weeks.
- **Path C**: Halfway — pcc can compile a representative "self-host
  subset" (parse, codegen, ir_passes minus legacy), not the full tree.
  ~4-6 weeks.
