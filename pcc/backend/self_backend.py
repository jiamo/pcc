from __future__ import annotations

"""Compatibility facade for the self-backend package.

The old single-file backend lived here. The AArch64 Darwin implementation now
resides in `self_backend_aarch64_darwin.py`; this facade keeps existing imports
stable while the backend is split into shared core plus target-specific layers.
"""

from .self_backend_aarch64_darwin import emit_aarch64_darwin_asm

__all__ = ["emit_aarch64_darwin_asm"]
