#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Summarize pcc profile/trace JSON")
    parser.add_argument("profile_json")
    args = parser.parse_args(argv)
    data = json.loads(Path(args.profile_json).read_text(encoding="utf-8"))
    phases = data.get("phases", [])
    for phase in sorted(phases, key=lambda p: p.get("duration_ms", 0), reverse=True):
        print(f"{phase.get('name', '<unknown>')}: {phase.get('duration_ms', 0):.3f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
