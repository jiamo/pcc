# Incremental Mach-O link focused evidence — 2026-08-14

Mode: host-side NativeObject incremental cache/final-link contracts.

As in the adjacent fast-path suite, the first fail-fast run found a test
`__TEXT,__text` section using the data-section default flags. The fixture now
uses `TEXT_SECTION_FLAGS`; executable-section validation remains strict.

Final result: 5 passed. Same-layout edits reuse merged state and are
byte-identical to a cold fresh link, exact actions hit the final image cache,
layout changes perform a cold prepare, corrupt/truncated state falls back
without publication, and source identity separates incompatible caches.

Public pipeline prior-artifact execution, real timing/RSS evidence and
pcc2/pcc3 fixed-point equality remain open.
