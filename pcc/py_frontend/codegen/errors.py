"""Shared codegen exceptions."""
from __future__ import annotations

class L1CodegenError(Exception):
    """Raised when L1 cannot handle an AST shape it should have."""


class CodegenDiagnosticError(Exception):
    """Codegen failure carrying the exact span used by structured diagnostics."""

    def __init__(
        self,
        message: str,
        diagnostic_span,
        original_exception_type: str,
    ) -> None:
        super().__init__(message)
        self.diagnostic_span = diagnostic_span
        self.original_exception_type = original_exception_type
