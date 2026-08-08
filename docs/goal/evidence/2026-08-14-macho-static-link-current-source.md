# LINK-P1-MACHO-LINK-STATIC — current-source closure

Mode: host pcc Mach-O relocatable linker/archive selector on Darwin arm64.

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_macho_link_relocatable.py \
  tests/python/test_macho_archive.py
17 passed in 1.19s
```

The finite row boundary is closed: symbol resolution, deterministic BSD
archive repeated-scan member selection, section/symbol/relocation rebasing,
local/external collision handling and malformed-input rejection pass with the
retained system-linker differentials.

This does not claim executable-container/dyld ownership, default-link route,
incremental linking or parallel linking.  Those remain separate task rows.
