# Investigation Report: SQLite Integration Exposed Casted Syscall Tables, Local Aggregate Initialization, and Darwin MCJIT Teardown

## Executive Summary

Getting `projects/sqlite-amalgamation-3490100/sqlite3.c` to run correctly through
`pcc` looked at first like one large SQLite bug.

It was not one bug.

The SQLite integration surfaced three independent failure classes:

1. casted function-pointer globals inside SQLite's Unix syscall table were
   lowered as null pointers
2. function-scope aggregate init-lists such as `struct sqlite3 db = {0};`
   were not actually initializing the local object
3. on Darwin, large multi-TU MCJIT executions could succeed and still crash
   later during llvmlite/LLVM teardown in the hosting Python process

Those failures appeared in sequence:

- first as broken Unix VFS/syscall behavior
- then as malformed internal `sqlite3` state during `CREATE TABLE`
- finally as "program printed `OK` but Python exited with SIGSEGV/SIGABRT"

The final solution was therefore layered:

- fix constant pointer initialization through casts
- fix recursive lowering of local aggregate init-lists
- isolate Darwin multi-TU MCJIT execution in a subprocess that exits via
  `os._exit()` after returning the program's result


## Initial Goal

The target command was the same dependency-driven workflow already used for
PCRE and zlib:

```bash
env -u LC_ALL uv run pcc \
  --depends-on projects/sqlite-amalgamation-3490100/sqlite3.c \
  projects/test_sqlite_main.c
```

And the explicit separate-TU form:

```bash
env -u LC_ALL uv run pcc \
  --separate-tus \
  --depends-on projects/sqlite-amalgamation-3490100/sqlite3.c \
  --jobs 2 \
  projects/test_sqlite_main.c
```

Because SQLite ships as an amalgamation, source collection was not the hard
part. The hard part was runtime correctness.


## Test Harness Shape

The SQLite smoke driver started small and later expanded. The final version in
`projects/test_sqlite_main.c` now exercises:

- `sqlite3_open()` on a real filesystem path
- schema creation through `sqlite3_exec()`
- prepared inserts with `sqlite3_prepare_v2()`
- `sqlite3_bind_text()` / `sqlite3_bind_int()`
- `sqlite3_step()` / `sqlite3_reset()` / `sqlite3_clear_bindings()`
- aggregate queries
- updates
- close and reopen of the same database file
- persisted reads after reopen

This was important because different bug classes showed up on different paths:

- VFS/syscall bugs required a real file path, not `:memory:`
- VDBE/accounting bugs showed up during DDL
- MCJIT lifecycle bugs only appeared after successful execution


## Phase 1: The First SQLite Failure Was Not MCJIT

Early failures looked like:

- hangs or crashes in Unix VFS setup
- bad behavior before useful SQL work completed

The tempting theory was:

- "SQLite is too big for MCJIT"
- "module linking/finalization is the issue"

That theory was wrong.

The runtime still failed after narrowing the execution path, and the first real
root cause turned out to be ordinary code generation.


## Root Cause A: Casted Function-Pointer Constants Became Null

SQLite's Unix VFS builds tables of function pointers using explicit casts, e.g.
patterns like:

```c
(sqlite3_syscall_ptr)lstat
```

`pcc`'s constant-pointer path previously handled direct function references but
did not correctly unwrap `c_ast.Cast` around them.

That meant some file-scope constant initializers were effectively lowered as
null pointers.

For SQLite this is disastrous because the Unix VFS dispatch table is not an
optional convenience; it is the runtime interface to filesystem behavior.

The fix was in `pcc/codegen/c_codegen.py`:

- `_build_pointer_const()` was extended to understand casts around function
  references when forming constant globals

Once that was fixed, SQLite advanced much deeper into real runtime behavior.


## Phase 2: `CREATE TABLE` Still Corrupted Internal State

After the syscall-table fix, SQLite could open the database and progress
through much more of initialization, but `CREATE TABLE` still misbehaved.

At this point a direct SQLite-internal debug harness was much more useful than
staring at generated IR.

A temporary debug build that included `sqlite3.c` directly printed key fields
from the `sqlite3` object before and after schema work:

- `errCode`
- `mallocFailed`
- `nVdbeExec`
- `nVdbeActive`
- `interrupted`

Native and `pcc` diverged sharply:

- native kept the counters sane
- `pcc` showed garbage values such as a huge `interrupted` flag and broken VDBE
  accounting


## Root Cause B: Local Aggregate Init-Lists Were Not Initializing the Object

The decisive reduction was not even a SQLite-specific program.

A small C reproducer showed that patterns like:

```c
struct S s = {0};
```

inside a function were not actually zeroing and initializing the stack object
under `pcc`.

That matters a lot for SQLite because it relies heavily on local aggregate
state objects whose fields must begin as zero.

The fix in `pcc/codegen/c_codegen.py` was:

- add explicit runtime initialization for local aggregate declarations with
  `InitList`
- recursively initialize structs/unions
- zero-fill arrays and aggregates before writing explicit initializer elements

Once this was fixed:

- the bogus `interrupted` state disappeared
- VDBE state became sane
- the system-link runtime path for SQLite passed


## Phase 3: SQLite Printed `OK`, Then Python Crashed

After the semantic fixes above, the actual SQLite program logic succeeded.

This was visible in all the right ways:

- `sqlite version ...`
- selected rows were correct
- updates and aggregates were correct

And yet the process could still die afterwards with:

- `SIGSEGV`
- or `SIGABRT`

The crucial clue was timing:

- the SQL work was already done
- the crash happened during Python garbage collection or interpreter teardown
- stack traces pointed into llvmlite/LLVM object disposal paths

That changed the diagnosis completely.

The remaining problem was not SQLite semantics. It was the host lifecycle of
MCJIT inside Python on Darwin.


## Root Cause C: Darwin Multi-TU MCJIT Teardown Was Not Stable

Large multi-TU MCJIT executions on the local Darwin / llvmlite / LLVM
combination could run correctly and still crash later during cleanup.

Several intermediate attempts were tried:

- linking all modules into one LLVM module before JIT
- detaching execution-engine wrappers
- detaching target machines and modules
- leaking detached wrappers to avoid Python GC revisiting them

These reduced some symptoms but did not make the path reliably safe.

The robust fix was architectural rather than semantic:

- on Darwin, `evaluate_translation_units()` now runs the linked MCJIT program
  in a subprocess
- the child process executes `main`
- the child writes the integer result to a small temp result file
- the child exits with `os._exit()` instead of normal Python/llvmlite teardown
- the parent returns the recorded result

This isolates unstable LLVM teardown from the long-lived parent Python process.


## Why the Subprocess Fix Is Acceptable Here

This is not a semantic compromise.

The compiled program still runs through `pcc`'s LLVM/MCJIT path. The subprocess
only changes where the lifecycle boundary lives.

That tradeoff is practical because:

- correctness matters more than in-process purity here
- the child process preserves normal program stdout/stderr behavior
- the parent avoids host-process crashes during tests and CLI execution


## Expanded Regression Coverage

After the root causes were fixed, the SQLite test coverage was broadened rather
than left at the tiny initial smoke level.

The current validation checks:

- unit collection for the amalgamation + driver
- MCJIT runtime through `evaluate_translation_units()`
- system-link runtime through emitted objects + `cc`
- on-disk database creation
- row contents from Python's stdlib `sqlite3` after the pcc-built program exits

This matters because it verifies both:

- compiler/runtime correctness while the program is running
- the actual database contents written to disk


## Final Commands

These are the useful manual entrypoints now:

```bash
env -u LC_ALL uv run pcc \
  --depends-on projects/sqlite-amalgamation-3490100/sqlite3.c \
  projects/test_sqlite_main.c \
  /tmp/pcc_sqlite.db

env -u LC_ALL uv run pcc \
  --separate-tus \
  --depends-on projects/sqlite-amalgamation-3490100/sqlite3.c \
  --jobs 2 \
  projects/test_sqlite_main.c \
  /tmp/pcc_sqlite.db

env -u LC_ALL uv run pytest tests/test_sqlite.py -q -n0
```


## Lessons for Future Debugging

SQLite was a good reminder that one large integration failure can contain
several independent bugs layered on top of each other.

The productive sequence was:

1. get the integration running at all
2. reduce the failing runtime path
3. compare native and `pcc` internal state
4. extract a tiny pure-C reproducer for the semantic bug
5. only after semantics are correct, deal with host-toolchain lifecycle issues

The important discipline is not to stop at the first fix just because one
visible symptom changes. SQLite only became stable after all three layers were
addressed:

- constant pointer initialization through casts
- local aggregate initialization
- Darwin MCJIT process lifecycle
