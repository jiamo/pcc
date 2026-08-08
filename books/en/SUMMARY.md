# The Design and Implementation of pcc — Contents

- [Preface](ch00-preface.md)

## Part I: Overview

- [Chapter 1: Introduction — Owning Python Execution](ch01-introduction.md)
- [Chapter 2: Architecture Overview](ch02-architecture.md)

## Part II: The C Frontend

- [Chapter 3: The C Frontend — Parsing, fake-libc, and the Evaluator](ch03-c-frontend.md)
- [Chapter 4: C Semantic Lowering and Signedness](ch04-c-lowering-signedness.md)

## Part III: The Python Frontend

- [Chapter 5: The Typed-Python Frontend](ch05-typed-python-frontend.md)
- [Chapter 6: Python Lowering — the Facade and the Mixins](ch06-python-lowering.md)

## Part IV: The Runtime

- [Chapter 7: The Object Model](ch07-object-model.md)
- [Chapter 8: The Exception Model](ch08-exception-model.md)
- [Chapter 9: Reference Counting and Ownership](ch09-refcount-ownership.md)

## Part V: GC — the Five-Backend Laboratory

- [Chapter 10: The Five-GC Architecture and the Equality Contract](ch10-gc-architecture.md)
- [Chapter 11: The Five Backends — from Refcounting to Relocation](ch11-gc-backends.md)

## Part VI: Backends and Linking

- [Chapter 12: The LLVM Backends and llvm_capi Parity](ch12-llvm-backends.md)
- [Chapter 13: The Self Backend — Native Emission without LLVM](ch13-self-backend.md)

## Part VII: Self-Hosting

- [Chapter 14: No-libpython and Zero-libc — Making the Runtime pcc-Python](ch14-no-libpython.md)
- [Chapter 15: Bootstrap — the pcc1→pcc2→pcc3 Fixed Point](ch15-bootstrap-fixed-point.md)

## Part VIII: The Value Model and the Ecosystem

- [Chapter 16: The Value Model — Projection, not Fixed Width](ch16-value-model.md)
- [Chapter 17: Packages, the C-API Shim, and Extension ABI](ch17-packages-capi.md)

## Part IX: Engineering Method

- [Chapter 18: Method — Tests, Investigations, and Claim Hygiene](ch18-engineering-method.md)

## Part X: Accelerators

- [Chapter 19: GPU Kernel IR, Metal, and Accelerator Execution](ch19-gpu-kernel-ir.md)

## Part XI: Application Execution

- [Chapter 20: Declarative GUI — Components, Scheduling, and a Webview-Free Application Boundary](ch20-declarative-gui.md)

## Appendices

- [Appendix A: Repository Map](appendix-a-repo-map.md)
- [Appendix B: Glossary](appendix-b-glossary.md)
