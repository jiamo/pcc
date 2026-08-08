# Port ABI autogen focused evidence — 2026-08-14

Mode: host generator/layout/source contracts; runtime descriptor execution was
deliberately excluded pending the final content-addressed runtime build.

The fail-fast serial command ran `test_port_abi_constants.py`,
`test_runtime_layout_contract.py`, and the source/static portion of
`test_descriptor_instance_boundary.py`. Result: 56 passed and the two
archive-backed descriptor deallocation cases were deselected.

This proves the generated inventory is current against the C headers, imported
constants lower correctly, frontend/freestanding aliases cover the inventory,
core port readers do not reintroduce raw public tags/layout offsets, and
reserved property/classmethod/staticmethod tags stay outside the instance
layout boundary. C-versus-pcc-Python descriptor deallocation under GC0..4 and
current-pcc1/bootstrap remain the promotion boundary.
