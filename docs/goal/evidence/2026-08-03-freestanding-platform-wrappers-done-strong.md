# Freestanding pcc-Python platform-wrapper closure

Date: 2026-08-03

Task: `LIBC-P2-THIN-WRAPPERS`

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Strict wrapper fingerprints:

```text
freestanding_platform_io.py       d76c28c67e44a52a5a39a23ac8b01b2b0f6cc51490a17d2a4a05b52bcc01dcaf
freestanding_platform_fs.py       6863b3912f576e4b19aa5d8c098a164965ca19559f105f2e881b87780a811426
freestanding_platform_env.py      b09c1148b35e60bc1ffb548daeeb667bb9b132aaa6c4ac5776175a3622fe2d6f
freestanding_platform_system.py   15001c7fa0d621c8896ea761e27299fb40d31c2f3a321de71e97f7a4cc787d9e
freestanding_platform_time.py     1517d0f6d404cdd6708b137cbf17c1c3a32d6e2c51e79418579b8e4bd7c3b48a
freestanding_platform_process.py  adb1f3a30a570a5069c3a19ab9802f163d6f6b51b8ce773e8b3b919b94f514db
freestanding_platform_socket.py   e2fe31f21bdc3fbec284899d300baadd5c277158682f55879bf8f796b883dba8
```

## Claim

The finite platform-wrapper ABI for IO, filesystem, owned environment and
snapshots, uname/CPU queries, clocks/sleep, spawn/PATH/system/timeout/wait/
signal/exit, numeric IPv4/IPv6 plus fail-closed `/etc/hosts` resolution, and
TCP socket operations is authored in strict freestanding pcc-Python. Linux
x86_64 lowers the supported boundary to raw syscalls; Darwin retains only the
explicitly ratcheted libSystem machine boundary.

DNS/NSS and general POSIX completeness remain outside this finite task. This
evidence does not widen the wrapper surface.

## Focused and ratchet gates

```text
45 passed in 16.18s
  test_freestanding_platform_{io,fs,env,system,time,process,socket}.py

2 passed, 2 deselected in 0.33s
  current Darwin import ratchet and Darwin/Linux platform-label contract
```

The process archive assertion was updated from a stale caller count to exact
current owners: `pcc_runtime_log.o` and `py_libc_fortify.o` import
`pcc_platform_abort`; `py_process.o` imports `pcc_platform_exit`; none imports
host `abort` or `exit`. The removed GC callers reflect the ongoing strict
pcc-Python GC migration and were not restored to satisfy the old count.

The current Darwin baseline contains 46 named imports and the threads-on
baseline 52, preserving the exact six-symbol pthread delta. This is tighter
than the original wrapper slice's 56/62 state because adjacent stdio, GC, and
runtime migrations removed more imports. Linux wrapper objects retain their
raw-syscall contract.

## Cold-stage cost localization

The original 94.785-second stage1 run had no detailed profile and was recorded
as a possible wrapper regression. Current source profiles localize the cost to
self-backend cache state rather than wrapper frontend work:

```text
profile                         wall       object hits/misses   native emit   frontend codegen
fully cold current source      140.939s        0 / 325          114.044s       10.492s
mostly warm comparison          34.784s      305 / 20             9.170s        9.368s
fully warm current source       17.815s      325 / 0              0.701s        0.311s
```

The current warm confirmation command was:

```text
gtimeout 120s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/libc-thin-wrappers-stage1-warm-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/libc-thin-wrappers-stage1-warm --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=17815
```

Its publish barrier passed. The profile attributes 13.927s to the enclosing
self-link phase but only 0.701s to native object emission; all 325 objects and
the frontend IR entry hit their content-addressed caches. The 94.785-second
observation therefore belongs to cold/partial cache population, not a
wrapper-specific runtime or frontend asymptotic regression.

## Fixed point and boundary disposition

No `pcc/` source changed after the current-source five-GC acceptance recorded
in `2026-08-03-freestanding-primitives-five-gc-fixed-point.md`:

```text
5 passed in 778.18s (0:12:58)
GC0..4; backend=self; python-libpython=off; normalized pcc2/pcc3 equal
```

The implementation/differential evidence in
`2026-08-02-freestanding-platform-process-primitives.md`, the current focused
and ratchet gates, the cache-localized stage1 profile, and the fixed-point
matrix exhaust this task's open boundary.
