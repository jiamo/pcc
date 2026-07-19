# 2026-07-16 first-class optional GPU owner-backend contract

Task: `GPU-P0-OWNER-BACKEND-CONTRACT`

## Result

`docs/design/pcc-gpu-owner-backends.md` now permits an explicitly selected GPU
provider to become the actual execution owner while pcc retains semantic, ABI,
resource-lifetime, diagnostic, and claim ownership.  This corrects the prior
over-broad reading of "oracle rather than owner": oracle-only remains the
current implementation state, not a permanent prohibition.

The contract defines:

- exact `pcc-metal` and optional `tvm-tilelang` owner labels;
- requested-owner = actual-owner and `fallback_used=false` invariants;
- a common validate/compile/package/launch/synchronize/destroy driver boundary;
- pinned provider identity, explicit pass pipeline, canonical IR/artifact
  hashes, and separate launcher/provider libpython dependency reporting;
- pcc-owned packed arguments, buffers, fences, and fence-safe release;
- staged Level 4 device result, Level 5 pcc1, and Level 6 five-GC gates;
- distinct source-subset, cpython-compat package, pcc-metal owner, and
  tvm-tilelang owner claims.

The implementation work is separately executable as:

- `GPU-P0-PCC-METAL-OWNER-DRIVER`
- `GPU-P0-TVM-TILELANG-OWNER-DRIVER`

## Claim boundary

This is a completed design contract, not implementation evidence.  The
existing TVM/TileLang path remains oracle/source-subset-only until the new
owner-provider rows pass their real device, no-fallback, pcc1, dependency, and
five-GC gates.

