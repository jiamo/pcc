"""Scope-owned AArch64 fragments with explicit integer record identities."""

from __future__ import annotations

from .arm64_encode import (
    EMITTED_INSTRUCTION_MOVE,
    EMITTED_INSTRUCTION_SCALAR,
    EMITTED_INSTRUCTION_UNSCALED,
    EncodeError,
    encode_emitted_addsub_immediate_parts,
    encode_emitted_addsub_register_parts,
    encode_emitted_load_store_parts,
    encode_emitted_move_register_parts,
    encode_emitted_movewide_parts,
    encode_emitted_nop_parts,
    intern_emitted_symbol,
    validate_emitted_label_name,
)
from .self_backend_value_arena import (
    CompilerInt2,
    CompilerInt4,
    CompilerIntArena,
    CompilerRecordSpanArena,
)


EMISSION_RECORD_LABEL = -1


class AArch64EmissionFragments:
    """Native records plus persistent sequence roots for one emission scope.

    Instruction records use (word, family, relocation kind, symbol ID), just
    like the direct encoder. A negative family identifies a label whose first
    field indexes the owned symbol side table. No record contains a pointer.
    This first producer slice encodes pointer reloads; unsupported operand
    families still fail through the canonical encoder before publication.
    """

    def __init__(self) -> None:
        names: list[str] = []
        symbol_ids: dict[str, int] = {}
        records = CompilerIntArena()
        try:
            spans = CompilerRecordSpanArena()
            try:
                cursor = CompilerIntArena()
            except Exception:
                spans.close()
                raise
        except Exception:
            records.close()
            raise
        self.records: CompilerIntArena = records
        self.spans: CompilerRecordSpanArena = spans
        self.cursor: CompilerIntArena = cursor
        self.symbol_names: list[str] = names
        self.symbol_ids: dict[str, int] = symbol_ids
        self.closed: bool = False

    def _require_open(self) -> None:
        if self.closed:
            raise RuntimeError("AArch64 emission fragments are closed")

    def new_fragment(self) -> CompilerInt2:
        self._require_open()
        return self.spans.new_span()

    def extend_fragment(self, destination: CompilerInt2, source: CompilerInt2) -> None:
        self._require_open()
        self.spans.extend(destination, source)

    def _append_record(self, fragment: CompilerInt2, record: CompilerInt4) -> None:
        self._require_open()
        record_id = len(self.records) // 4
        self.records.append4(record.first, record.second, record.third, record.fourth)
        # If publication raises, no new record becomes visible in the span.
        # The scope still owns the unpublished storage and closes it on error.
        self.spans.append(fragment, record_id)

    def append_word(self, fragment: CompilerInt2, word: int, family: int) -> None:
        if word < 0 or word > 0xFFFFFFFF:
            raise EncodeError("emission fragment word is outside uint32")
        if family not in (
            EMITTED_INSTRUCTION_MOVE,
            EMITTED_INSTRUCTION_SCALAR,
            EMITTED_INSTRUCTION_UNSCALED,
        ):
            raise EncodeError("emission fragment instruction family is unsupported")
        self._append_record(fragment, CompilerInt4(word, family, 0, -1))

    def append_label(self, fragment: CompilerInt2, name: str) -> None:
        self._require_open()
        validate_emitted_label_name(name)
        symbol_id = intern_emitted_symbol(name, self.symbol_ids, self.symbol_names)
        self._append_record(fragment, CompilerInt4(symbol_id, EMISSION_RECORD_LABEL, 0, -1))

    def append_nop(self, fragment: CompilerInt2) -> None:
        self.append_word(fragment, encode_emitted_nop_parts(), EMITTED_INSTRUCTION_SCALAR)

    def append_memory(
        self, fragment: CompilerInt2, mnemonic: str, register: str,
        base: str, offset: int = 0,
    ) -> None:
        word = encode_emitted_load_store_parts(mnemonic, register, base, offset)
        family = EMITTED_INSTRUCTION_SCALAR
        if mnemonic in ("ldur", "stur", "ldurb", "sturb"):
            family = EMITTED_INSTRUCTION_UNSCALED
        self.append_word(fragment, word, family)

    def append_move(self, fragment: CompilerInt2, destination: str, source: str) -> None:
        word = encode_emitted_move_register_parts(destination, source)
        self.append_word(fragment, word, EMITTED_INSTRUCTION_MOVE)

    def append_movewide(
        self, fragment: CompilerInt2, mnemonic: str, destination: str,
        immediate: int, shift: int = 0,
    ) -> None:
        word = encode_emitted_movewide_parts(mnemonic, destination, immediate, shift)
        self.append_word(fragment, word, EMITTED_INSTRUCTION_MOVE)

    def append_addsub_immediate(
        self, fragment: CompilerInt2, mnemonic: str, destination: str,
        left: str, immediate: int,
    ) -> None:
        word = encode_emitted_addsub_immediate_parts(mnemonic, destination, left, immediate)
        self.append_word(fragment, word, EMITTED_INSTRUCTION_SCALAR)

    def append_addsub_register(
        self, fragment: CompilerInt2, mnemonic: str, destination: str,
        left: str, right: str,
    ) -> None:
        word = encode_emitted_addsub_register_parts(mnemonic, destination, left, right)
        self.append_word(fragment, word, EMITTED_INSTRUCTION_SCALAR)

    def start_cursor(self, fragment: CompilerInt2) -> None:
        self._require_open()
        self.spans.start_cursor(fragment, self.cursor)

    def next_record_id(self) -> int:
        self._require_open()
        return self.spans.next_record(self.cursor)

    def reset(self) -> None:
        self._require_open()
        self.spans.reset()
        self.records.clear()
        self.cursor.clear()
        self.symbol_names.clear()
        self.symbol_ids.clear()

    def close(self) -> None:
        if self.closed:
            return
        self.cursor.close()
        self.spans.close()
        self.records.close()
        self.symbol_names.clear()
        self.symbol_ids.clear()
        self.closed = True
