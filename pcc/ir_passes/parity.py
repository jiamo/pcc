"""IR parity harness: compare pcc IR passes against upstream ``opt``.

Upstream reference:

- LLVM's legacy ``opt`` driver and the new pass manager entry point:
  ``/tmp/llvm-src/llvm-20.1.8.src/tools/opt/opt.cpp``
- The pass-pipeline parser that drives ``-passes=...``:
  ``/tmp/llvm-src/llvm-20.1.8.src/lib/Passes/PassBuilder.cpp``

The harness is deliberately small. It shells out to the system ``opt``
binary (detected with ``which opt``) so that the upstream side of every
parity assertion is literally an unmodified LLVM 20.1.8 build. On our
side we run the same input through an :class:`IRPassManager`. The two
outputs are then compared using a small set of structural projections:

- normalized IR text (metadata and header noise stripped),
- function / basic-block / instruction counts,
- CFG edge sets (successor lists per block),
- per-opcode instruction histograms.

These projections cover the kinds of "did we match upstream?" questions
that matter for the pass families in Phase 3–7 of the master plan.
Runtime comparison is intentionally out of scope here: the Python
runtime cannot meaningfully race upstream ``opt``, and the benchmark
rig already tracks executable-level timings.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

import llvmlite.binding as llvm

from pcc.passes.llvm_text_pipeline import find_opt_binary

from .manager import AnalysisManager, IRPassManager, ModulePass, PreservedAnalyses


# ---------------------------------------------------------------------------
# Running upstream ``opt``
# ---------------------------------------------------------------------------


class OptNotFoundError(RuntimeError):
    """Raised when no ``opt`` binary is on PATH."""


def _locate_opt(explicit: str | None = None) -> str:
    """Return a path to an ``opt`` binary.

    Looks at ``explicit`` first, then for an LLVM version matching llvmlite.
    Raises
    :class:`OptNotFoundError` if nothing is found — parity tests that
    need ``opt`` should use collection-time ``pcc_gate`` when unavailable.
    """
    if explicit is not None:
        return explicit
    found = find_opt_binary()
    if found is None:
        raise OptNotFoundError(
            "no LLVM 'opt' matching llvmlite; parity harness cannot run"
        )
    return found


@dataclass
class OptInvocation:
    """Captured result of one ``opt -passes=...`` invocation."""

    ir_text: str
    stderr: str
    passes: str
    returncode: int


def run_upstream_opt(
    ir_text: str,
    passes: str,
    *,
    opt_path: str | None = None,
    extra_args: Iterable[str] = (),
) -> OptInvocation:
    """Run ``opt -passes=<passes> -S`` on the given IR text.

    Equivalent to ``echo <ir> | opt -passes=PASSES -S``. Returns both
    the post-pass IR text and stderr so callers can assert on stats or
    warnings when they care.
    """
    binary = _locate_opt(opt_path)
    cmd = [binary, f"-passes={passes}", "-S", *extra_args]
    proc = subprocess.run(
        cmd,
        input=ir_text,
        capture_output=True,
        text=True,
        check=False,
    )
    return OptInvocation(
        ir_text=proc.stdout,
        stderr=proc.stderr,
        passes=passes,
        returncode=proc.returncode,
    )


def run_pcc_ir_pass(
    ir_text: str,
    pass_: ModulePass,
    *,
    am: AnalysisManager | None = None,
) -> tuple[str, PreservedAnalyses]:
    """Parse ``ir_text`` via llvmlite, run ``pass_``, return the new IR.

    The pass is wrapped in a single-entry :class:`IRPassManager`. We
    return the preserved-analyses result too so tests can assert on
    what the pass claims to preserve (useful for analysis-correctness
    tests).

    Passes that rewrite IR textually (because llvmlite's
    :class:`~llvmlite.binding.ModuleRef` is read-only from Python)
    may expose the post-pass IR via a ``rewritten_ir`` attribute —
    when set, that string is returned in preference to the
    unmodified module's serialization. Passes that mutate in place
    via :mod:`llvmlite.ir` should leave the attribute unset.
    """
    module = llvm.parse_assembly(ir_text)
    module.verify()
    pm = IRPassManager().add(pass_)
    pa = pm.run(module, am)
    rewritten = getattr(pass_, "rewritten_ir", None)
    if rewritten is not None:
        return rewritten, pa
    return str(module), pa


# ---------------------------------------------------------------------------
# IR normalization
# ---------------------------------------------------------------------------


_HEADER_RE = re.compile(
    r"""^
    (?:
        \s*
        (?:
            ;\s*ModuleID\s*=.*
            | source_filename\s*=.*
            | target\s+(?:datalayout|triple)\s*=.*
        )
        \s*
    )$
    """,
    re.VERBOSE | re.MULTILINE,
)

# llvmlite insists on numbered anonymous values; upstream opt often
# numbers the same values differently. Strip purely numeric SSA names
# so comparisons focus on shape, not specific integer labels.
_NUM_NAME_RE = re.compile(r"%(\d+)\b")
_MD_COMMENT_RE = re.compile(r"^\s*;[^\n]*\n?", re.MULTILINE)
_ATTR_GROUP_RE = re.compile(r"attributes\s+#\d+\s*=\s*\{[^}]*\}")
_FN_ATTR_REF_RE = re.compile(r"\s#\d+\b")
_LABEL_COMMENT_RE = re.compile(r"^(\s*[\w\.\-]+:)\s*;[^\n]*$", re.MULTILINE)


def _strip_llvm_boilerplate(text: str) -> str:
    text = _HEADER_RE.sub("", text)
    text = _MD_COMMENT_RE.sub("", text)
    text = _LABEL_COMMENT_RE.sub(r"\1", text)
    text = _ATTR_GROUP_RE.sub("", text)
    text = _FN_ATTR_REF_RE.sub("", text)
    return text


def _replace_numeric_names_in_line(
    line: str,
    mapping: dict[str, str],
    counter: int,
) -> tuple[str, int]:
    out: list[str] = []
    pos = 0
    for match in _NUM_NAME_RE.finditer(line):
        key = match.group(1)
        if key not in mapping:
            mapping[key] = f"n{counter}"
            counter += 1
        out.append(line[pos : match.start()])
        out.append(f"%{mapping[key]}")
        pos = match.end()
    out.append(line[pos:])
    return "".join(out), counter


def _normalize_numeric_names(text: str) -> str:
    """Rewrite ``%<digits>`` names densely per function.

    Within a function, the first numeric name seen becomes ``%n0``,
    the second ``%n1``, etc. This is enough to paper over the routine
    renumbering differences between llvmlite's serializer and upstream
    ``opt``.
    """
    out: list[str] = []
    mapping: dict[str, str] = {}
    counter = 0
    for line in text.splitlines(keepends=True):
        if line.startswith("define "):
            mapping.clear()
            counter = 0
        replaced, counter = _replace_numeric_names_in_line(line, mapping, counter)
        out.append(replaced)
    return "".join(out)


def normalize_ir(text: str) -> str:
    """Return a canonical form of ``text`` suitable for structural diff.

    - drops ``ModuleID`` / ``source_filename`` / ``target`` lines,
    - drops standalone ``;`` comment lines,
    - drops ``attributes #N = { ... }`` groups and ``#N`` callsite refs,
    - renumbers anonymous numeric SSA names densely per function,
    - collapses runs of blank lines.
    """
    stripped = _strip_llvm_boilerplate(text)
    renamed = _normalize_numeric_names(stripped)
    lines = [ln.rstrip() for ln in renamed.splitlines() if ln.strip()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Structural IR projections
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FunctionShape:
    """Shape projection of a single function."""

    name: str
    block_count: int
    instruction_count: int
    opcode_histogram: tuple[tuple[str, int], ...]
    cfg_edges: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ModuleShape:
    """Shape projection of a module — list of function shapes plus globals."""

    functions: tuple[FunctionShape, ...]
    global_count: int

    def function_by_name(self, name: str) -> FunctionShape | None:
        for fn in self.functions:
            if fn.name == name:
                return fn
        return None


_OPCODE_LINE_RE = re.compile(
    r"""
    ^\s*
    (?:%[\w\.]+\s*=\s*)?
    (?P<opcode>[a-zA-Z_][\w.]*)
    """,
    re.VERBOSE,
)

# Instruction keywords that are NOT opcodes — filter noise.
_NON_OPCODE_WORDS = {
    "define",
    "declare",
    "target",
    "source_filename",
    "attributes",
    "module",
    "to",
    "align",
}


def _iter_function_blocks(fn: llvm.ValueRef):
    return list(fn.blocks)


def _block_label(block: llvm.ValueRef) -> str:
    name = block.name
    if name:
        return name
    return str(id(block))


def _block_terminator(block: llvm.ValueRef) -> llvm.ValueRef | None:
    last = None
    for inst in block.instructions:
        last = inst
    return last


_TERM_TARGET_RE = re.compile(r"label %([\w\.]+)")


def _function_shape(fn: llvm.ValueRef) -> FunctionShape:
    block_names: list[str] = []
    opcode_counts: Counter[str] = Counter()
    edges: list[tuple[str, str]] = []
    inst_count = 0

    for block in _iter_function_blocks(fn):
        label = _block_label(block)
        block_names.append(label)
        term = None
        for inst in block.instructions:
            inst_count += 1
            opcode = inst.opcode
            if opcode:
                opcode_counts[opcode] += 1
            term = inst
        if term is not None:
            for succ_label in _TERM_TARGET_RE.findall(str(term)):
                edges.append((label, succ_label))

    return FunctionShape(
        name=fn.name,
        block_count=len(block_names),
        instruction_count=inst_count,
        opcode_histogram=tuple(sorted(opcode_counts.items())),
        cfg_edges=tuple(sorted(edges)),
    )


def module_shape(ir_text: str) -> ModuleShape:
    """Parse ``ir_text`` and return its structural shape."""
    module = llvm.parse_assembly(ir_text)
    module.verify()
    shapes: list[FunctionShape] = []
    for fn in module.functions:
        if fn.is_declaration:
            continue
        shapes.append(_function_shape(fn))
    shapes.sort(key=lambda s: s.name)
    globals_count = sum(1 for _ in module.global_variables)
    return ModuleShape(functions=tuple(shapes), global_count=globals_count)


# ---------------------------------------------------------------------------
# Diff report
# ---------------------------------------------------------------------------


@dataclass
class IRDiff:
    """Summary of the differences between two IR modules.

    Empty lists everywhere means the modules match under every
    projection the harness checks.
    """

    text_equal: bool = False
    normalized_text_equal: bool = False
    missing_functions: list[str] = field(default_factory=list)
    extra_functions: list[str] = field(default_factory=list)
    function_diffs: list["FunctionDiff"] = field(default_factory=list)
    global_count_diff: tuple[int, int] | None = None

    def is_equivalent(self) -> bool:
        """True when no projection showed any difference."""
        return (
            self.normalized_text_equal
            and not self.missing_functions
            and not self.extra_functions
            and not self.function_diffs
            and self.global_count_diff is None
        )


@dataclass
class FunctionDiff:
    name: str
    block_count: tuple[int, int] | None = None
    instruction_count: tuple[int, int] | None = None
    opcode_diff: dict[str, tuple[int, int]] = field(default_factory=dict)
    cfg_edges_missing: list[tuple[str, str]] = field(default_factory=list)
    cfg_edges_extra: list[tuple[str, str]] = field(default_factory=list)


def _diff_function(
    pcc: FunctionShape, opt_: FunctionShape
) -> FunctionDiff | None:
    fd = FunctionDiff(name=pcc.name)
    changed = False

    if pcc.block_count != opt_.block_count:
        fd.block_count = (pcc.block_count, opt_.block_count)
        changed = True
    if pcc.instruction_count != opt_.instruction_count:
        fd.instruction_count = (pcc.instruction_count, opt_.instruction_count)
        changed = True

    pcc_hist = dict(pcc.opcode_histogram)
    opt_hist = dict(opt_.opcode_histogram)
    for opcode in set(pcc_hist) | set(opt_hist):
        a = pcc_hist.get(opcode, 0)
        b = opt_hist.get(opcode, 0)
        if a != b:
            fd.opcode_diff[opcode] = (a, b)
            changed = True

    pcc_edges = set(pcc.cfg_edges)
    opt_edges = set(opt_.cfg_edges)
    missing = sorted(opt_edges - pcc_edges)
    extra = sorted(pcc_edges - opt_edges)
    if missing:
        fd.cfg_edges_missing = missing
        changed = True
    if extra:
        fd.cfg_edges_extra = extra
        changed = True

    return fd if changed else None


def compare_ir(pcc_ir: str, opt_ir: str) -> IRDiff:
    """Compare two IR texts across text / shape / CFG projections."""
    diff = IRDiff()
    diff.text_equal = pcc_ir == opt_ir
    diff.normalized_text_equal = normalize_ir(pcc_ir) == normalize_ir(opt_ir)

    pcc_mod = module_shape(pcc_ir)
    opt_mod = module_shape(opt_ir)

    if pcc_mod.global_count != opt_mod.global_count:
        diff.global_count_diff = (pcc_mod.global_count, opt_mod.global_count)

    pcc_names = {fn.name for fn in pcc_mod.functions}
    opt_names = {fn.name for fn in opt_mod.functions}
    diff.missing_functions = sorted(opt_names - pcc_names)
    diff.extra_functions = sorted(pcc_names - opt_names)

    for pcc_fn in pcc_mod.functions:
        opt_fn = opt_mod.function_by_name(pcc_fn.name)
        if opt_fn is None:
            continue
        fd = _diff_function(pcc_fn, opt_fn)
        if fd is not None:
            diff.function_diffs.append(fd)

    return diff


# ---------------------------------------------------------------------------
# End-to-end parity helper
# ---------------------------------------------------------------------------


@dataclass
class ParityReport:
    """Everything the harness produces for one parity run."""

    input_ir: str
    pcc_ir: str
    opt_ir: str
    preserved: PreservedAnalyses
    diff: IRDiff
    opt_stderr: str = ""

    @property
    def is_equivalent(self) -> bool:
        return self.diff.is_equivalent()


def assert_ir_parity(
    ir_text: str,
    pcc_pass: ModulePass,
    opt_passes: str,
    *,
    opt_path: str | None = None,
    am: AnalysisManager | None = None,
) -> ParityReport:
    """Run ``pcc_pass`` and ``opt -passes=opt_passes`` on the same IR and diff.

    Callers use this in tests; the returned :class:`ParityReport`
    reports both the raw IR on each side and a structural diff. The
    helper does not raise on mismatch — tests decide what tolerance
    they want (some passes are expected to be a strict subset).
    """
    pcc_out, preserved = run_pcc_ir_pass(ir_text, pcc_pass, am=am)
    opt_result = run_upstream_opt(ir_text, opt_passes, opt_path=opt_path)
    diff = compare_ir(pcc_out, opt_result.ir_text)
    return ParityReport(
        input_ir=ir_text,
        pcc_ir=pcc_out,
        opt_ir=opt_result.ir_text,
        preserved=preserved,
        diff=diff,
        opt_stderr=opt_result.stderr,
    )
