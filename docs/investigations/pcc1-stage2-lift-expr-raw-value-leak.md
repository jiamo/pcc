# Investigation: pcc1 stage2 leaks raw Python values into AST seen by lift_expr

## Context

After commit `18f60d6a` (UAF fix in `_parse_float_literal_lift`), pcc1
is no longer corrupted by the float-literal lifter and stage1 builds a
clean binary. The next gate — pcc1 compiling pcc itself for stage2 —
still fails, but in a different way: cleanly, with a Python traceback.

The original traceback before this investigation:

```
File "/Users/jiamo/my/pcc/pcc/parse/py_lift.py", line 1, in lift_expr
File "/Users/jiamo/my/pcc/pcc/parse/py_lift.py", line 844, in parse_and_lift
File "/Users/jiamo/my/pcc/pcc/py_frontend/pipeline.py", line 2214, ...
...
AttributeError: object has no attribute __name__
```

The ``AttributeError`` is a secondary failure — ``lift_expr``'s fallback
``raise LiftError(f"no expr lifter for {t.__name__}")`` itself blows
up because ``t`` lacks ``__name__``. The primary failure is whatever
made the dispatch fall through to that raise.

## Diagnostic instrumentation

To see *what* ``e`` actually is at the failing dispatch, the raise
clause was temporarily expanded to test ``isinstance(e, pp._XYZ)`` for
all 25 expression classes, and to detect Python primitives (``None``,
``True``, ``False``, ``int``, ``str``, ``list``, ``tuple``, ``dict``).
The instrumentation also includes ``self.filename`` and a 60-character
repr of ``e`` when it is a string.

After rebuilding pcc1 with the diagnostic and running stage2, the
finding was unambiguous:

| pcc1 invocation                                           | leaked value                                    |
|----------------------------------------------------------|-------------------------------------------------|
| stage2 multi-file build of `pcc/__main__.py`             | `prim=str` `value="'"` (single-quote char)     |
| pcc1 on `pcc/parse/py_parse.py` (auto-multi-file mode)   | `prim=tuple` (a raw Python tuple)               |
| pcc1 on `/tmp/p_full.py` (single-file copy of py_parse) | `prim=str` `value="{"` (open-brace char)       |

In all three runs, ``isinstance(e, pp._XYZ)`` returned False for every
expression node class. ``t.__name__`` raised ``AttributeError``. So
``e`` is genuinely *not a parse node at all* — it is a raw Python
``str`` or ``tuple`` value that has leaked into the AST.

## What this rules in / out

**Not** a missing dispatch case: the lifter handles all 25 expression
classes that ``py_parse.py`` defines. ``isinstance`` confirms ``e`` is
none of them.

**Not** a class-identity bug from double-import: if it were, ``is``
comparisons would fail but ``isinstance`` would still match. They do
not match.

**Not** the host-side parser: when CPython hosts pcc, stage1 succeeds
and the same lifter never trips this fallback. The failure mode is
specific to pcc1 (compiled native binary) parsing Python source.

The bug is in pcc1's compiled parser/runtime: somewhere it produces an
AST node whose child slot — which the lifter expects to hold a
``pp._XYZ`` instance — instead holds a raw Python primitive. The
specific value (`"'"`, `"{"`, raw tuple) is heap-layout dependent: the
exact leak point varies between runs and input modes.

## Why minimal repros do not fire the bug

Reduced source files were tried:

```python
# /tmp/quote_tuple.py
def f():
    x = ("'", '"')
    return x
```

```python
# /tmp/qt2.py
def f(raw):
    i = 0
    while i < len(raw) and raw[i] not in ("'", '"'):
        i += 1
    return i
```

Neither triggered the LiftError. They compile (silently; layer1 codegen
emits no binary, but exits 0). ``qt3.py`` (an ``if ch in ("'", '"'):``
fragment) does emit a ``[BAD_INCREF] o=0x... tag=2043`` warning during
type_infer, suggesting refcount issues in pcc-runtime when iterating
small string tuples — but it does not crash either.

The leak therefore requires a more complex context to manifest. Likely
factors are size, heap pressure, or a specific construct in the larger
``Parser`` class in ``py_parse.py``.

## Stmt-trail diagnostic exposed a separate pcc-py codegen issue

To localize the failing statement, ``_Lifter`` was instrumented to
record ``self._cur_stmt_kind`` / ``self._cur_stmt_line`` on each
``lift_stmt`` entry and append them to the LiftError. That code passes
all hosted tests. But after building pcc1 with it, pcc1 segfaults
immediately on ``pcc/parse/py_parse.py`` instead of producing the
LiftError. Trivial inputs (``pass``, empty file) still work.

The stmt-trail diagnostic uses two patterns the diagnostic-expanded
``raise`` did not:

1. ``try: ... except AttributeError: ...`` around an attribute read,
   followed by writing the result to ``self.<name>``.
2. Module-level execution of ``self._cur_stmt_line = -1`` (signed-int
   instance attribute set with a negative literal).

One of these — most likely the ``try / except AttributeError`` paired
with the attribute write — is mis-compiled by pcc-py codegen for
self-host builds. This is an additional bug surface, distinct from
the raw-value leak. The diagnostic was reverted to a single
``try/except`` on a local variable (no ``self.``), which compiles
correctly.

## Fixed ergonomics in production

The original f-string ``raise LiftError(f"no expr lifter for
{t.__name__}")`` produces a confusing ``AttributeError`` when ``t``
lacks ``__name__``. Replaced with:

```python
try:
    _name = t.__name__
except AttributeError:
    _name = "<no-__name__>"
raise LiftError("no expr lifter for " + _name)
```

This is a minimal robustness improvement: the primary failure now
surfaces as a real ``LiftError`` even when the parse-node convention
is violated, instead of cascading into an unrelated attribute error.

## Localized to a stmt range — and to a class of methods

A second diagnostic was added in ``lift_module`` only (no instance
attributes, just locals + ``try / except LiftError``): the top-level
``mod.body`` loop now catches ``LiftError`` and re-raises with the
0-based stmt index and ``s.line`` of the failing top-level stmt.

Re-running stage2 on a copy of ``py_parse.py`` reports the failure
deterministically:

```
LiftError: no expr lifter for <no-__name__> | top-stmt #66 line=419 in /tmp/p_full.py
```

Top-stmt #66 line 419 = ``class Parser:``. That narrows the leak to
*inside* the Parser class body, the only large class in the file.

A third diagnostic (mirrored try/except in ``_lift_stmt_list``)
exposed nested-stmt context but tripped the *separate* pcc-py codegen
bug noted above — instance-method ``try/except LiftError`` produced
binaries that segfaulted on trivial inputs. The diagnostic was reverted
after extracting these failing nested-stmt reports across 5 runs:

| iter | nested-stmt # | line | Parser-class member             | leaked type     |
|------|---------------|------|---------------------------------|-----------------|
|   1  |          59  | 1544 | ``_string_piece`` (classmethod) | `<no-__name__>` |
|   2  |          60  | 1549 | ``_string_is_f`` (classmethod)  | `<no-__name__>` |
|   3  |          57  | 1524 | ``_string_prefix`` (staticmethod) | `_While`      |
|   4  |          57  | 1524 | ``_string_prefix`` (staticmethod) | `<no-__name__>` |
|   5  |          63  | 1616 | ``_split_fstring_expr`` (classmethod) | `<no-__name__>` |

All five failures hit decorator-bearing methods — the only seven
``@classmethod`` / ``@staticmethod`` decorators in the entire Parser
class are at lines 1523, 1530, 1543, 1548, 1552, 1556, and 1615. The
failure region 1523–1616 maps **exactly** to the decorator region. No
non-decorated methods elsewhere in Parser have ever triggered the leak.

## What the variance reveals

Across all observed runs the leaked-into-lift_expr value has been:

- ``str`` ``"'"`` (a single quote character)
- ``str`` ``"{"`` (open brace)
- a raw Python ``tuple``
- a ``_Return`` parse node (a *statement* node, not an expression)
- a ``_While`` parse node (also a statement)

A statement node showing up where the lifter expects an expression is
diagnostic. ``_While`` and ``_Return`` are constructed inside the
function body of these decorated methods (they contain ``while`` loops
and ``return`` statements). The lifter's only paths into ``lift_expr``
during a FuncDef pass are: parameter type/default annotations, the
return type annotation, decorators, and operands of body statements.
Decorator and annotation expressions for these methods are simple
``_Name``s; the body statements should never be passed to ``lift_expr``
at all (``_lift_stmt_list`` calls ``lift_stmt`` for body items).

So a body ``_While`` / ``_Return`` arriving in lift_expr is only
explainable by an aliasing or use-after-free in pcc1's compiled
parser/runtime: a ``_FuncDef``'s ``decorators`` (or ``returns``, or
``params[i].annotation``) field is being overwritten with the address
of a body statement that was allocated nearby. The fact that the
specific leaked object varies across runs (sometimes a primitive,
sometimes a stmt node) is the signature of a heap-layout-dependent
memory bug, not a deterministic source-level mistake.

## Resolution: post-mutation of dataclass field is the trigger

The smoking-gun pattern in ``_parse_decorated``:

```python
fn = self._parse_funcdef()
fn.decorators = list(decorators)
```

``_parse_funcdef`` constructs a ``_FuncDef(...)`` *without* passing
``decorators``, leaving the field at its dataclass default (``None``).
The follow-up assignment then mutates that field in pcc1's runtime —
and that is where the corruption fires.

Refactoring to set ``decorators`` in the constructor (instead of
post-mutation) makes the bug disappear. Concretely:

- ``_parse_funcdef`` now accepts a ``decorators=`` kwarg; if not
  supplied it defaults to ``[]`` and is passed into the ``_FuncDef``
  constructor.
- ``_parse_decorated`` calls ``self._parse_funcdef(decorators=list(
  decorators))`` instead of mutating ``fn.decorators`` after the fact.
- The async path is updated symmetrically: ``_parse_async_stmt``
  threads a ``decorators`` kwarg into ``_parse_funcdef``.

Verification with the rebuilt pcc1:

- single-file ``pcc1 .../py_parse.py`` — no LiftError across 5 runs;
  pipeline now proceeds into ``type_infer``.
- multi-file stage2 ``pcc1 pcc/__main__.py`` (with ``--verbose``) — all
  16 modules' exports phase completes; ``type_infer[pcc.__main__]``
  and ``codegen pcc.__main__`` log lines fire; exit 0. Without
  ``--verbose`` it can still segfault later in the pipeline (a
  separate downstream memory bug, not the lift_expr leak).

Hosted (CPython) tests pass — the constructor-style refactor is
behavior-preserving.

## Why post-mutation explains the variance

The earlier diagnostic table showed leaked values that included
``_Return`` and ``_While`` parse nodes from inside the **method's
function body**. That is consistent with this picture:

1. ``_parse_funcdef`` allocates a ``_FuncDef`` with
   ``decorators=None``.
2. ``self._parse_block()`` parses the body, allocating ``_Assign``,
   ``_While``, ``_Return``, etc.
3. Control returns to ``_parse_decorated`` and ``fn.decorators =
   list(decorators)`` writes through whatever pcc1's ``setattr`` path
   does for a default-``None`` slot.

If pcc1's compiled ``setattr`` for a default-``None`` field has an
off-by-one or an aliased pointer, the write can clobber a neighboring
slot — for example a body-statement pointer that was just allocated
adjacent to the ``_FuncDef`` on the heap. After that, the lifter sees
``s.decorators[0]`` as ``_While``, ``_Return``, or whatever heap
sliver happened to land there. Setting the field at construction time
side-steps that path entirely.

The deeper pcc1 codegen / runtime bug for "default-``None``
post-mutation" is real and worth fixing, but it is a runtime fix —
this commit applies the source-side workaround that unblocks stage2.

## Status / next steps

- Stage1: clean.
- Stage2 lift_expr blocker: **resolved.** All 16 modules now lift
  cleanly through ``type_infer``.
- Remaining downstream symptoms: ``--verbose``-sensitive segfault
  during/after codegen for ``pcc.__main__`` — a separate memory bug
  to investigate next.
- Follow-up: still worth fixing the underlying pcc1 setattr-on-
  default-``None`` issue. The pattern (post-construction mutation of a
  dataclass field whose default is ``None``) is common and likely
  bites other call sites too. Adding an audit-selfhost check or
  smoke-test that constructs a dataclass and mutates a default-``None``
  field would make the regression hard to re-introduce.
- Repro: ``./pcc1 --ir-scaffold=on --python-libpython off --backend self
  -o /tmp/p_bin pcc/parse/py_parse.py`` — fires reliably; payload type
  varies between runs but the failure window (top-stmt #66, decorated
  methods 1523–1616) is constant.
- Likely fix locus: pcc1's parser code path for ``_parse_decorated`` /
  ``_parse_funcdef`` (``pcc/parse/py_parse.py`` lines 1011–1099). The
  ``fn.decorators = list(decorators)`` assignment after constructing
  ``_FuncDef`` is the obvious suspect — possibly an alias to a list
  that's reused across calls, or a write to a freed object.
- Recommended workflow: malloc_history (see
  ``docs/investigations/malloc-history-uaf-localization.md``) on the
  decorated-method-only repro, set a breakpoint just before
  ``fn.decorators = list(decorators)`` and just after, and watch for
  the ``decorators`` slot being clobbered before the lifter reads it.

## Files touched in this investigation

- ``pcc/parse/py_lift.py`` —
  - safe ``try / except`` around ``t.__name__`` in the fallback raise
    (kept).
  - top-level stmt-context wrap in ``lift_module`` (kept; uses only
    locals and is safe under pcc-py codegen).
  - nested-stmt-context wrap in ``_lift_stmt_list`` (reverted; the
    instance-method ``try/except`` form trips a separate pcc-py
    codegen bug — see
    ``feedback_pcc_py_codegen_self_attr_try.md`` in user memory).
- ``pcc/parse/py_parse.py`` —
  - ``_parse_funcdef`` accepts ``decorators=`` kwarg and threads it
    into the ``_FuncDef`` constructor.
  - ``_parse_decorated`` and ``_parse_async_stmt`` no longer mutate
    ``fn.decorators`` post-construction.
- ``docs/investigations/pcc1-stage2-lift-expr-raw-value-leak.md`` —
  this document.

## Files touched in this investigation

- ``pcc/parse/py_lift.py`` — minimal robustness change to the fallback
  raise (kept). Verbose diagnostic and stmt-trail (reverted).
- ``docs/investigations/pcc1-stage2-lift-expr-raw-value-leak.md`` —
  this document (new).
