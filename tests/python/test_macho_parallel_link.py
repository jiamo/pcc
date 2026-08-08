"""Focused deterministic-concurrency contracts for the owned Mach-O linker."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcc.backend import macho_spec as spec
from pcc.backend.macho_archive import read_archive
from pcc.backend.macho_exec import link_executable
from pcc.backend.macho_link import LinkError
from pcc.backend.macho_obj import Relocation, Section, TextSymbol, TEXT_SECTION_FLAGS
from pcc.backend.macho_parallel import (
    PARALLEL_JOBS_ENV,
    OutputRegion,
    ParallelLinkError,
    ShardedSymbolDefinitions,
    SymbolDefinition,
    materialize_output,
    ordered_parallel_map,
    resolve_link_jobs,
    write_mmap_output,
)
from pcc.backend.native_object import NativeObject


_RET = b"\xc0\x03\x5f\xd6"
_BL_PLACEHOLDER = b"\x00\x00\x00\x94"


def _link_inputs() -> list[NativeObject]:
    caller = NativeObject.from_sections(
        [Section(
            sectname="__text",
            segname="__TEXT",
            data=_BL_PLACEHOLDER + _RET,
            align_log2=2,
            flags=TEXT_SECTION_FLAGS,
            symbols=(TextSymbol("_main", 0),),
            relocations=(Relocation(
                offset=0,
                symbol="_helper",
                type=spec.ARM64_RELOC_BRANCH26,
                pcrel=True,
            ),),
        )],
        undefined=["_helper"],
    )
    helper = NativeObject.from_sections([Section(
        sectname="__text",
        segname="__TEXT",
        data=_RET,
        align_log2=2,
        flags=TEXT_SECTION_FLAGS,
        symbols=(TextSymbol("_helper", 0),),
    )])
    return [caller, helper]


def _archive_bytes(members: list[tuple[str, bytes]]) -> bytes:
    archive = bytearray(b"!<arch>\n")
    for name, payload in members:
        encoded_name = (name + "/").encode("ascii")
        assert len(encoded_name) <= 16
        archive += encoded_name.ljust(16, b" ")
        archive += b"0".ljust(12, b" ")  # timestamp
        archive += b"0".ljust(6, b" ")   # uid
        archive += b"0".ljust(6, b" ")   # gid
        archive += b"100644".ljust(8, b" ")
        archive += str(len(payload)).encode("ascii").ljust(10, b" ")
        archive += b"`\n"
        archive += payload
        if len(payload) % 2:
            archive += b"\n"
    return bytes(archive)


def test_disjoint_output_is_independent_of_region_and_worker_order() -> None:
    regions = [
        OutputRegion(16, b"tail", "tail"),
        OutputRegion(0, b"head", "head"),
        OutputRegion(8, b"middle", "middle"),
    ]
    expected = b"head\0\0\0\0middle\0\0tail"

    assert materialize_output(20, regions, jobs=1) == expected
    for jobs in (2, 3, 8):
        assert materialize_output(20, list(reversed(regions)), jobs=jobs) == expected


def test_parallel_archive_inspection_preserves_file_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller, helper = _link_inputs()
    archive = _archive_bytes([
        ("caller.o", caller.to_macho()),
        ("helper.o", helper.to_macho()),
    ])
    monkeypatch.setenv(PARALLEL_JOBS_ENV, "4")

    first = read_archive(archive)
    second = read_archive(archive)

    assert [member.name for member in first] == ["caller.o", "helper.o"]
    assert first == second
    assert first[0].undefined == frozenset({"_helper"})
    assert first[1].defines == frozenset({"_helper"})


def test_disjoint_regions_patch_a_file_backed_mapping(
    tmp_path: Path,
) -> None:
    output = tmp_path / "linked-image"
    output.write_bytes(b"-" * 20)
    regions = [
        OutputRegion(16, b"tail", "tail"),
        OutputRegion(0, b"head", "head"),
        OutputRegion(8, b"middle", "middle"),
    ]

    with output.open("r+b") as file:
        write_mmap_output(file, 20, regions, jobs=4)

    assert output.read_bytes() == b"head----middle--tail"


def test_parallel_map_reports_lowest_input_failure_not_first_scheduled() -> None:
    def inspect(index: int) -> int:
        if index in (1, 3):
            raise ValueError(f"bad input {index}")
        return index * 2

    with pytest.raises(ValueError, match="bad input 1"):
        ordered_parallel_map(
            [0, 1, 2, 3],
            inspect,
            total_bytes=1024 * 1024,
            jobs=4,
        )


def test_sharded_symbol_owner_is_independent_of_parallel_insertion_order() -> None:
    definitions = ShardedSymbolDefinitions()
    candidates = [
        SymbolDefinition(7, 3),
        SymbolDefinition(1, 9),
        SymbolDefinition(4, 2),
        SymbolDefinition(1, 5),
    ]

    ordered_parallel_map(
        list(reversed(candidates)),
        lambda definition: definitions.add("_shared", definition),
        total_bytes=1024 * 1024,
        jobs=4,
    )
    with pytest.raises(ParallelLinkError, match="frozen before lookup"):
        definitions.owner("_shared")
    definitions.freeze()

    assert definitions.definitions("_shared") == tuple(sorted(candidates))
    assert definitions.owner("_shared") == SymbolDefinition(1, 5)
    with pytest.raises(ParallelLinkError, match="frozen"):
        definitions.add("_shared", SymbolDefinition(0, 0))


def test_parallel_output_rejects_overlap_and_out_of_bounds() -> None:
    with pytest.raises(ParallelLinkError, match="overlaps"):
        materialize_output(8, [
            OutputRegion(0, b"12345", "first"),
            OutputRegion(4, b"5678", "second"),
        ], jobs=4)

    with pytest.raises(ParallelLinkError, match="past the image size"):
        materialize_output(8, [
            OutputRegion(7, b"too long", "overflow"),
        ], jobs=4)


def test_mmap_layout_failure_does_not_resize_or_patch_the_file(
    tmp_path: Path,
) -> None:
    output = tmp_path / "must-survive"
    original = b"unchanged"
    output.write_bytes(original)

    with output.open("r+b") as file:
        with pytest.raises(ParallelLinkError, match="overlaps"):
            write_mmap_output(file, 4, [
                OutputRegion(0, b"abc", "first"),
                OutputRegion(2, b"xy", "second"),
            ], jobs=2)

    assert output.read_bytes() == original


def test_parallel_job_configuration_is_bounded_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PARALLEL_JOBS_ENV, raising=False)
    monkeypatch.setenv("PCC_OUTER_PARALLELISM", "2")
    monkeypatch.setattr("pcc.backend.macho_parallel.os.cpu_count", lambda: 12)
    assert resolve_link_jobs(1000, 1024) == 1
    assert resolve_link_jobs(1000, 1024 * 1024) == 6

    monkeypatch.setenv(PARALLEL_JOBS_ENV, "9999")
    assert resolve_link_jobs(1000, 1024 * 1024) == 32

    monkeypatch.setenv(PARALLEL_JOBS_ENV, "off")
    assert resolve_link_jobs(1000, 1024 * 1024) == 1

    monkeypatch.setenv(PARALLEL_JOBS_ENV, "four")
    with pytest.raises(ParallelLinkError, match="positive integer"):
        resolve_link_jobs(1000, 1024 * 1024)


def test_parallel_and_serial_executable_links_are_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _link_inputs()
    monkeypatch.setenv(PARALLEL_JOBS_ENV, "1")
    serial = link_executable(inputs)

    for jobs in (2, 4, 8):
        monkeypatch.setenv(PARALLEL_JOBS_ENV, str(jobs))
        assert link_executable(inputs) == serial
        assert link_executable(inputs) == serial


def test_duplicate_definition_diagnostic_is_worker_count_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate = NativeObject.from_sections([Section(
        sectname="__text",
        segname="__TEXT",
        data=_RET,
        align_log2=2,
        flags=TEXT_SECTION_FLAGS,
        symbols=(TextSymbol("_main", 0),),
    )])

    for jobs in (1, 2, 8):
        monkeypatch.setenv(PARALLEL_JOBS_ENV, str(jobs))
        with pytest.raises(LinkError, match="duplicate definition of '_main'"):
            link_executable([duplicate, duplicate])
