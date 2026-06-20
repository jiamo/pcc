"""Shared, CPU-only, deterministic surrogates and mode-labeled skip taxonomy
for the vLLM-Metal / MiniMind first-slice gates.

This module is the ONLY place where the two gate files (test_vllm_metal_kv.py
and test_minimind_gpu.py) share behavior. It contains:

1. A ``mode-labeled skip taxonomy`` (present/absent -> SKIPPED_WITH_REASON) so
   both gates decide *run vs skip* the same way, and every skip carries an
   explicit, greppable reason string.

2. Two REAL, deterministic, CPU-only metadata surrogates:
     - ``KVBlockTable`` : a paged-attention block table with block-id,
       refcount, prefix-hash, pin/unpin and eviction. No GPU, no tensors,
       no attention math. It models the *bookkeeping* only.
     - ``resolve_minimind_mode`` / ``derive_minimind_config`` : MiniMind
       argument + device-mode resolution and the derived model shape
       (hidden_size / num_hidden_layers / intermediate_size / vocab_size).
       Mirrors the arithmetic in ``model/model_minimind.py`` and the device
       default ``"cuda:0" if torch.cuda.is_available() else "cpu"`` WITHOUT
       importing torch. This is metadata resolution, NOT model execution.

WHAT THIS MODULE DELIBERATELY DOES NOT DO (claim boundary):
- It does not run any GPU / Metal / MLX / MPS kernel.
- It does not run attention, matmul, training, or serving.
- It does not download weights or datasets.
- It does not build a pcc1 binary or compile anything.
- It makes NO throughput / scaling / accuracy claim.

The ``--python-libpython=off`` CPython-extension rejection boundary is asserted
through pcc's generic native-artifact linkage scanner.  The probe is real and
fail-closed, but remains a package-boundary check rather than a pcc1 execution
claim.  See docs/design/pcc-vllm-minimind-gates.md.

No ``if package == "numpy"`` (or any package) special-casing lives here: the
presence probe below is a generic ``importlib.util.find_spec`` sweep over a
data-driven list of names, treated uniformly.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pcc.package.linkage import linkage_report


# --------------------------------------------------------------------------
# Mode-labeled skip taxonomy
# --------------------------------------------------------------------------
#
# Every mode below maps to a stable label string used verbatim in skip
# reasons. MAIN's ``pytest -rs`` output can be grepped for these labels.
#
# The taxonomy has two axes:
#   * framework/device PRESENCE  (do we even have torch / mlx / vllm-metal?)
#   * execution MODE label       (cpu / torch-mps / mlx-metal / pcc-metal-kernel)
#
# A gate for a given mode runs ONLY when its required packages are present AND
# (for device modes) the device is actually available; otherwise it emits a
# SKIPPED_WITH_REASON carrying the exact missing dependency.

# Execution-mode labels (kept identical to the design doc taxonomy).
MODE_CPU = "cpu"
MODE_TORCH_MPS = "torch-mps"
MODE_MLX_METAL = "mlx-metal"
MODE_PCC_METAL_KERNEL = "pcc-metal-kernel"

# libpython / ABI compat labels (kept distinct on purpose).
LABEL_CPYTHON_COMPAT = "cpython-compat"      # extension built for CPython ABI
LABEL_PCC_NATIVE = "pcc-native"              # extension built for pcc-native ABI
LABEL_NO_LIBPYTHON = "no-libpython"          # --python-libpython=off boundary


def cpython_extension_rejection_report(
    tmp_path: Path, relative_path: str
) -> dict[str, object]:
    """Exercise the real fail-closed package boundary without compiling pcc1.

    ``relative_path`` is data, not a package special case.  It must carry a
    CPython extension suffix; the payload also records a libpython edge so the
    generic scanner must emit both PCC-PKG-003 and PCC-PKG-004.
    """
    artifact = tmp_path / relative_path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(
        b"\x7fELF\x00synthetic native artifact\x00/usr/lib/libpython3.13.dylib\x00"
    )
    return linkage_report(artifacts=[str(artifact)], abi_mode="pcc-native")


def _spec_present(name: str) -> bool:
    """Generic, uniform presence probe. No per-package special-casing."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        # A partially-installed / namespace-broken dep counts as absent.
        return False


def probe_packages(names: List[str]) -> Dict[str, bool]:
    """Return {name: present?} for a data-driven list, uniform treatment."""
    return {name: _spec_present(name) for name in names}


def torch_mps_available() -> Tuple[bool, str]:
    """Is a torch build with a usable MPS (Apple Metal) device importable?

    Returns (available, reason). ``reason`` is a SKIPPED_WITH_REASON string
    when not available. Never raises; never runs a kernel.
    """
    if not _spec_present("torch"):
        return False, "SKIPPED_WITH_REASON: torch absent (mode=torch-mps)"
    try:
        import torch  # noqa: PLC0415  (probe is intentionally lazy)
    except Exception as exc:  # pragma: no cover - env-dependent
        return False, f"SKIPPED_WITH_REASON: torch import failed ({exc!r}) (mode=torch-mps)"
    backends = getattr(torch, "backends", None)
    mps = getattr(backends, "mps", None) if backends is not None else None
    if mps is None:
        return False, "SKIPPED_WITH_REASON: torch has no mps backend (mode=torch-mps)"
    try:
        ok = bool(mps.is_available())
    except Exception as exc:  # pragma: no cover - env-dependent
        return False, f"SKIPPED_WITH_REASON: torch.mps probe failed ({exc!r}) (mode=torch-mps)"
    if not ok:
        return False, "SKIPPED_WITH_REASON: torch MPS device not available (mode=torch-mps)"
    return True, ""


def mlx_metal_available() -> Tuple[bool, str]:
    """Is the ``mlx`` (Apple MLX / Metal) framework importable?

    Returns (available, reason). Never runs a kernel.
    """
    if not _spec_present("mlx"):
        return False, "SKIPPED_WITH_REASON: mlx absent (mode=mlx-metal)"
    try:
        import mlx.core as _mx  # noqa: F401,PLC0415
    except Exception as exc:  # pragma: no cover - env-dependent
        return False, f"SKIPPED_WITH_REASON: mlx import failed ({exc!r}) (mode=mlx-metal)"
    return True, ""


def vllm_metal_surface() -> Tuple[bool, str]:
    """Is a current ``vllm-metal`` / ``mlx`` package surface present on macOS?

    Per deep-research: Apple Silicon GPU support in vLLM lives in the OUT-OF-TREE
    ``vllm-metal`` hardware plugin (MLX-based, unified memory). We probe both the
    plugin module name and its MLX dependency. Returns (present, reason).
    """
    names = ["vllm_metal", "vllm", "mlx"]
    present = probe_packages(names)
    if present.get("vllm_metal"):
        return True, ""
    # mlx-only still counts as "the Apple/Metal compute surface is here" but not
    # the vllm-metal plugin itself -> report precisely.
    missing = [n for n, ok in present.items() if not ok]
    return False, (
        "SKIPPED_WITH_REASON: vllm-metal plugin surface absent "
        f"(missing={missing}; mode=mlx-metal / vllm-metal)"
    )


# --------------------------------------------------------------------------
# KV-block metadata surrogate (vLLM-style paged-attention block table)
# --------------------------------------------------------------------------
#
# This is a REAL, asserted, CPU-only model of the *bookkeeping* vLLM performs:
#   - fixed-size blocks with stable block-ids
#   - reference counting (touch on reuse, drop on free)
#   - content-addressed prefix hashing for shared/cached blocks
#   - explicit pin / unpin
#   - eviction of refcount==0 blocks, LRU + longest-prefix-tail preference
#
# It intentionally holds NO KV tensor data. It proves the metadata invariants
# that a GPU/Metal collector would have to respect, without any GPU.


def prefix_block_hash(parent_hash: Optional[str], token_ids: Tuple[int, ...]) -> str:
    """Deterministic content hash: hash(prefix_hash + block_tokens).

    Mirrors vLLM automatic-prefix-caching identity. Pure, deterministic.
    """
    h = hashlib.sha256()
    h.update((parent_hash or "ROOT").encode("utf-8"))
    h.update(b"|")
    h.update(",".join(str(t) for t in token_ids).encode("utf-8"))
    return h.hexdigest()[:16]


@dataclass
class KVBlock:
    block_id: int
    block_size: int
    refcount: int = 0
    pinned: bool = False
    prefix_hash: Optional[str] = None
    token_ids: Tuple[int, ...] = ()
    last_touch_epoch: int = 0


class KVBlockTableError(RuntimeError):
    """Raised on an illegal block-table transition (double-free, over-unpin...)."""


@dataclass
class KVBlockTable:
    """Deterministic CPU-only paged-attention block table.

    No GPU. No tensors. Models block-id / refcount / prefix-hash / pin-unpin /
    eviction so downstream (a GPU collector) has a stable metadata contract.
    """

    num_blocks: int
    block_size: int = 16
    _epoch: int = 0
    _blocks: Dict[int, KVBlock] = field(default_factory=dict)
    _free_ids: List[int] = field(default_factory=list)
    _hash_index: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.num_blocks <= 0:
            raise KVBlockTableError("num_blocks must be positive")
        if self.block_size <= 0:
            raise KVBlockTableError("block_size must be positive")
        # Free queue is a list used tail-insert/head-pop, mirroring vLLM's
        # doubly linked free queue semantics deterministically.
        self._free_ids = list(range(self.num_blocks))
        self._blocks = {
            i: KVBlock(block_id=i, block_size=self.block_size) for i in range(self.num_blocks)
        }

    # -- epoch / clock -----------------------------------------------------
    def _tick(self) -> int:
        self._epoch += 1
        return self._epoch

    # -- allocation --------------------------------------------------------
    def free_count(self) -> int:
        return len(self._free_ids)

    def allocate(self, token_ids: Tuple[int, ...], parent_hash: Optional[str] = None) -> int:
        """Allocate (or reuse via prefix hash) a block; returns block_id.

        Reuse path: if the content hash already maps to a live block, TOUCH it
        (increment refcount, pull it out of the free queue) and return it.
        """
        phash = prefix_block_hash(parent_hash, token_ids)
        # Content-addressed reuse ("touch").
        existing = self._hash_index.get(phash)
        if existing is not None:
            blk = self._blocks[existing]
            self._touch(blk)
            return existing
        # Fresh allocation from the head of the free queue.
        if not self._free_ids:
            raise KVBlockTableError("no free blocks (OOM surrogate)")
        bid = self._free_ids.pop(0)
        blk = self._blocks[bid]
        old_hash = blk.prefix_hash
        if old_hash is not None and self._hash_index.get(old_hash) == bid:
            self._hash_index.pop(old_hash, None)
        blk.refcount = 1
        blk.prefix_hash = phash
        blk.token_ids = tuple(token_ids)
        blk.pinned = False
        blk.last_touch_epoch = self._tick()
        self._hash_index[phash] = bid
        return bid

    def _touch(self, blk: KVBlock) -> None:
        blk.refcount += 1
        blk.last_touch_epoch = self._tick()
        # A reused block that was sitting free returns to "in use".
        if blk.block_id in self._free_ids:
            self._free_ids.remove(blk.block_id)

    # -- reference counting ------------------------------------------------
    def incref(self, block_id: int) -> int:
        blk = self._require(block_id)
        blk.refcount += 1
        blk.last_touch_epoch = self._tick()
        return blk.refcount

    def free(self, block_id: int) -> int:
        """Drop one reference; when refcount hits 0 the block becomes evictable
        and its id is appended to the TAIL of the free queue (LRU order)."""
        blk = self._require(block_id)
        if blk.refcount <= 0:
            raise KVBlockTableError(f"double-free of block {block_id}")
        blk.refcount -= 1
        blk.last_touch_epoch = self._tick()
        if blk.refcount == 0:
            if blk.pinned:
                # Pinned blocks stay resident even at refcount 0.
                return blk.refcount
            # Tail insertion => oldest-freed evicted first (vLLM tail rule).
            if blk.block_id not in self._free_ids:
                self._free_ids.append(blk.block_id)
        return blk.refcount

    # -- pin / unpin -------------------------------------------------------
    def pin(self, block_id: int) -> None:
        blk = self._require(block_id)
        blk.pinned = True
        if blk.block_id in self._free_ids:
            self._free_ids.remove(blk.block_id)

    def unpin(self, block_id: int) -> None:
        blk = self._require(block_id)
        if not blk.pinned:
            raise KVBlockTableError(f"unpin of non-pinned block {block_id}")
        blk.pinned = False
        if blk.refcount == 0 and blk.block_id not in self._free_ids:
            self._free_ids.append(blk.block_id)

    # -- eviction ----------------------------------------------------------
    def evict_one(self) -> Optional[int]:
        """Evict the oldest-freed CACHED block (refcount==0, not pinned).

        Eviction reclaims a *cached* block's identity: pristine (never-cached)
        blocks need no eviction because they are already reusable, so they are
        skipped. The block scanned earliest in the free queue is the
        oldest-freed one (vLLM tail-insertion => head is oldest), giving LRU
        order. Invalidates the content-hash mapping. The evicted block stays in
        the free queue in place (now pristine and ready for reuse). Returns the
        evicted block_id, or None when nothing cached is evictable.
        """
        for bid in self._free_ids:
            blk = self._blocks[bid]
            if blk.refcount == 0 and not blk.pinned and blk.prefix_hash is not None:
                self._hash_index.pop(blk.prefix_hash, None)
                blk.prefix_hash = None
                blk.token_ids = ()
                blk.last_touch_epoch = self._tick()
                # Block remains free in place -- no churn, still reusable.
                return bid
        return None

    def cache_hit_rate(self, lookups: List[Tuple[Optional[str], Tuple[int, ...]]]) -> float:
        """Deterministic surrogate hit-rate over a lookup trace (metadata only)."""
        if not lookups:
            return 0.0
        hits = 0
        for parent, toks in lookups:
            if prefix_block_hash(parent, toks) in self._hash_index:
                hits += 1
        return hits / len(lookups)

    # -- internals ---------------------------------------------------------
    def _require(self, block_id: int) -> KVBlock:
        blk = self._blocks.get(block_id)
        if blk is None:
            raise KVBlockTableError(f"unknown block {block_id}")
        return blk

    def snapshot(self, block_id: int) -> KVBlock:
        return self._require(block_id)


# --------------------------------------------------------------------------
# MiniMind arg / device-mode resolution surrogate (CPU-only, no torch)
# --------------------------------------------------------------------------
#
# Mirrors:
#   trainer/train_pretrain.py --device default:
#       "cuda:0" if torch.cuda.is_available() else "cpu"
#   README line 284: CPU / MPS / CUDA are all selectable on Apple hardware.
#   model/model_minimind.py MiniMindConfig arithmetic:
#       intermediate_size = ceil(hidden_size * pi / 64) * 64
#       vocab_size default 6400
#
# We resolve WITHOUT importing torch, so the surrogate runs anywhere. When a
# framework/device is genuinely requested for a real run, the gate skips.


@dataclass(frozen=True)
class MiniMindMode:
    """Resolved execution mode for a MiniMind command shape."""

    label: str          # one of MODE_CPU / MODE_TORCH_MPS / MODE_MLX_METAL / MODE_PCC_METAL_KERNEL
    device: str         # e.g. "cpu", "mps", "cuda:0", "metal"
    requires_framework: Optional[str]  # "torch" / "mlx" / None
    real_run_possible: bool            # can this mode actually execute here?
    reason: str = ""    # SKIPPED_WITH_REASON when real_run_possible is False


def resolve_minimind_mode(requested_device: str) -> MiniMindMode:
    """Map a requested device string to a labeled MiniMindMode.

    ``requested_device`` values understood: 'cpu', 'mps', 'cuda' / 'cuda:N',
    'metal' (pcc-native Metal kernel intent). This is pure metadata; it never
    imports torch/mlx to *run*, only probes availability for real_run_possible.
    """
    dev = requested_device.strip().lower()

    if dev == "cpu":
        return MiniMindMode(
            label=MODE_CPU, device="cpu", requires_framework=None,
            real_run_possible=True,
        )

    if dev in ("mps", "torch-mps"):
        ok, reason = torch_mps_available()
        return MiniMindMode(
            label=MODE_TORCH_MPS, device="mps", requires_framework="torch",
            real_run_possible=ok, reason=reason,
        )

    if dev in ("mlx", "mlx-metal"):
        ok, reason = mlx_metal_available()
        return MiniMindMode(
            label=MODE_MLX_METAL, device="metal", requires_framework="mlx",
            real_run_possible=ok, reason=reason,
        )

    if dev in ("metal", "pcc-metal", "pcc-metal-kernel"):
        # pcc-native Metal kernel path: NOT claimable in this slice. It is only
        # a labeled intent; a real run needs a pcc1 build + Metal backend, which
        # this environment must not perform.
        return MiniMindMode(
            label=MODE_PCC_METAL_KERNEL, device="metal", requires_framework="pcc",
            real_run_possible=False,
            reason=(
                "SKIPPED_WITH_REASON: pcc-native Metal kernel execution is not "
                "claimed in this slice (needs pcc1 + Metal backend; mode=pcc-metal-kernel)"
            ),
        )

    if dev.startswith("cuda"):
        # No CUDA on Apple hardware; report precisely rather than pretend.
        return MiniMindMode(
            label="cuda", device=dev, requires_framework="torch",
            real_run_possible=False,
            reason="SKIPPED_WITH_REASON: cuda device not available on this host (mode=cuda)",
        )

    return MiniMindMode(
        label="unknown", device=dev, requires_framework=None,
        real_run_possible=False,
        reason=f"SKIPPED_WITH_REASON: unrecognized device '{requested_device}'",
    )


def default_minimind_device(cuda_available: bool) -> str:
    """Reproduce train_pretrain.py's default: cuda:0 if cuda else cpu.

    Pure function of a boolean so it is deterministic in tests.
    """
    return "cuda:0" if cuda_available else "cpu"


@dataclass(frozen=True)
class MiniMindConfigShape:
    """Derived model shape metadata (NOT an instantiated model)."""

    hidden_size: int
    num_hidden_layers: int
    use_moe: bool
    vocab_size: int
    num_attention_heads: int
    head_dim: int
    intermediate_size: int
    max_seq_len: int


def derive_minimind_config(
    hidden_size: int = 768,
    num_hidden_layers: int = 8,
    use_moe: bool = False,
    vocab_size: int = 6400,
    num_attention_heads: int = 8,
    max_seq_len: int = 340,
    head_dim: Optional[int] = None,
    intermediate_size: Optional[int] = None,
) -> MiniMindConfigShape:
    """Derive MiniMind config shape, mirroring model_minimind.py arithmetic.

    intermediate_size = ceil(hidden_size * pi / 64) * 64  (when not given)
    head_dim          = hidden_size // num_attention_heads (when not given)
    """
    if hidden_size <= 0 or num_hidden_layers <= 0 or num_attention_heads <= 0:
        raise ValueError("hidden_size / num_hidden_layers / num_attention_heads must be positive")
    hd = head_dim if head_dim is not None else hidden_size // num_attention_heads
    inter = (
        intermediate_size
        if intermediate_size is not None
        else math.ceil(hidden_size * math.pi / 64) * 64
    )
    return MiniMindConfigShape(
        hidden_size=hidden_size,
        num_hidden_layers=num_hidden_layers,
        use_moe=use_moe,
        vocab_size=vocab_size,
        num_attention_heads=num_attention_heads,
        head_dim=hd,
        intermediate_size=inter,
        max_seq_len=max_seq_len,
    )


# --------------------------------------------------------------------------
# Tiny synthetic corpus (NO downloaded weights / datasets)
# --------------------------------------------------------------------------


def synthetic_token_stream(num_tokens: int, vocab_size: int, seed: int = 1234) -> List[int]:
    """Deterministic pseudo-random token ids. No dataset download."""
    if num_tokens < 0 or vocab_size <= 0:
        raise ValueError("num_tokens>=0 and vocab_size>0 required")
    out: List[int] = []
    state = seed & 0xFFFFFFFF
    for _ in range(num_tokens):
        # xorshift32, deterministic and dependency-free.
        state ^= (state << 13) & 0xFFFFFFFF
        state ^= (state >> 17)
        state ^= (state << 5) & 0xFFFFFFFF
        out.append(state % vocab_size)
    return out


def chunk_into_blocks(tokens: List[int], block_size: int) -> List[Tuple[int, ...]]:
    """Split a token stream into fixed-size blocks (last block may be short)."""
    if block_size <= 0:
        raise ValueError("block_size>0 required")
    return [tuple(tokens[i:i + block_size]) for i in range(0, len(tokens), block_size)]
