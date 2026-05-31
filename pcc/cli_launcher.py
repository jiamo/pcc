"""Installed ``pcc`` launcher.

The public command intentionally stays on the full CPython-hosted CLI.  The
native bootstrap compiler is exposed separately as ``pcc1``.
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    from pcc.cli_core import cli_main

    return cli_main(list(argv))


if __name__ == "__main__":
    raise SystemExit(main())
