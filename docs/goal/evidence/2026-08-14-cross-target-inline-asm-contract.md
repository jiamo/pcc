# Cross-target inline-assembly contract evidence — 2026-08-14

Mode: host-side `ir_to_obj` subprocess/object-emission tests.

The complete `test_runtime_archive_provenance.py` suite passed inside the
131-case archive bundle contract run. Its focused cases verify explicit
triple/data-layout consistency, controlled rejection of foreign inline and
module assembly before LLVM emission, native same-architecture assembly, and
ordinary foreign-target IR without assembly.

Final Linux/Darwin target-labeled executable gates and sequential bootstrap
remain open.
