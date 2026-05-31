# Investigation Workflow (mandatory for any non-trivial bug)

Moved out of `AGENTS.md` to keep the always-loaded startup file under the
context threshold. The enforceable guardrails (when to open one, the regen
command, and the hard rules) still live inline in `AGENTS.md > Investigation
Workflow`; this file holds the full mandatory-sections template, the three
modes, and the rationale. Read it before opening or continuing an
investigation under `docs/investigations/`.

When a problem deserves a written investigation — anything more involved
than a one-line code fix — follow this workflow. The structure is mandatory
so the next agent can pick up the file without re-reading the chat log.

### Discovering prior investigations

Before opening a new investigation, scan
[`docs/investigations/INDEX.md`](docs/investigations/INDEX.md) — it lists
every investigation with a one-line summary, grouped by topic (codegen /
self-backend / pcc1-stage / bootstrap / gc / python-self-host / python /
lua / projects / llvm / other). If the symptom matches an existing entry,
read that file first and either:

- continue it via a `## Update` block (Mode 2 below), or
- write a new file that links to it as the predecessor.

After adding or editing any `docs/investigations/*.md` file, regenerate
the index:

```bash
env -u LC_ALL uv run python scripts/regen_investigations_index.py
```

The index is committed; the regen script is idempotent and only rewrites
`INDEX.md`.

### File location & naming

- One investigation = one file under `docs/investigations/<topic>.md`.
- Topic slug must be specific
  (`pcc1-stage2-lift-expr-raw-value-leak.md`, **not** `bug.md`).
- Existing files are historical record — never delete or rewrite them.
  When superseding a prior finding, link to its file from the new one and
  add a `## Status: superseded by <doc>` paragraph.
- `docs/investigations/` (and its `INDEX.md`) is the source of truth —
  not any list embedded in `AGENTS.md` or `README.md`.

### Mandatory sections

```
# Investigation: <one-line summary>

## Status
<active|resolved|superseded by <other doc>>

## Problem Description
<English statement of the user-reported problem (translated faithfully
if originally in another language), OR an explicit reduction of what
we are trying to confirm. Preserve the user's exact wording only as
an inline quote when the phrasing itself is part of the evidence
(e.g. distinctive vocabulary that future grep should match).>

## Repro
<the smallest deterministic command sequence that reproduces the
bug, including expected exit code / log line / backtrace marker>

## Test [CONFIRMED|N/A]
<test cases or harnesses that gate the fix; mark [CONFIRMED] only
after you have run them yourself and observed the failure>

## Proposals
- No.1 <title>     [CONFIRMED|DENIED|pending]
- No.2 <title>     ...

## No.1 <title>
### Code Change
<diff or description of the smallest change that implements the proposal>
### CONFIRMED|DENIED|DENIED BY USER
<comprehensive explanation of why it works, or why it does not.
Include the test command and observed result.>

## Report (only when the investigation is closing)
<which CONFIRMED proposal landed, comparative pros/cons against
DENIED ones, links to the commits, and any follow-up issues opened>
```

### Modes

Map any incoming user request to one of three:

1. **Repro** — fresh problem. Create the file, stop only when
   `## Test [CONFIRMED]` is observed. Do not skip `## Repro` even if the
   problem looked obvious.
2. **Continue** — additional info. Append a new `## Update` block to
   `## Problem Description`; do not rewrite history. If the user
   disagrees with a `[CONFIRMED]`, change it to `[DENIED]` with
   `### DENIED BY USER` and a written reason.
3. **Report** — closing summary. Add `## Report`. Do not delete proposals
   that turned out wrong; their `### DENIED` paragraphs are the record of
   what was ruled out.

### Hard rules

- **Agents must not commit unless the user explicitly asks.** The save/commit
  sequence below is guidance for a human/user-driven workflow, not permission
  for an autonomous agent to run `git commit`.
- **Save before each experiment** in user-driven workflows: commit the
  investigation file plus the smallest gating test, run the proposal, record
  outcome, commit again. Don't stack unverified edits in shared codegen — see
  *Debugging Playbook §9*.
- **One proposal at a time.** Pick the first `pending` proposal, run to
  verdict. If you cannot pick a verdict, write `### DENIED — incomplete`
  with the open question and stop.
- **No silent fixes.** A behavioral change in source while investigating
  must show up as a `### Code Change` under the proposal that motivated
  it. No "while I was in there" cleanup.
- **Test = observation, not optimism.** Mark `## Test [CONFIRMED]` only
  after the failure has been observed under the listed command.
- **Do not fork the report.** Two distinct bugs → two investigation files,
  linked. One file does not carry two storylines.
- **Status is final.** Once `## Status` is `resolved` or `superseded by
  <doc>`, do not edit the body again.

### Status enforcement

`## Status` is mandatory. To audit the directory:

```bash
for f in docs/investigations/*.md; do
  rg -q '^## Status' "$f" || echo "MISSING ## Status: $f"
done
```

### Why this matters

`docs/investigations/` is the long-term institutional memory for
flaky-self-host / heap-corruption / codegen-parity bugs. The structure costs
~10 minutes per investigation; skipping it has cost this project literal weeks
of duplicated diagnosis (a second agent re-deriving a first agent's findings
because the original never got a `## Status` line).
