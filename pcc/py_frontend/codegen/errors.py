"""Shared codegen exceptions."""
from __future__ import annotations


class L1CodegenError(Exception):
    """Raised when L1 cannot handle an AST shape it should have."""
