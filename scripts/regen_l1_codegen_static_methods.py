"""Regenerate ``pcc/py_frontend/codegen/_l1_codegen_static_methods.py``.

This is a HOST-PYTHON-ONLY tool that uses ``inspect.signature`` to extract
real call signatures from the live L1CodeGen class (with all mixins
assembled) and emits a pure-data Python module that the bootstrap-safe
``layer1_support.py`` can import.

Why this layer of indirection: ``layer1_support.py`` is in the no-libpython
bootstrap closure for ``pcc1``.  ``inspect.signature`` and friends aren't
natively lowered by the pcc-Python frontend, so any direct use of them in
layer1_support.py forces a libpython fallback and aborts the strict
``--python-libpython=off`` build.  Generating the static data offline keeps
the runtime module data-only and bootstrap-safe.

Run when ``host_contract.py::L1_CODEGEN_HOST_METHODS`` changes or the
mixin source code's signatures change:

    env -u LC_ALL uv run python scripts/regen_l1_codegen_static_methods.py

The output is committed; the runtime never invokes this script.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _arg_dict_literal(name: str, kind: str, has_default: bool) -> str:
    """Return the source-text of an inline ``_export_arg``-shaped dict.

    Inline form (no helper-call) avoids a circular import between
    ``layer1_support.py`` (which defines ``_export_arg``) and this
    generated module (which is imported back into ``layer1_support``).
    Mirrors ``layer1_support._export_arg``'s return shape exactly.
    """
    return (
        "        {"
        f"'name': {name!r}, 'kind': {kind!r}, "
        "'annotation': ('dyn',), 'default': None, "
        f"'has_default': {'True' if has_default else 'False'}"
        "},"
    )


def _format_param(p: inspect.Parameter, kw_only_emitted: list) -> str:
    """Return the source-text of one or more inline param dicts."""
    kind = p.kind
    if kind is inspect.Parameter.VAR_POSITIONAL:
        return _arg_dict_literal(p.name, "*args", False)
    if kind is inspect.Parameter.VAR_KEYWORD:
        return _arg_dict_literal(p.name, "**kwargs", False)
    if kind is inspect.Parameter.KEYWORD_ONLY:
        lines = []
        if not kw_only_emitted[0]:
            lines.append(_arg_dict_literal("", "kw_only", False))
            kw_only_emitted[0] = True
        has_default = p.default is not inspect.Parameter.empty
        lines.append(_arg_dict_literal(p.name, "pos", has_default))
        return "\n".join(lines)
    has_default = p.default is not inspect.Parameter.empty
    return _arg_dict_literal(p.name, "pos", has_default)


def _format_method_entry(name: str, sig: inspect.Signature) -> str:
    kw_only_emitted = [False]
    param_lines = []
    n_params = 0
    for p in sig.parameters.values():
        n_params += 1
        param_lines.append(_format_param(p, kw_only_emitted))
    param_types_repr = "(" + ", ".join(['("dyn",)'] * n_params) + (",)" if n_params == 1 else ")")
    return (
        "    {\n"
        f"        'name': {name!r},\n"
        "        'kind': 'instance',\n"
        "        'return_ty': ('dyn',),\n"
        f"        'param_types': {param_types_repr},\n"
        "        'call_sig': (\n"
        f"{chr(10).join(param_lines)}\n"
        "        ),\n"
        "        'box_int_abi': False,\n"
        "    },"
    )


def main() -> int:
    from pcc.py_frontend.codegen.host_contract import L1_CODEGEN_HOST_METHODS
    from pcc.py_frontend.codegen.layer1 import L1CodeGen

    method_entries: list[str] = []
    skipped: list[tuple[str, str]] = []
    for method_name in L1_CODEGEN_HOST_METHODS:
        fn = getattr(L1CodeGen, method_name, None)
        if fn is None or not callable(fn):
            skipped.append((method_name, "not-callable"))
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError) as e:
            skipped.append((method_name, f"signature-fail: {e!r}"))
            continue
        method_entries.append(_format_method_entry(method_name, sig))

    out_path = (
        _REPO_ROOT
        / "pcc"
        / "py_frontend"
        / "codegen"
        / "_l1_codegen_static_methods.py"
    )
    body = "\n".join(method_entries)
    content = (
        '"""Auto-generated pure-data static method entries for L1CodeGen.\n'
        "\n"
        "DO NOT EDIT BY HAND.  Regenerate via\n"
        "``scripts/regen_l1_codegen_static_methods.py`` after changing\n"
        "``host_contract.L1_CODEGEN_HOST_METHODS`` or a mixin source\n"
        "signature.\n"
        "\n"
        "This file lives in the no-libpython bootstrap closure for pcc1\n"
        "so it must be parseable as pure data (only tuples, dicts,\n"
        "string literals, and the ``_export_arg`` helper call).  See\n"
        "``scripts/regen_l1_codegen_static_methods.py`` for context.\n"
        '"""\n'
        "from __future__ import annotations\n"
        "\n"
        "L1_CODEGEN_STATIC_METHODS = (\n"
        f"{body}\n"
        ")\n"
    )
    out_path.write_text(content, encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr)
    print(f"  {len(method_entries)} method entries", file=sys.stderr)
    if skipped:
        print(f"  {len(skipped)} skipped:", file=sys.stderr)
        for name, reason in skipped:
            print(f"    - {name}: {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
