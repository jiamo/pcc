# Investigation Report: Why Make-Derived CPP Flags Covered PCRE but Not Lua, zlib, or SQLite

## Executive Summary

After `pcc` gained `--cpp-arg` and make-derived preprocessor flag inference,
one question became unavoidable:

- why can `--sources-from-make` or `--depends-on PATH=GOAL` cover PCRE
- but still not fully cover Lua, zlib, and SQLite

The short answer is:

- make-derived inference can only recover flags that the build system actually
  emits in real compile commands
- some projects expose the needed configuration there
- some do not
- and some of the currently required flags are not project build flags at all,
  but explicit compatibility workarounds for `pcc`

This distinction matters. It tells us when automatic inference is principled
and when explicit `--cpp-arg` is the correct interface.


## The Underlying Rule

`pcc` can infer preprocessor flags from:

- `make -n` / `make -nB` compile lines
- explicit user input such as `--cpp-arg`

It cannot infer flags from:

- comments in headers
- source code that mentions an optional macro but does not define it
- conventions like "this project is usually configured with autotools"
- project names or directory names

So the real rule is:

> Build-system-derived inference can only recover configuration that the build
> system has already made concrete.


## Why PCRE Worked

PCRE is the clean example.

Its build system emits `-DHAVE_CONFIG_H` directly in the compile command for
the library target. A dry-run looks like:

```text
/bin/sh ./libtool ... gcc -DHAVE_CONFIG_H -I. ... -c pcre_compile.c
```

That means `pcc` can recover two important facts without any project-specific
heuristic:

- this target expects `HAVE_CONFIG_H`
- this target uses the local include path

In this case, make-derived inference is not guessing. It is simply reusing the
project's own declared build inputs.


## Why Lua Did Not

Lua's `make -n lua` output does include real compile flags, for example
`-DLUA_USE_LINUX`. But it does **not** include the two extra flags currently
needed by `pcc`:

- `-DLUA_USE_JUMPTABLE=0`
- `-DLUA_NOBUILTIN`

Those flags come from a different category.

`LUA_USE_JUMPTABLE` controls a code path in `lvm.c` that Lua enables by
default under GCC-like compilers. `LUA_NOBUILTIN` disables `__builtin_expect`
use in `luaconf.h`.

The important point is:

- Lua's build system did not choose those values for this target
- `pcc` users chose them explicitly to avoid compiler paths that are not yet
  fully supported

So `--sources-from-make lua` cannot infer them, because they are not part of
the build system's emitted compile command.


## Why zlib Did Not

zlib failed for a different reason.

The checked-in tree was not configured. Its top-level `Makefile` just says:

```text
Please use ./configure first.
```

That means there are no real compile commands to inspect for `libz.a`, and
therefore nothing from which to infer:

- `HAVE_UNISTD_H`
- `HAVE_STDARG_H`

Those macros are normally configuration results. In an unconfigured source tree,
they are not yet part of the concrete build.

So this was not a failure of make-derived inference. It was a missing configured
build description.


## Why SQLite Did Not

SQLite was different again.

The command we were running was:

```bash
uv run pcc --depends-on projects/sqlite-amalgamation-3490100/sqlite3.c \
  projects/test_sqlite_main.c
```

That does not name a make goal at all. It just names a single source file.

So there is no build-system layer to inspect, and no compile command from which
to infer anything.

The explicit flags currently used for SQLite:

- `-U__APPLE__`
- `-U__MACH__`
- `-U__DARWIN__`
- `-DSQLITE_THREADSAFE=0`
- `-DSQLITE_OMIT_WAL=1`
- `-DSQLITE_MAX_MMAP_SIZE=0`

are therefore not "missing make inference". They are an explicit choice of a
configuration subset that `pcc` can currently compile and run reliably.


## Two Kinds of Explicit Defines

Not all explicit `-D` or `-U` flags mean the same thing.

### 1. Build configuration flags

Examples:

- `-DHAVE_CONFIG_H`
- `-DHAVE_UNISTD_H`
- `-DHAVE_STDARG_H`

These describe facts the build system is supposed to decide. The ideal source
for them is:

- configured `make` output
- `compile_commands.json`
- generated config headers

If they must be written explicitly for now, that is acceptable, but the long
term goal should be to derive them from the project's real build metadata.

### 2. Compiler-compatibility flags

Examples in the current repository:

- `-DLUA_USE_JUMPTABLE=0`
- `-DLUA_NOBUILTIN`
- the current SQLite flags that disable unsupported or unstable platform paths

These are not just "project config". They are compatibility choices made to
fit the current compiler implementation.

That does **not** make them wrong. It means they should be:

- explicit
- documented
- visible in tests and examples

and **not** silently injected by the compiler based on project detection.


## What Is Reasonable Today

The current explicit-flag model is reasonable if it follows these rules:

1. The flag is visible in the command, test, script, or README.
2. The compiler does not infer it from a project name or path.
3. The flag has a clear role:
   either build configuration or compiler compatibility.

Under that model:

- PCRE can already rely on make-derived inference for `HAVE_CONFIG_H`
- zlib should ideally move to a configured build so `HAVE_*` comes from the
  build system
- SQLite needs either a real build description or an explicit compatibility
  preset
- Lua can keep explicit compatibility flags until `pcc` supports the relevant
  code paths well enough to remove them


## What Would Be Wrong

The following would be a regression in design quality:

- "if the path contains `sqlite`, add `-DSQLITE_THREADSAFE=0`"
- "if the directory looks like zlib, add `-DHAVE_UNISTD_H`"
- "if the project is Lua, disable jump tables automatically"

Those approaches hide build inputs inside compiler implementation and make the
CLI behavior non-transparent.


## Practical Guidance

When integrating a new project, ask these questions in order:

1. Does its build system already emit compile commands with the needed `-D`,
   `-U`, and `-I` flags?
2. If yes, can `pcc` recover them through `--sources-from-make` or a similar
   build-metadata path?
3. If not, are the missing flags true build configuration results, or temporary
   compatibility flags for `pcc`?
4. If they are compatibility flags, keep them explicit and documented instead
   of hiding them in compiler internals.


## Recommended Direction

The right long-term direction is:

- keep adding support for build-metadata-driven inputs
- keep explicit compatibility flags visible at the call site
- remove compatibility flags only by improving compiler correctness
- do not reintroduce project-name-based preprocessor heuristics

That keeps the layering clean:

- the build system owns project configuration
- the CLI exposes explicit user choices
- the compiler implements C semantics

