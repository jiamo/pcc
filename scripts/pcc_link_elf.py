#!/usr/bin/env python3
"""Link x86_64 ELF relocatable inputs with pcc's static-only linker.

This driver is the host-process seam matching ``pcc_link_macho.py``.  It never
falls back to ``as`` or ``ld``.  Internal self-backend assembly is encoded by
pcc; ``--object`` remains the explicit boundary for external standard ET_REL
inputs.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


def repo_root() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists():
            return current
        current = current.parent
    raise SystemExit("AGENTS.md not found above " + __file__)


def _paths_alias(first: Path, second: Path) -> bool:
    try:
        if first.resolve() == second.resolve():
            return True
    except (OSError, RuntimeError):
        pass
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _publish(out_path: Path, image: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=out_path.name + ".pcc-elf-",
            suffix=".tmp",
            dir=out_path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(image)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != image:
            raise RuntimeError("ELF publication byte verification failed")
        os.chmod(temporary, 0o755)
        os.replace(temporary, out_path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcc_link_elf")
    parser.add_argument("--asm", action="append", default=[], dest="assembly")
    parser.add_argument("--object", action="append", default=[], dest="objects")
    parser.add_argument("--archive", action="append", default=[])
    parser.add_argument("--out", required=True)
    parser.add_argument("--entry", default="_start")
    parser.add_argument(
        "--previous-output",
        help=(
            "accepted for command parity; the ELF slice always rebuilds an "
            "exact image before atomic publication"
        ),
    )
    args = parser.parse_args(argv)
    if not args.assembly and not args.objects:
        parser.error("at least one --asm or --object input is required")

    out_path = Path(args.out)
    input_paths = [
        Path(path) for path in args.assembly + args.objects + args.archive
    ]
    if args.previous_output:
        input_paths.append(Path(args.previous_output))
    if any(_paths_alias(out_path, path) for path in input_paths):
        parser.error("--out must not overwrite an input file")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(repo_root()))
    from pcc.backend.elf_x86_64 import (
        ElfError,
        link_static_executable,
        parse_relocatable,
        parse_static_executable,
    )
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.backend.x86_64_encode import X86EncodeError

    try:
        objects = [assemble_file(Path(path).read_text(encoding="utf-8"))
                   for path in args.assembly]
        objects.extend(
            parse_relocatable(Path(path).read_bytes())
            for path in args.objects
        )
        archives = [Path(path).read_bytes() for path in args.archive]
        image = link_static_executable(objects, archives=archives, entry=args.entry)
        parse_static_executable(image)
    except (ElfError, X86EncodeError, OSError, UnicodeError) as exc:
        parser.error(str(exc))
    _publish(out_path, image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
