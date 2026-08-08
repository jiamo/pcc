# Package build ownership focused evidence — 2026-08-14

Mode: host-side ownership, receipt and local install contracts only.

`test_package_build_ownership.py` completed with 14 passed. The host-only
selection of `test_package_install.py` completed with 22 passed and the one
current-pcc1 node explicitly deselected.

The first broad invocation exposed that the final node auto-builds stage1; that
process group was terminated immediately and the incomplete run was not used
as evidence. No child survived. The recorded green run therefore proves only
the host-side contracts: owned mode fails before unowned build tools,
compiler/tool/source receipts are bound, and host/prebuilt provenance cannot be
silently relabeled owned.

The current-pcc1 package path, clean-checkout Meson build and explicit host-mode
network install remain open for the final compiler/package gate.
