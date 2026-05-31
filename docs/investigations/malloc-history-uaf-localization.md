# Investigation: Using `MallocStackLogging` + `malloc_history` to localize a UAF

## Summary

When pcc1 self-host crashes with `nanov2_guard_corruption_detected` deep
inside a later `malloc` call, the immediate backtrace points at the
*allocation that detected the broken free list*, not at the code that
caused the corruption.  Static analysis and minimal repros both failed
to isolate the cause.  The lookup that finally worked was macOS's
`malloc_history(pid, addr)` against the address of the first stale
`py_decref` caught by a debug probe.

This doc records the workflow so we don't waste another hour re-deriving
it.  The exact bug instance is in
`docs/investigations/pcc1-self-host-parse-float-literal-uaf.md`; the
*technique* below applies to any pcc-runtime UAF.

## When this technique is useful

Use it when you have:

- a deterministic crash inside `nanov2_guard_corruption_detected` or
  similar nano-allocator integrity check, **AND**
- `MallocScribble=1` / `MallocGuardEdges=1` / `libgmalloc` make the
  crash disappear (classic UAF / buffer-overrun signature), **AND**
- the crash backtrace points at an *allocation*, not a *write*, so
  there's no obvious "who corrupted this" call site.

Don't use it when the crash bt already points directly at the bad
write — read the code there first.

## Workflow

### 1. Add a debug probe to py_decref / py_incref

In `pcc/py_runtime/py/py_obj.py` and a C helper in
`pcc/py_runtime/src/pcc_threads.c` (so the symbol is in the pcc-py
archive):

```python
# pcc-py port py_obj.py
_pcc_debug_bad_incref = extern("pcc_debug_bad_incref", (c_ptr, c_int32), c_void)

@c_abi_export("py_incref")
def py_incref(o) -> None:
    if ptr_is_null(o): return
    if is_tagged_int(o): return
    tag: int = load_i32(o, 8)
    if tag < 0 or (tag > 27 and tag < 100) or tag > 500:
        _pcc_debug_bad_incref(o, tag)
        return
    flags: int = load_i32(o, 12)
    if (flags & 1) != 0: return
    pcc_refcount_incref(o)

# Same shape inside py_decref to catch stale decrefs.
```

```c
/* pcc/py_runtime/src/pcc_threads.c */
void pcc_debug_bad_incref(void *o, int32_t tag) {
    fprintf(stderr, "[BAD_INCREF] o=%p tag=%d\n", o, tag);
    fflush(stderr);
    __builtin_trap();
}
```

This catches the *first* time refcount-side code touches a freed-and-
reused chunk: the chunk's `type_tag` will be garbage (some random i32
that happens to not collide with `0..27` or `100..500`).

### 2. Build pcc1 with the probe in the pcc-py archive

```bash
PATH="$PWD/.venv/bin:$PATH" make -B -C pcc/py_runtime libpy_runtime.a libpy_runtime_pcc_py.a
rm -f pcc1
uv run pcc --ir-scaffold=on --python-libpython=off --backend self pcc/__main__.py -o pcc1
```

The C helper goes in libpy_runtime.a (used during pcc1 link) **and**
`pcc_threads.c` is also pulled into libpy_runtime_pcc_py.a (which pcc1
itself links).  Both archives must be rebuilt.

### 3. Run pcc1 under lldb with `MallocStackLogging=1`

```bash
cat > /tmp/lldb_history.txt <<'EOF'
breakpoint set -n pcc_debug_bad_incref
process launch -- --ir-scaffold=on --python-libpython=off --backend self pcc/__main__.py -o /tmp/p.out
register read x0
script
import os
addr = lldb.frame.FindRegister("x0").GetValueAsUnsigned()
pid = lldb.process.GetProcessID()
print(f"#### ADDR={hex(addr)} PID={pid}")
os.system(f"malloc_history {pid} {hex(addr)} 2>&1 | head -80")
DONE
quit
EOF
MallocStackLogging=1 timeout 90 lldb -s /tmp/lldb_history.txt -- ./pcc1 2>&1 | tail -60
```

Two important details:

1. The `script` block runs Python *inside lldb* once the trap fires,
   pulling out `x0` (the bad pointer) and the running pid, then forks
   `malloc_history` against that pid while the process is still
   stopped.  After lldb exits, the pid is gone and the stack-log files
   are deleted — you have to capture in-process.
2. `MallocStackLogging=1` is set as an env var, not a `MallocLog…`
   variant.  `MallocStackLoggingNoCompact=1` also works but produces
   bigger logs.

### 4. Read the malloc_history output

You'll see something like:

```
ALLOC 0x600059c1de60-0x600059c1de89 [size=42]:
  ... | _e_Num | _str_alloc | _malloc_zone_malloc ...

FREE  0x600059c1de60-0x600059c1de89 [size=42]:
  ... | _e_Num | py_dealloc_str | find_zone_and_free
```

The `ALLOC` and `FREE` traces are the call stacks at allocation and
free time.  If both happen inside the same logical operation (here,
`_e_Num`'s flow) and the address is what the probe reported, you have:

- where the chunk was first allocated,
- where it was freed,
- which function the stale ptr belongs to (top of the FREE stack
  *and* the function that the probe trapped from).

That gives you three coordinates to localize a UAF:

| | who |
|---|---|
| **allocator entry** | top of the ALLOC stack |
| **freer** | top of the FREE stack |
| **stale-deref site** | the function the probe trapped from |

For our bug, all three pointed at `_e_Num` ↔ `_parse_float_literal_lift`,
which immediately narrowed the search to those two functions and
their string-handling locals.

### 5. Cross-check with disassembly

Once you know which function holds the stale local, disassemble it to
confirm the bug shape:

```bash
cat > /tmp/lldb_disasm.txt <<'EOF'
disassemble -n user_pcc_parse_py_lift__parse_float_literal_lift -c 400
quit
EOF
echo | lldb -s /tmp/lldb_disasm.txt -- ./pcc1 > /tmp/disasm.log
grep -nE "x29, #-0xNN" /tmp/disasm.log     # find writes to the bad slot
```

For the parse_float_literal_lift case, the bad slot was `[x29, #-0x50]`,
and asm clearly showed `ldur x9, [x29, #-0x8]; str x9, [x13]` (param
load **without** any `incref` call) followed by store into the local
slot.  That confirmed the alias-without-incref bug.

## Don't forget to

- **Revert the debug probe** before merging — it's a deliberate trap,
  not a production guard.  Keep it on a branch / in `git log` so the
  next investigation can re-apply it quickly.
- Keep both `--python-libpython=off` and the default malloc settings
  when reproducing.  `MallocScribble=1` / `libgmalloc` change the
  reuse pattern enough to hide the bug entirely.
- Use a hard `timeout` around any pcc1 invocation that loops on the
  bug.  pcc1 prints `Heap corruption detected` on /dev/tty and exits
  via `abort()`, but a lldb-attached run can sit in the trap forever
  if you forget.

## What this doesn't catch

`malloc_history` requires the corruption to involve a real
malloc/free pair, observable at allocator level.  Bugs that don't
free anything (e.g. plain double-incref leaks, or wrong-sized
copies that overflow into the next-chunk metadata without first
calling free) won't show up cleanly in the history.  For those,
fall back to:

- `MallocGuardEdges=1` (catches buffer overruns at the page boundary)
- `libgmalloc` (turns each allocation into its own page, catches OOB
  reads/writes immediately)
- Direct `lldb` watchpoint on the suspected slot

The gating rule: if `MallocScribble=1` *also* hides the crash, the
free/realloc reuse pattern matters and `malloc_history` is the right
tool.
