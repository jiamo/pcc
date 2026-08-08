# LINK-P1-MACHO-CODESIGN — pcc emits the ad-hoc signature itself

Mode: host pcc, Darwin arm64. `pcc/backend/macho_codesign.py`; no
`codesign(1)` in the signing path.

## Why the row exists

arm64 macOS refuses to run unsigned binaries. Without this, "pcc's own link
path" would still end in a shell-out to `codesign`, and the ownership claim
would be false at the last step.

## What landed

`build_signature()` / `resign()` produce the linker-style embedded signature:

```text
LC_CODE_SIGNATURE -> SuperBlob (big-endian)
  CSMAGIC_EMBEDDED_SIGNATURE, 1 slot -> CodeDirectory @ +20
CodeDirectory v0x20400, flags CS_ADHOC|CS_LINKER_SIGNED
  identifier string, then one SHA-256 per 4096-byte page of the file from
  offset 0 to codeLimit (= the signature's own file offset; last page partial)
```

`resign()` re-signs a *changed* binary: read the existing CodeDirectory for
the parameters that describe the binary (identifier, exec-segment base/limit/
flags, version, flags), patch `LC_CODE_SIGNATURE.datasize` and `__LINKEDIT`'s
`filesize` for the new blob size, and only then hash — the load commands live
in page 0, inside the signed range, so patch-before-hash is correctness, not
tidiness.

## Two ld details found by differential, not by reading docs

- The **CodeDirectory's own `length` is exact, but the file region it occupies
  is padded to 8 bytes**, and `LC_CODE_SIGNATURE.datasize` names the padded
  size. Measured: a 399-byte blob is stored as 400, a 401-byte one as 408.
  An earlier 16-byte guess produced exactly the observed `__LINKEDIT.filesize`
  divergence (0x2b0 vs ld's 0x2a8), which is how the real rule was pinned.
- `nSpecialSlots == 0` is what makes this a *linker* signature; the writer
  refuses anything else rather than pretending to model developer-ID layouts.

## Evidence (tests/python/test_macho_codesign.py, 4 passed)

1. **Byte identity with ld**: re-signing an untouched ld-signed binary
   reproduces ld's bytes exactly — every header field and all nine page
   hashes. This is a fixed-point test: any divergence in any field fails.
2. **codesign(1) + the kernel**: after pcc *changes* the binary (a cstring
   edit that invalidates a hashed `__TEXT` page) and re-signs it,
   `codesign --verify --strict` accepts it and the binary runs with the new
   behavior. A negative control first proves `codesign` rejects the edited,
   un-resigned binary — without it the test could pass vacuously.
3. Re-signing with a *different* identifier still verifies and runs.
4. Fail-closed: an object file (no LC_CODE_SIGNATURE) and a signature with
   special slots both raise `CodesignError`.

## What this does not claim

Ad-hoc / linker signatures only: no developer-ID, no entitlements, no
requirements blob, no special slots (Info.plist, resources), no notarization.
Those are outside what a linker emits and are refused rather than
approximated.
