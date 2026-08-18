"""Fragment identity, ordering and resource-lifetime contracts."""

import pytest
import re

from pcc.backend import self_backend_aarch64_fragments as fragments
from pcc.backend.arm64_encode import (
    EMITTED_INSTRUCTION_SCALAR,
    EncodeError,
    encode_emitted_move_register_parts,
    encode_emitted_nop_parts,
)


def _read(owner, span):
    result = []
    owner.start_cursor(span)
    record_id = owner.next_record_id()
    while record_id >= 0:
        value = owner.records.get4_unchecked(record_id)
        result.append((record_id, value.first, value.second, value.third, value.fourth))
        record_id = owner.next_record_id()
    return result


def test_explicit_ids_preserve_non_creation_order_and_extend_snapshot():
    owner = fragments.AArch64EmissionFragments()
    try:
        first = owner.new_fragment()
        second = owner.new_fragment()
        owner.append_move(second, "x1", "x2")
        owner.append_nop(first)
        owner.extend_fragment(first, second)
        owner.append_label(second, "Llate")
        records = _read(owner, first)
        assert [row[0] for row in records] == [1, 0]
        assert [row[1] for row in records] == [
            encode_emitted_nop_parts(), encode_emitted_move_register_parts("x1", "x2"),
        ]
        assert len(_read(owner, second)) == 2
        assert owner.spans.projection_count == 0
    finally:
        owner.close()


def test_rejected_operand_preserves_populated_fragment():
    owner = fragments.AArch64EmissionFragments()
    try:
        span = owner.new_fragment()
        owner.append_nop(span)
        before = _read(owner, span)
        for mnemonic in ("ldurh", "sturh"):
            with pytest.raises(EncodeError):
                owner.append_memory(span, mnemonic, "w1", "x29", -8)
        with pytest.raises(EncodeError):
            owner.append_word(span, 1 << 32, EMITTED_INSTRUCTION_SCALAR)
        assert _read(owner, span) == before
    finally:
        owner.close()


@pytest.mark.parametrize("name", [
    "Lfirst:\n  nop\nLsecond", "Lfirst\rLsecond", "Lbad;tail",
    "Lbad:tail", "Lbad\x00tail", ".subsections_via_symbols", "", "L spaced",
])
def test_label_payload_cannot_be_reinterpreted_as_assembly(name):
    owner = fragments.AArch64EmissionFragments()
    try:
        span = owner.new_fragment()
        owner.append_nop(span)
        before = _read(owner, span)
        with pytest.raises(EncodeError, match="label"):
            owner.append_label(span, name)
        assert _read(owner, span) == before
        assert owner.symbol_names == []
    finally:
        owner.close()


def test_plain_ascii_labels_do_not_enter_extended_symbol_parser(monkeypatch):
    from pcc.backend import arm64_encode as encoder

    def extended_parser_not_needed(name):
        raise AssertionError("plain identifier entered extended symbol parser")

    monkeypatch.setattr(encoder, "_is_symbol", extended_parser_not_needed)
    for name in ("Lentry", "_private", "Label_123", "a"):
        encoder.validate_emitted_label_name(name)


def test_emitted_label_language_matches_ascii_symbol_grammar():
    from pcc.backend.arm64_encode import validate_emitted_label_name

    grammar = re.compile(r"[A-Za-z_][A-Za-z_0-9.$]*")
    names = ["", ".directive", "$leading", "9digit", "Lé", "é", "L尾", "L\u2028x"]
    for code in range(128):
        char = chr(code)
        names.extend((char, char + "tail", "L" + char, "L" + char + "tail"))
    names.extend(("L.ok", "L$ok", "L.$end", "Lbad:\n  nop", " Lspace", "Lspace "))
    for name in names:
        if grammar.fullmatch(name):
            validate_emitted_label_name(name)
        else:
            with pytest.raises(EncodeError) as error:
                validate_emitted_label_name(name)
            assert str(error.value) == f"invalid emitted label {name!r}"


def test_reset_invalidates_handles_and_cursor_and_close_is_idempotent():
    owner = fragments.AArch64EmissionFragments()
    span = owner.new_fragment()
    owner.append_label(span, "Lfirst")
    owner.start_cursor(span)
    owner.reset()
    with pytest.raises(RuntimeError, match="stale"):
        owner.start_cursor(span)
    with pytest.raises(RuntimeError, match="stale"):
        owner.next_record_id()
    assert owner.symbol_names == []
    current = owner.new_fragment()
    owner.append_nop(current)
    assert len(_read(owner, current)) == 1
    owner.close()
    owner.close()
    with pytest.raises(RuntimeError, match="closed"):
        owner.new_fragment()


def test_constructor_failure_closes_already_owned_arenas(monkeypatch):
    closed = []

    class Owned:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)

    def scalar_arena():
        if scalar_arena.calls:
            raise MemoryError("cursor allocation")
        scalar_arena.calls += 1
        return Owned("records")

    scalar_arena.calls = 0
    monkeypatch.setattr(fragments, "CompilerIntArena", scalar_arena)
    monkeypatch.setattr(fragments, "CompilerRecordSpanArena", lambda: Owned("spans"))
    with pytest.raises(MemoryError, match="cursor allocation"):
        fragments.AArch64EmissionFragments()
    assert closed == ["spans", "records"]


def test_publication_rejects_invalid_record_before_unchecked_read():
    from pcc.backend import BackendUnavailable
    from pcc.backend.self_backend_aarch64_darwin import _NativeAArch64Emission
    from pcc.backend.self_backend_aarch64_darwin_mem import (
        begin_direct_instruction_capture, end_direct_instruction_capture,
    )

    begin_direct_instruction_capture()
    sink = None
    try:
        sink = _NativeAArch64Emission()
        sink.append(".section __TEXT,__text,regular,pure_instructions")
        span = sink.fragments.new_fragment()
        sink.fragments.spans.append(span, 99)
        with pytest.raises(BackendUnavailable, match="record ID is invalid"):
            sink.publish_fragment(span)
        assert sink.builder.text_size() == 0
    finally:
        if sink is not None:
            sink.close()
        end_direct_instruction_capture()


def test_publication_revalidates_corrupted_label_table_without_instruction_parse():
    from pcc.backend.self_backend_aarch64_darwin import _NativeAArch64Emission
    from pcc.backend.self_backend_aarch64_darwin_mem import (
        begin_direct_instruction_capture, end_direct_instruction_capture,
    )

    begin_direct_instruction_capture()
    sink = None
    try:
        sink = _NativeAArch64Emission()
        sink.append(".section __TEXT,__text,regular,pure_instructions")
        span = sink.fragments.new_fragment()
        sink.fragments.append_label(span, "Lvalid")
        sink.fragments.symbol_names[0] = "Lfirst:\n  nop\nLsecond"
        with pytest.raises(EncodeError, match="label"):
            sink.publish_fragment(span)
        assert sink.builder.text_size() == 0
        assert sink.fragment_record_count == 0
    finally:
        if sink is not None:
            sink.close()
        end_direct_instruction_capture()


def test_text_label_adapter_keeps_its_own_comment_normalization():
    from pcc.backend.arm64_asm_driver import assemble_file

    with pytest.raises(EncodeError):
        assemble_file(".section __TEXT,__text,regular,pure_instructions\nLfoo //comment:\n")
