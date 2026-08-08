# README Mermaid architecture diagram

- Task: `DOC-P2-README-ARCHITECTURE-MERMAID`
- Date: 2026-08-12
- Source identity: `ed6f1b30ebcc7f60f1eb84fd41fcd2d075d00743`
- Worktree state: dirty before this slice; unrelated in-flight README, task-board,
  book, compiler, stdlib, and test changes were preserved.

## Changed behavior

The existing `README.md` Architecture section now contains a GitHub-native
Mermaid `flowchart` instead of the previous six-line text pipeline. The diagram
keeps the main CPU route readable while showing the boundaries that materially
change pcc's execution claims:

- mature C and experimental typed-Python frontends converge on native LLVM IR;
- LLVM/LLVM-CAPI and the experimental LLVM-free self backend remain distinct;
- the Python native link consumes the migrating pcc-Python runtime and one of
  five selectable GC backends, with libpython shown only as an explicit optional
  compatibility edge;
- the `pcc0/host -> pcc1 -> pcc2 -> pcc3` fixed-point contract is visible;
- the experimental kernel-only Metal route is a separate bounded subgraph, with
  TVM/TIRx/TileLang labeled as reference shapes rather than runtime owners.

The adjacent layer/owner table and all unrelated README edits remain in place.

## Gates and observed results

Task-board validation:

```bash
gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate
```

Result: `OK: 262 tasks validated`.

Real browser-backed Mermaid render, using temporary dependencies outside the
repository and the installed Chrome binary:

```bash
PUPPETEER_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  gtimeout 60s /tmp/pcc-mermaid-verify.LDXypm/node_modules/.bin/mmdc \
  --input README.md \
  --output /tmp/pcc-mermaid-verify.LDXypm/rendered/README-final.md \
  --artefacts /tmp/pcc-mermaid-verify.LDXypm/rendered \
  --outputFormat png --scale 1 --quiet
```

Result: Mermaid CLI `11.16.0` exited 0 and produced
`README-final-1.png`. Visual inspection confirmed that the C and Python
frontends remain in the main top-to-bottom pipeline, the runtime and GC inputs
join only the Python native link, and the accelerator path remains visibly
separate. A second parser inspection reported `flowchart-v2`, 23 vertices, 26
edges, and the two expected subgraphs (`CPU compiler and runtime path` and
`Bounded accelerator path`).

Whitespace/error check:

```bash
gtimeout 30s git diff --check -- README.md docs/goal/task-board.yaml \
  docs/goal/evidence/2026-08-12-readme-architecture-mermaid.md
```

Result: exit 0 with no output.

## Supported claim

At this dirty-worktree source identity, the README contains a Mermaid architecture
overview that parses and renders with a current Mermaid engine and explicitly
labels pcc's mature, experimental, optional, oracle-only, and in-progress
boundaries.

## Not proven

- This documentation-only slice does not change or re-prove compiler, runtime,
  bootstrap, GC, package, GUI, GPU-hardware, or zero-libc behavior.
- The diagram is an orientation view, not an exhaustive call graph or repository
  map; the adjacent table and `docs/system-architecture.md` retain that detail.
- A remote GitHub page was not changed or rendered in this slice. The diagram
  uses GitHub-supported core `flowchart`, `subgraph`, solid-edge, dotted-edge,
  and quoted-label syntax; publication still depends on pushing this worktree.
