# pcc1 cannot compile a module-level `try` / `except`

## Symptom

`pcc1` fails to compile `pcc/__main__.py`, so **stage2 cannot run at all**. The
CLI reported only:

```
error: PCC-PY-COMPILE-001: [python-frontend] compile failed
  note: exception_type=Exception; backend=self; python_libpython=off; ir_scaffold=on
```

with an empty message, no module, no line.

## Test [CONFIRMED]

Nine lines. `pcc/py_frontend/pipeline.py` carries the same shape at lines
308-325 (a `PCC_DEBUG_RUNTIME` probe), which is the statement stage2 dies on.

```python
try:
    Z = 1
except Exception:
    pass


def main() -> None:
    print("ok")


main()
```

```
CPython                                        ok
host pcc  --backend self --python-libpython=off  exit 0, binary prints "ok"
pcc1      --backend self --python-libpython=off  exit 1
```

Command:

```bash
./build/bootstrap/pcc1 --backend self --python-libpython=off --ir-scaffold=on MIN.py -o MIN
```

The **same construct inside a function compiles fine** — so this is specific to
module-level lowering, not to `try` itself:

```
module-level  try: Z = 1 / except: pass          FAILS
in-function   try: with open(...) / except: pass  OK
```

Also failing (all module-level, all reduce to the above): `try` + `with`,
`try` + `with` + `",".join([...])`, and the same nested inside
`if os.environ.get(...).strip():`.

## How it was localised

Every attempt to read the error from the exception failed, because under pcc1 a
**caught** exception carries neither `__cause__` nor `__traceback__` (probed:
`MISSING` and `<unavailable>`), while an **uncaught** one gets a full traceback
printed by the runtime. Seven successive message fixes therefore recovered
nothing.

What worked was recording position *before* the work, at three levels:

```
module    PCC_COMPILE_PROGRESS_FILE   -> pcc.py_frontend.pipeline
function  marker in _emit_user_function -> compile_python_multi (stale; see below)
statement marker in _emit_stmt          -> ExprStmt @323:1
```

The function marker was **misleading**: module-level statements do not go
through `_emit_user_function`, so it still held the previously-lowered
function's name. The statement span was the reliable signal.

## Instrumentation hazards hit on the way (4 times)

Anything added to the frontend is compiled *into* pcc1 and can break it:

1. a module-level constant holding the marker path — comes back zeroed in a
   stripped self-hosted object build, and pcc1 then could not compile
   `print("hi")`;
2. `str(stmt.line)` — `'If' object has no attribute 'line'`; the attribute
   every statement carries is `span` (`span.line` / `span.col`);
3. `return null()` over dead code, used to disable a port function while
   bisecting — corrupted the compiled function and nearly produced the false
   conclusion "the MRO cache breaks pcc1";
4. reading an exit code from a shell pipeline, which returned `head`'s status
   instead of `pcc`'s, so a failing closure check was recorded as passing.

**Rule that came out of this: after any frontend instrumentation, compile
`print("hi")` with the new pcc1 before trusting any longer run.**

## Fixed along the way

`scripts/bootstrap.sh` printed a success-shaped
`PCC_BOOTSTRAP_STAGE_RESULT stage=2 elapsed_ms=334749 output=.../pcc2` for a
stage that crashed and produced no pcc2 — that line is what these runs are
measured by. It now prints `PCC_BOOTSTRAP_STAGE_FAILED ... rc=N output=<none>`,
fails a missing artifact even at rc=0, and tags the success line with `rc=0`.

## Status

Root cause confirmed and minimised. Not yet fixed. The fix belongs in
module-level statement lowering; the in-function path already handles `try`, so
the two paths need to be reconciled rather than a new mechanism written.
