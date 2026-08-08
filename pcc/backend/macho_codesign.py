"""Ad-hoc (linker-style) Mach-O code signatures, emitted by pcc itself.

LINK-P1-MACHO-CODESIGN: arm64 macOS refuses to run unsigned binaries, so a
linker that is not ld must be able to produce the signature ld produces —
otherwise "pcc's own link path" still ends in `codesign(1)`.

The format (verified against a real ld-signed binary, byte for byte):

    LC_CODE_SIGNATURE -> SuperBlob (big-endian):
        CSMAGIC_EMBEDDED_SIGNATURE, one slot -> CodeDirectory
    CodeDirectory v0x20400, flags CS_ADHOC|CS_LINKER_SIGNED:
        identifier string, then one SHA-256 per 4096-byte page of the file
        from offset 0 up to codeLimit (= the signature's own file offset;
        the final page is partial).

`resign()` recomputes a binary's signature in place: parse the existing
CodeDirectory for the semantic parameters that describe the binary
(identifier, exec-segment fields, flags), patch the load-command sizes if the
blob size changed, and only then hash — the load commands live in page 0, so
patch-before-hash is correctness, not tidiness. Everything outside the
verified shape (missing signature, foreign hash type, unexpected slot
layout) fails closed with `CodesignError`.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from . import macho_spec as spec

CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSSLOT_CODEDIRECTORY = 0

CS_ADHOC = 0x2
CS_LINKER_SIGNED = 0x20000

CS_HASHTYPE_SHA256 = 2
_PAGE_LOG2 = 12
_PAGE = 1 << _PAGE_LOG2
_SIGNATURE_ALIGN = 16
_CODEDIRECTORY_VERSION = 0x20400
_CODEDIRECTORY_FLAGS = CS_ADHOC | CS_LINKER_SIGNED
_U32_MAX = (1 << 32) - 1
_U64_MAX = (1 << 64) - 1


def _align8(n: int) -> int:
    return (n + 7) & ~7

# CodeDirectory v0x20400 fixed header (big-endian), before the identifier:
#   magic, length, version, flags, hashOffset, identOffset, nSpecialSlots,
#   nCodeSlots, codeLimit, hashSize/hashType/platform/pageSize, spare2,
#   scatterOffset, teamOffset, spare3, codeLimit64, execSegBase,
#   execSegLimit, execSegFlags
_CD_HEADER = ">IIIIIIIII4BIIIIQQQQ"
_CD_HEADER_SIZE = struct.calcsize(_CD_HEADER)
assert _CD_HEADER_SIZE == 88


class CodesignError(Exception):
    """The binary's signature is outside the verified shape."""


@dataclass(frozen=True)
class SignatureParams:
    dataoff: int
    datasize: int
    version: int
    flags: int
    identifier: bytes
    exec_seg_base: int
    exec_seg_limit: int
    exec_seg_flags: int


def _parse_object(data: bytes) -> spec.MachOObject:
    if not isinstance(data, bytes):
        raise CodesignError("Mach-O data must be bytes")
    try:
        return spec.parse_object(data)
    except (spec.MachOFormatError, struct.error) as exc:
        raise CodesignError(f"invalid Mach-O: {exc}") from exc


def _unpack_from(fmt: str, data: bytes, offset: int, what: str) -> tuple:
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        raise CodesignError(f"truncated {what}")
    return struct.unpack_from(fmt, data, offset)


def _find_signature_command(data: bytes) -> tuple[int, int, int]:
    """-> (load-command file offset, dataoff, datasize)."""
    obj = _parse_object(data)
    matches = [lc for lc in obj.commands if lc.cmd == spec.LC_CODE_SIGNATURE]
    if not matches:
        raise CodesignError("binary has no LC_CODE_SIGNATURE")
    if len(matches) != 1:
        raise CodesignError(
            f"binary has {len(matches)} LC_CODE_SIGNATURE commands; expected 1"
        )
    lc = matches[0]
    if lc.cmdsize != spec.LINKEDIT_DATA_COMMAND.size:
        raise CodesignError(
            f"LC_CODE_SIGNATURE has bad cmdsize {lc.cmdsize}; expected "
            f"{spec.LINKEDIT_DATA_COMMAND.size}"
        )
    dataoff, datasize = _unpack_from(
        "<II", lc.raw, 8, "LC_CODE_SIGNATURE"
    )
    if dataoff == 0 or datasize == 0:
        raise CodesignError("LC_CODE_SIGNATURE names an empty signature region")
    if dataoff % _SIGNATURE_ALIGN:
        raise CodesignError(
            f"LC_CODE_SIGNATURE dataoff is not {_SIGNATURE_ALIGN}-byte aligned"
        )
    if dataoff > len(data) or datasize > len(data) - dataoff:
        raise CodesignError("LC_CODE_SIGNATURE range is outside the file")
    if dataoff + datasize != len(data):
        raise CodesignError(
            "linker-style signature must occupy the end of the file"
        )
    if datasize != _align8(datasize):
        raise CodesignError("LC_CODE_SIGNATURE datasize is not 8-byte aligned")
    commands_end = spec.MACH_HEADER_64.size + obj.header["sizeofcmds"]
    if commands_end > len(data):
        raise CodesignError("load-command region is outside the file")
    if dataoff < commands_end:
        raise CodesignError("signature overlaps the load-command region")
    return lc.offset, dataoff, datasize


def parse_signature(data: bytes) -> SignatureParams:
    _lc_off, dataoff, datasize = _find_signature_command(data)
    blob = data[dataoff:dataoff + datasize]
    magic, super_len, count = _unpack_from(
        ">III", blob, 0, "embedded-signature header"
    )
    if magic != CSMAGIC_EMBEDDED_SIGNATURE:
        raise CodesignError(f"bad superblob magic {magic:#x}")
    if count != 1:
        raise CodesignError(
            f"signature has {count} slots; only one CodeDirectory is verified"
        )
    directory_end = 12 + count * 8
    slot_type, cd_off = _unpack_from(
        ">II", blob, 12, "embedded-signature slot directory"
    )
    if slot_type != CSSLOT_CODEDIRECTORY:
        raise CodesignError(
            f"signature slot has type {slot_type}; expected CodeDirectory"
        )
    if cd_off != directory_end:
        raise CodesignError(
            f"CodeDirectory starts at {cd_off}; expected {directory_end}"
        )
    if super_len < cd_off + _CD_HEADER_SIZE or super_len > datasize:
        raise CodesignError(
            f"bad superblob length {super_len} for datasize {datasize}"
        )
    if _align8(super_len) != datasize:
        raise CodesignError(
            f"superblob length {super_len} does not match padded datasize "
            f"{datasize}"
        )
    if any(blob[super_len:]):
        raise CodesignError("signature has nonzero file padding")

    fields = _unpack_from(
        _CD_HEADER, blob[:super_len], cd_off, "CodeDirectory header"
    )
    (magic, cd_len, version, flags, hash_off, ident_off, nspecial, ncode,
     code_limit, hash_size, hash_type, platform, page_size, spare2,
     scatter, team, spare3, code_limit64, seg_base, seg_limit,
     seg_flags) = fields
    if magic != CSMAGIC_CODEDIRECTORY:
        raise CodesignError(f"bad CodeDirectory magic {magic:#x}")
    if cd_len < _CD_HEADER_SIZE or cd_off + cd_len != super_len:
        raise CodesignError(
            f"CodeDirectory length {cd_len} does not fill the superblob"
        )
    if version != _CODEDIRECTORY_VERSION:
        raise CodesignError(
            f"unverified CodeDirectory version {version:#x}; expected "
            f"{_CODEDIRECTORY_VERSION:#x}"
        )
    if flags != _CODEDIRECTORY_FLAGS:
        raise CodesignError(
            f"unverified CodeDirectory flags {flags:#x}; expected "
            f"{_CODEDIRECTORY_FLAGS:#x}"
        )
    if (hash_size, hash_type, page_size) != (32, CS_HASHTYPE_SHA256, _PAGE_LOG2):
        raise CodesignError(
            f"unverified hash shape: size {hash_size} type {hash_type} "
            f"page 2^{page_size}"
        )
    if nspecial != 0:
        raise CodesignError(
            f"{nspecial} special slots present; only linker-style "
            "signatures (0 special slots) are verified"
        )
    if any((platform, spare2, scatter, team, spare3, code_limit64)):
        raise CodesignError(
            "platform, spare, scatter, team, and codeLimit64 fields must be zero"
        )
    if code_limit != dataoff:
        raise CodesignError(
            f"CodeDirectory codeLimit {code_limit} does not match signature "
            f"offset {dataoff}"
        )
    expected_ncode = (code_limit + _PAGE - 1) // _PAGE
    if ncode != expected_ncode:
        raise CodesignError(
            f"CodeDirectory has {ncode} code slots; expected {expected_ncode}"
        )
    if ident_off != _CD_HEADER_SIZE:
        raise CodesignError(
            f"CodeDirectory identifier starts at {ident_off}; expected "
            f"{_CD_HEADER_SIZE}"
        )
    if hash_off <= ident_off or hash_off > cd_len:
        raise CodesignError(
            f"CodeDirectory hash offset {hash_off} is outside its payload"
        )
    if hash_off + ncode * hash_size != cd_len:
        raise CodesignError(
            "CodeDirectory hash table length does not match its code slots"
        )
    # Do not compare the digest bytes here: resign() intentionally parses a
    # structurally valid old signature after the signed pages have changed.
    # The freshly built blob below hashes every page from the changed bytes.
    ident_start = cd_off + ident_off
    hash_start = cd_off + hash_off
    ident_end = blob.find(b"\0", ident_start, hash_start)
    if ident_end < 0 or ident_end != hash_start - 1:
        raise CodesignError(
            "CodeDirectory identifier is not a single NUL-terminated field"
        )
    if ident_end == ident_start:
        raise CodesignError("CodeDirectory identifier is empty")
    return SignatureParams(
        dataoff=dataoff, datasize=datasize, version=version, flags=flags,
        identifier=blob[ident_start:ident_end],
        exec_seg_base=seg_base, exec_seg_limit=seg_limit,
        exec_seg_flags=seg_flags,
    )


def _validate_identifier(identifier: bytes) -> None:
    if not isinstance(identifier, bytes):
        raise CodesignError("CodeDirectory identifier must be bytes")
    if not identifier:
        raise CodesignError("CodeDirectory identifier must not be empty")
    if b"\0" in identifier:
        raise CodesignError("CodeDirectory identifier must not contain NUL")


def _validate_u64(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CodesignError(f"{name} must be an integer")
    if value < 0 or value > _U64_MAX:
        raise CodesignError(f"{name} does not fit in an unsigned 64-bit field")


def _validate_u32(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CodesignError(f"{name} must be an integer")
    if value < 0 or value > _U32_MAX:
        raise CodesignError(f"{name} does not fit in an unsigned 32-bit field")


def _signature_size(code_limit: int, identifier: bytes) -> int:
    n_code = (code_limit + _PAGE - 1) // _PAGE
    hash_off = _CD_HEADER_SIZE + len(identifier) + 1
    cd_len = hash_off + n_code * 32
    super_len = 20 + cd_len
    if any(value > _U32_MAX for value in (n_code, hash_off, cd_len, super_len)):
        raise CodesignError("signature metadata does not fit in 32-bit fields")
    padded_size = _align8(super_len)
    if padded_size > _U32_MAX:
        raise CodesignError("padded signature size does not fit in datasize")
    return padded_size


def build_signature(
    hashed_region: bytes,
    *,
    identifier: bytes,
    exec_seg_base: int,
    exec_seg_limit: int,
    exec_seg_flags: int,
    version: int = _CODEDIRECTORY_VERSION,
    flags: int = _CODEDIRECTORY_FLAGS,
) -> bytes:
    """Build the complete SuperBlob for a file whose signed range is
    `hashed_region` (offset 0 up to the signature's own file offset)."""
    if not isinstance(hashed_region, bytes):
        raise CodesignError("hashed_region must be bytes")
    _validate_identifier(identifier)
    _validate_u32("version", version)
    _validate_u32("flags", flags)
    if version != _CODEDIRECTORY_VERSION:
        raise CodesignError(
            f"unsupported CodeDirectory version {version:#x}; expected "
            f"{_CODEDIRECTORY_VERSION:#x}"
        )
    if flags != _CODEDIRECTORY_FLAGS:
        raise CodesignError(
            f"unsupported CodeDirectory flags {flags:#x}; expected "
            f"{_CODEDIRECTORY_FLAGS:#x}"
        )
    for name, value in (
        ("exec_seg_base", exec_seg_base),
        ("exec_seg_limit", exec_seg_limit),
        ("exec_seg_flags", exec_seg_flags),
    ):
        _validate_u64(name, value)

    code_limit = len(hashed_region)
    if code_limit > _U32_MAX:
        raise CodesignError(
            "signed region exceeds 32-bit codeLimit; codeLimit64 is unsupported"
        )
    if code_limit % _SIGNATURE_ALIGN:
        raise CodesignError(
            f"signature offset must be {_SIGNATURE_ALIGN}-byte aligned"
        )
    n_code = (code_limit + _PAGE - 1) // _PAGE
    ident_z = identifier + b"\0"
    ident_off = _CD_HEADER_SIZE
    hash_off = ident_off + len(ident_z)
    cd_len = hash_off + n_code * 32
    expected_size = _signature_size(code_limit, identifier)

    cd = struct.pack(
        _CD_HEADER,
        CSMAGIC_CODEDIRECTORY, cd_len, version, flags,
        hash_off, ident_off, 0, n_code, code_limit,
        32, CS_HASHTYPE_SHA256, 0, _PAGE_LOG2, 0,
        0, 0, 0, 0,
        exec_seg_base, exec_seg_limit, exec_seg_flags,
    )
    hashes = b"".join(
        hashlib.sha256(hashed_region[i:i + _PAGE]).digest()
        for i in range(0, code_limit, _PAGE)
    )
    cd_blob = cd + ident_z + hashes
    if len(cd_blob) != cd_len:
        raise CodesignError("internal CodeDirectory length mismatch")

    super_len = 12 + 8 + cd_len
    superblob = struct.pack(
        ">IIIII", CSMAGIC_EMBEDDED_SIGNATURE, super_len, 1,
        CSSLOT_CODEDIRECTORY, 20,
    )
    # The blob's own length field is exact; the *file* region it occupies is
    # padded to 8 bytes, and LC_CODE_SIGNATURE.datasize names the padded
    # size (verified against ld: 399-byte blobs are stored as 400,
    # 401-byte ones as 408).
    blob = superblob + cd_blob
    padded = blob + b"\0" * (_align8(len(blob)) - len(blob))
    if len(padded) != expected_size:
        raise CodesignError("internal signature length mismatch")
    return padded


def resign(data: bytes, identifier: bytes | None = None) -> bytes:
    """Recompute a binary's ad-hoc signature after its contents changed."""
    params = parse_signature(data)
    ident = params.identifier if identifier is None else identifier
    _validate_identifier(ident)

    # Predict the new blob size so the load commands can be patched BEFORE
    # hashing — dataoff sits in page 0, inside the signed range.
    new_size = _signature_size(params.dataoff, ident)

    patched = bytearray(data[:params.dataoff])
    lc_off, _dataoff, _datasize = _find_signature_command(data)
    if lc_off + spec.LINKEDIT_DATA_COMMAND.size > len(patched):
        raise CodesignError("LC_CODE_SIGNATURE overlaps its signature payload")
    struct.pack_into("<II", patched, lc_off + 8, params.dataoff, new_size)

    # __LINKEDIT filesize must cover the signature exactly.
    obj = _parse_object(data)
    linkedits = [
        lc for lc in obj.commands
        if lc.cmd == spec.LC_SEGMENT_64
        and lc.body["segname_str"] == "__LINKEDIT"
    ]
    if len(linkedits) != 1:
        raise CodesignError(
            f"binary has {len(linkedits)} __LINKEDIT segments; expected 1"
        )
    linkedit = linkedits[0]
    if (
        linkedit.cmdsize != spec.SEGMENT_COMMAND_64.size
        or linkedit.body["nsects"] != 0
        or linkedit.sections
    ):
        raise CodesignError(
            "__LINKEDIT must be a sectionless segment command of standard size"
        )
    if linkedit.offset + linkedit.cmdsize > len(patched):
        raise CodesignError("__LINKEDIT command overlaps the signature payload")
    fileoff = linkedit.body["fileoff"]
    filesize = linkedit.body["filesize"]
    vmsize = linkedit.body["vmsize"]
    if fileoff > params.dataoff:
        raise CodesignError("signature starts before __LINKEDIT")
    if fileoff + filesize != len(data):
        raise CodesignError("__LINKEDIT does not end at the end of the file")
    if filesize > vmsize:
        raise CodesignError("__LINKEDIT filesize exceeds its vmsize")
    new_filesize = params.dataoff + new_size - fileoff
    if new_filesize > vmsize:
        raise CodesignError("new signature does not fit in __LINKEDIT vmsize")
    struct.pack_into(
        "<Q", patched,
        linkedit.offset + spec.SEGMENT_COMMAND_64.offset_of("filesize"),
        new_filesize,
    )

    blob = build_signature(
        bytes(patched),
        identifier=ident,
        exec_seg_base=params.exec_seg_base,
        exec_seg_limit=params.exec_seg_limit,
        exec_seg_flags=params.exec_seg_flags,
        version=params.version,
        flags=params.flags,
    )
    if len(blob) != new_size:
        raise CodesignError("internal resign size mismatch")
    return bytes(patched) + blob
