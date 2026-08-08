#!/usr/bin/env python3
"""Assemble and link a self-backend .s with pcc's own Mach-O toolchain.

This is a **subprocess entry point on purpose**. `pipeline.py` must not import
`pcc.backend.macho_*` in process: doing so pulls the whole Mach-O toolchain
into pcc's stage1 self-host closure, which AGENTS.md forbids ("must not
reintroduce compiled-stage imports/calls of `pcc.backend.*`") and which broke
the fallback-baseline gate when it was tried. Running it behind a process
boundary — the same seam the host-python probes in `pipeline.py` already use —
keeps the compiled-stage closure frozen while letting the self path use pcc's
own linker.

Usage:
    pcc_link_macho.py [--asm SELF.s ...] --out BINARY [--archive LIB.a]
                      [--native-object SELF.pco ...]
                      [--object EXTRA.o ...] [--entry _main]
                      [--previous-output BINARY]

Exits non-zero with the underlying error on anything outside the linker's
proven subset; there is no fall back to cc/ld here, because the caller asked
for pcc's linker specifically (S-track no-silent-fallback rule). The complete
pcc-signed image is structurally checked and atomically published; its caller
must not replace that signature with an external codesign invocation.
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import tempfile
from pathlib import Path


def repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    while cur != cur.parent:
        if (cur / "AGENTS.md").exists():
            return cur
        cur = cur.parent
    raise SystemExit("AGENTS.md not found above " + __file__)


_PATCH_CHUNK_SIZE = 64 * 1024


def _assemble_asm_path_worker(path: str) -> bytes:
    """Assemble one .s file to encoded native-object bytes (serial path).

    The parallel path submits ``pcc.backend.macho_assemble_worker``'s
    importable twin instead, because a function defined in this script is
    only picklable when the script itself is ``__main__``.
    """
    sys.path.insert(0, str(repo_root()))
    from pcc.backend.macho_assemble_worker import assemble_asm_path_to_encoded

    return assemble_asm_path_to_encoded(path)


def _publish_executable(
    out_path: Path,
    image: bytes,
    previous_path: Path | None = None,
) -> tuple[int, int]:
    """Atomically patch a prior artifact into the exact requested image.

    The destination is never modified in place.  A temporary copy of the
    previous complete artifact is patched by changed chunks and byte-compared
    to the freshly validated target.  Its verified bytes are then copied to a
    fresh executable inode and atomically published.
    ``(changed_chunks, patched_bytes)`` is returned for focused diagnostics.
    """

    if not isinstance(image, bytes):
        raise TypeError("linked executable image must be bytes")
    # Kept behind the subprocess function boundary so importing this driver
    # never pulls the Mach-O implementation into pcc's compiled-stage closure.
    from pcc.backend.macho_parallel import OutputRegion, write_mmap_output

    base_path = previous_path if previous_path is not None else out_path
    try:
        previous = base_path.read_bytes()
    except OSError:
        previous = None

    temporary: Path | None = None
    publish_temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=out_path.name + ".pcc-link-",
            suffix=".tmp",
            dir=out_path.parent,
            delete=False,
        ) as f:
            temporary = Path(f.name)
            changed_chunks = 0
            patched_bytes = 0
            patches = []
            if previous is None:
                if image:
                    changed_chunks = 1
                    patched_bytes = len(image)
                    patches.append(OutputRegion(0, image, "complete image"))
            else:
                f.write(previous)
                for offset in range(0, len(image), _PATCH_CHUNK_SIZE):
                    end = min(offset + _PATCH_CHUNK_SIZE, len(image))
                    target_chunk = image[offset:end]
                    if previous[offset:end] == target_chunk:
                        continue
                    patches.append(OutputRegion(
                        offset,
                        target_chunk,
                        f"incremental chunk {offset // _PATCH_CHUNK_SIZE}",
                    ))
                    changed_chunks += 1
                    patched_bytes += len(target_chunk)
            # The complete target size and every patch destination are frozen
            # before workers receive a file-backed mapping.  The temp file is
            # still verified and atomically renamed only after all workers join.
            write_mmap_output(f, len(image), patches)
            f.seek(0)
            if f.read() != image:
                raise RuntimeError(
                    "incremental publication did not reproduce the linked image"
                )
            f.flush()
            os.fsync(f.fileno())
        # Do not publish the inode that was modified through a writable mmap.
        # On Darwin the kernel can retain a vnode/code-sign state for that
        # inode and SIGKILL an otherwise byte-identical, userspace-verifiable
        # Mach-O with ``Code Signature Invalid`` at launch.  Copy the fully
        # verified bytes into a fresh, never-mmap-written inode and atomically
        # publish that inode instead.
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=out_path.name + ".pcc-publish-",
            suffix=".tmp",
            dir=out_path.parent,
            delete=False,
        ) as published:
            publish_temporary = Path(published.name)
            published.write(image)
            published.flush()
            os.fsync(published.fileno())
        os.chmod(publish_temporary, 0o755)
        os.replace(publish_temporary, out_path)
        publish_temporary = None
        return changed_chunks, patched_bytes
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        if publish_temporary is not None:
            try:
                publish_temporary.unlink()
            except FileNotFoundError:
                pass


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcc_link_macho")
    parser.add_argument("--asm", action="append", default=[],
                        help="self-backend .s input")
    parser.add_argument("--out", required=True, help="executable to write")
    parser.add_argument("--archive", action="append", default=[],
                        help="static archive to draw members from")
    parser.add_argument("--object", action="append", default=[],
                        dest="objects", help="additional .o input")
    parser.add_argument("--native-object", action="append", default=[],
                        dest="native_objects",
                        help="indexed pcc-native object input")
    parser.add_argument("--entry", default="_main")
    parser.add_argument(
        "--previous-output",
        help="previous complete artifact used as the incremental patch base",
    )
    semantic_group = parser.add_mutually_exclusive_group()
    semantic_group.add_argument(
        "--semantic-layout-manifest",
        help=(
            "exact merged-object semantic atom proof; opt-in and fail-closed"
        ),
    )
    semantic_group.add_argument(
        "--semantic-layout-policy",
        help=(
            "frontend symbol-semantics policy materialized against the exact "
            "merged object; opt-in and fail-closed"
        ),
    )
    args = parser.parse_args(argv)
    if not args.asm and not args.objects and not args.native_objects:
        parser.error(
            "at least one --asm, --native-object, or --object input is required"
        )
    out_path = Path(args.out)
    raw_input_paths = (
        list(args.asm)
        + list(args.native_objects)
        + list(args.objects)
        + list(args.archive)
        + ([args.semantic_layout_manifest] if args.semantic_layout_manifest else [])
        + ([args.semantic_layout_policy] if args.semantic_layout_policy else [])
    )
    input_paths = [Path(path) for path in raw_input_paths]
    if any(_paths_alias(out_path, path) for path in input_paths):
        parser.error("--out must not overwrite an input file")

    sys.path.insert(0, str(repo_root()))
    from pcc.backend.macho_codesign import build_signature, parse_signature
    from pcc.backend.macho_exec import (
        link_executable,
        link_prepared_executable,
        prepare_executable_object,
    )
    from pcc.backend.macho_incremental import (
        incremental_link_session_from_environment,
    )
    from pcc.backend.macho_parallel import ParallelLinkError, resolve_link_jobs
    from pcc.backend.native_object import (
        NativeObject,
        decode_native_object,
        is_native_object_bytes,
    )
    # A cold self-host link builds millions of short-lived dataclass/tuple
    # objects (stack-map locations, relocations, sections).  Measured: ~84%
    # of the semantic-link CPU was CPython's cycle collector tracing them
    # (gc_collect_main / deduce_unreachable), not the link logic itself.
    # These objects are acyclic (relocations name symbols by string), so
    # refcounting is sufficient; freeze the interpreter baseline and disable
    # the collector for this batch process, then resume before publishing.
    try:
        gc.freeze()
    except Exception:
        pass
    gc.disable()

    # Validate the scheduling selector even if an exact incremental image hit
    # means no worker phase is needed.  A misspelled production mode must not
    # be accepted or rejected depending on cache state.
    try:
        resolve_link_jobs(0, 0)
    except ParallelLinkError as exc:
        parser.error(str(exc))

    # A monkeypatched/custom linker has no stable implementation identity and
    # must not read or populate production incremental state.
    canonical_linker = (
        getattr(link_executable, "__module__", "") == "pcc.backend.macho_exec"
        and getattr(link_executable, "__name__", "") == "link_executable"
    )
    session = (
        incremental_link_session_from_environment(repo_root())
        if canonical_linker else None
    )
    # Probe the incremental cache for every .s first, then assemble only the
    # misses — in parallel processes, since pure-Python assembly of a cold
    # stage1 (hundreds of .s, one 72k-block module top) dominates link time.
    natives_by_index: dict[int, NativeObject] = {}
    pending_paths: list[str] = []
    pending_indices: list[int] = []
    pending_store: list[tuple] = []
    for index, path in enumerate(args.asm):
        if session is None:
            pending_paths.append(path)
            pending_indices.append(index)
            continue
        with open(path, "r", encoding="utf-8") as f:
            assembly = f.read()
        cache_path, cached, replace_valid = session.probe_assembly_cache(
            assembly
        )
        if cached is not None:
            natives_by_index[index] = cached
            continue
        pending_paths.append(path)
        pending_indices.append(index)
        pending_store.append((cache_path, replace_valid))
    if pending_paths:
        jobs = resolve_link_jobs(
            len(pending_paths),
            sum(os.path.getsize(path) for path in pending_paths),
        )
        if jobs > 1 and len(pending_paths) > 1:
            from concurrent.futures import ProcessPoolExecutor

            from pcc.backend.macho_assemble_worker import (
                assemble_asm_path_to_encoded,
            )

            with ProcessPoolExecutor(max_workers=jobs) as pool:
                encoded_results = list(
                    pool.map(assemble_asm_path_to_encoded, pending_paths)
                )
        else:
            encoded_results = [
                _assemble_asm_path_worker(path) for path in pending_paths
            ]
        for position, encoded in enumerate(encoded_results):
            native = decode_native_object(encoded)
            if session is not None:
                cache_path, replace_valid = pending_store[position]
                session.store_assembled_native_object(
                    cache_path,
                    native,
                    replace_valid=replace_valid,
                    encoded=encoded,
                )
            natives_by_index[pending_indices[position]] = native
    objects = [natives_by_index[index] for index in range(len(args.asm))]
    for path in args.native_objects:
        with open(path, "rb") as f:
            objects.append(decode_native_object(f.read()))
    for path in args.objects:
        with open(path, "rb") as f:
            payload = f.read()
        if is_native_object_bytes(payload):
            parser.error(
                f"{path}: pcc-native input requires --native-object, "
                "not --object"
            )
        objects.append(payload)
    archives = []
    for path in args.archive:
        with open(path, "rb") as f:
            archives.append(f.read())

    semantic_manifest = None
    semantic_policy = None
    semantic_identity = b""
    if args.semantic_layout_manifest or args.semantic_layout_policy:
        from pcc.backend.macho_semantic_layout import (
            SemanticLayoutError,
            apply_semantic_layout,
            load_frontend_policy,
            load_manifest,
            materialize_frontend_manifest,
        )
        try:
            if args.semantic_layout_manifest:
                semantic_manifest = load_manifest(args.semantic_layout_manifest)
                semantic_identity = (
                    "manifest:" + semantic_manifest.digest()
                ).encode("ascii")
            else:
                semantic_policy = load_frontend_policy(
                    args.semantic_layout_policy
                )
                semantic_identity = (
                    "frontend-policy:" + semantic_policy.digest()
                ).encode("ascii")
        except SemanticLayoutError as exc:
            parser.error(str(exc))

    identifier = b"pcc-linked"

    def validate_image(candidate: bytes):
        signature = parse_signature(candidate)
        if signature.identifier != identifier:
            raise RuntimeError(
                "pcc Mach-O linker returned an artifact without its linker identity"
            )
        if signature.dataoff + signature.datasize != len(candidate):
            raise RuntimeError(
                "pcc Mach-O linker returned trailing bytes after its signature"
            )
        expected_signature = build_signature(
            candidate[:signature.dataoff],
            identifier=signature.identifier,
            exec_seg_base=signature.exec_seg_base,
            exec_seg_limit=signature.exec_seg_limit,
            exec_seg_flags=signature.exec_seg_flags,
        )
        actual_signature = candidate[
            signature.dataoff:signature.dataoff + signature.datasize
        ]
        if actual_signature != expected_signature:
            raise RuntimeError(
                "pcc Mach-O linker returned an image with stale page hashes"
            )
        return signature

    def prepare_for_link(link_objects, *, archives=()):
        if semantic_policy is None:
            return prepare_executable_object(
                link_objects,
                archives=archives,
                semantic_manifest=semantic_manifest,
            )
        merged = prepare_executable_object(link_objects, archives=archives)
        exact_manifest = materialize_frontend_manifest(
            merged, semantic_policy
        )
        return apply_semantic_layout(
            merged, exact_manifest
        ).native_object

    if session is None and not semantic_identity:
        # Preserve the ordinary cold-link API exactly when no semantic policy
        # was requested.  Tests and embedders may deliberately replace it.
        image = link_executable(
            objects,
            archives=archives,
            entry=args.entry,
            identifier=identifier,
        )
        validate_image(image)
    elif session is None:
        merged = prepare_for_link(objects, archives=archives)
        image = link_prepared_executable(
            merged,
            entry=args.entry,
            identifier=identifier,
        )
        validate_image(image)
    else:
        image = session.link(
            objects,
            archives=archives,
            entry=args.entry,
            identifier=identifier,
            semantic_identity=semantic_identity,
            prepare=prepare_for_link,
            finalize=link_prepared_executable,
            validate=validate_image,
        )
    previous_path = (
        Path(args.previous_output) if args.previous_output else None
    )
    gc.enable()
    gc.collect()
    _publish_executable(out_path, image, previous_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
