# Text FileCheck pilot focused evidence — 2026-08-14

Mode: host matcher unit tests and one x86_64 self-backend asm pilot.

Results: 4 matcher cases passed, followed by the converted mixed
word/pointer struct-global node passing. The tests exercise ordered anchors,
adjacent-line requirements, forbidden prefix/inter-match/suffix regions,
malformed specifications and deterministic diagnostics.

The wider self-backend suite and any broader migration remain open; regex,
captures and LLVM FileCheck binary parity are outside this pilot.
