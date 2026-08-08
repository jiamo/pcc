# NativeObject fast-path focused evidence — 2026-08-14

Mode: host-side codec, indexed view, relocatable/final-link boundary tests.

The first fail-fast run stopped because the test helper named a section
`__TEXT,__text` but left its flags at the data-section default. The final
linker correctly rejected `_main` outside an executable section. The fixture
now uses `TEXT_SECTION_FLAGS`; production validation was not relaxed.

Final result: 7 passed. The codec stores indexed symbols/relocations, the
owned final link does not reparse an internal Mach-O string table, internal
and external object boundaries produce the same image, external boundaries
materialize standard Mach-O, encoded native bytes require explicit decode,
and malformed indices/framing fail closed.

Real self-emitter/system-assembler parity, performance measurement and
pcc2/pcc3 byte identity remain open.
