#!/usr/bin/env python3
from __future__ import annotations

import argparse

from pcc.pass_explain import PassDecision, format_pass_explain


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Explain pcc default pass decisions.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    ns = parser.parse_args(argv)
    print(format_pass_explain([
        PassDecision("mem2reg", True, "default fast preset"),
        PassDecision("sroa", True, "default fast preset"),
        PassDecision("adce", True, "default fast preset"),
    ], fmt=ns.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
