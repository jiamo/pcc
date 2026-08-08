# Upstream migration ledger

`upstream.json` is the machine-readable upstream pin. Every non-mechanical PCC
Harness implementation commit adds exactly one ordered Markdown record under
`commits/`. Copy `ENTRY_TEMPLATE.md`; do not invent another format. A dirty
implementation slice uses a unique `pending:<slice>` identity and must be the
last entry. Replace it with the full PCC commit hash when that slice is
committed.

Run the Python-only gate before commit:

```sh
gtimeout 60s env -u LC_ALL uv run python projects/harness/migration/validate_ledger.py
```

The validator reads only the ledger, upstream pin, task board and Git metadata.
It does not scan settings, environment files, credentials or build outputs.
It rejects missing committed or dirty-worktree entries, duplicate identities,
non-contiguous order, malformed fields, unknown tasks, a stale upstream head,
and a stale `pending` entry.

One PCC commit may cover multiple upstream commits when they form one coherent
behavioral slice. One upstream commit may map to multiple PCC commits when the
port needs separate core, GUI, and platform changes. The ledger records the
mapping rather than forcing histories to have the same shape.

`Upstream range` uses inclusive full commit hashes. Use `not-applicable` only
for a PCC-native facility with a concrete `Native-only rationale`. Every entry
names changed upstream/PCC domains, test commands plus results, GUI impact and
evidence status, and remaining differences. GUI changes may be recorded as
`PENDING` while parity work remains open, but that missing evidence must also
remain an explicit task boundary.

Do not advance `last_audited_commit` merely because a remote commit exists.
Advance it after every intervening upstream commit has been classified as
migrated, intentionally inapplicable, or represented by a ready PCC task.
