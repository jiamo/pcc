# x86 ELF TLS focused evidence — 2026-08-14

Mode: host-side parser, self-assembly and owned-ELF structural gates; no Docker
final executable and no bootstrap.

Focused x86 TLS lowering/cross-assembly completed with 8 passed. The ELF
writer's initial-exec relocation and PT_TLS tests completed with 2 passed.
They prove `.tdata`/`.tbss`, TLS symbol typing, GOTTPOFF access and deterministic
rejection of unsupported/external/unsafe TLS shapes.

The Linux two-pthread LLVM differential, static zero-libc production closure
and sequential pcc1 -> pcc2 -> pcc3 evidence remain open.
