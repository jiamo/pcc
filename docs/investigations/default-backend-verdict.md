# Default backend verdict

Default: Backend #0 — `refcount-cycle`

Backend #0 remains default because it is the least surprising semantic baseline; Backend #1-#4 remain selectable production backends.

| backend | name | default | pros | cons |
|---:|---|---|---|---|
| 0 | refcount-cycle | True | deterministic cleanup; lowest semantic risk | not compacting |
| 1 | incremental-tricolor | False | bounded work | not concurrent |
| 2 | concurrent-mark-sweep | False | worker/assist path | threaded build needed for full benefit |
| 3 | generational-minor-major | False | fast bump allocation | remembered-set complexity |
| 4 | colored-relocating | False | relocation semantics | highest complexity |
