# Tuple completion scan: real quadratic baseline and mirror guard

Date:2026-09-06. Status: focused mirror semantics passed; matched rebuilt
runtime control and real-worker performance pending.

Native tuple([1]*N), linked to the immutable v80 runtime, executes1.221B,
4.827B and19.255B instructions at10k/20k/40k elements. Outputs match CPython.
The actual source scans the populated prefix after each setter call, even
though initialization publication has no effect onGC0..3.

The C and pcc-Python guards now skip only that no-op completion scan after
the original store/incref/cycle tracking. GC4 still runs the exact old scan
and publication contract.20 deterministic tests pass;2 more cover per-call
backend selection and Python None versusNULL. GC1/2 allocation-grace flags
remain unchanged. No layout, identity, barrier or reference policy changed.

The rebuilt candidate runtime passes provenance and native contents, but128
object hashes differ from the old archive because compiler generation also
changed. A fresh pre-fix runtime control will use the same compiler before
speed is attributed. Source audit and all receipts are indexed in
`docs/investigations/tuple-publication-noop-quadratic-scan.md`.

Latest full compiler result remains v80 Stage2=566.617s versus Stage1=185.70s.
This row does not claim stage parity, GC4-linear construction or fixed point.
