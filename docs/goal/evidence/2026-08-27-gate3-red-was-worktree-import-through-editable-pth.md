# Gate #3 red was worktree-import contamination, not a HEAD defect

Date: 2026-08-27
Task: `PY-P1-HEAD-SHIPS-THREE-RED-FRONTEND-GATES` (third gate)
Gate: `tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_compiled_pcc_multi_can_compile_toy_module`
Claim level: snapshot-pure manual reproduction of both test steps, green;
mechanism of the earlier red proven by reading the import chain. The exact
152 s failure output was never captured (dots-only first run), so the red's
final exception is attributed by mechanism, not by traceback.

## Snapshot-pure arm (HEAD 47c9b7d7, fresh `git archive` snapshot)

With the subprocess import pinned to the snapshot (`PYTHONPATH=$SNAP`) and the
pytest arm's own env (`PCC_PYTHON_IR_PASSES=off`), runtime archive pinned:

```text
pair build (pcc_multi.py + pipeline.py, --python-libpython off)  rc=0  (54 MB)
toy compile via the compiled pair                                rc=0
banned strings (pcc cpy error / Exception ignored / TemporaryDirectory)  0
toy run                                                          rc=0  "123\nok"
```

HEAD's gate substance is GREEN. The earlier "red at pure HEAD" verdict for
THIS gate is withdrawn; the 2x2 bisect's snapshot arm was not actually
snapshot-pure for this test (mechanism below). Gates #1/#2 (in-process) were
genuinely red and are already fixed.

## Mechanism

1. `scripts/pcc_multi.py` has NO sys.path pin — it does `from pcc.extern
   import ...` and trusts the interpreter.
2. The venv's `_editable_impl_python_cc.pth` appends the DEVELOPMENT worktree
   (`/Users/jiamo/my/pcc`) to sys.path in EVERY interpreter started from that
   venv.
3. The test spawns `sys.executable <repo_root>/scripts/pcc_multi.py`.
   `repo_root` follows the test file (snapshot), but the subprocess's `import
   pcc` resolves through the .pth to the WORKTREE — which was mid-refactor.
   conftest's root insertion protects the test process only, never the
   subprocess.
4. Independently, the build step's 180 s subprocess timeout only holds warm:
   `PCC_PYTHON_IR_PASSES=off` shapes key the object cache separately, and a
   cold build measured ~500 s.

## Harness fix (same commit-unit as this note)

The test now pins the subprocess `PYTHONPATH` to its own `repo_root` and
carries a cold-tolerant 600 s timeout. In the ordinary worktree run the pin is
an identity change (the .pth already points there); in a snapshot it makes the
gate faithful to the tree it lives in.

## Rule confirmed (already in memory as the uv-run import trap)

A subprocess-spawning test is only as snapshot-pure as the subprocess's OWN
import resolution. Auditing the pytest process is not enough.
