"""Process-pool worker: assemble one self-backend .s to encoded native bytes.

Lives in an importable module (not the pcc_link_macho.py script) so
multiprocessing's spawn start method can resolve it by qualified name no
matter what ``__main__`` is — running the driver under cProfile or any other
wrapper module must not break the pool. The driver passes file paths, not
assembly text, to keep the inter-process payload small; the encoded result
is exactly the .pco payload the incremental cache stores.
"""

from __future__ import annotations


def assemble_asm_text_to_encoded(assembly: str) -> bytes:
    import gc

    from .arm64_asm_driver import assemble_file
    from .native_object import NativeObject, encode_native_object

    # Batch worker: one file per process, millions of short-lived objects.
    # The cycle collector only adds tracing overhead here (acyclic objects),
    # so disable it for the duration of the job.
    try:
        gc.freeze()
    except Exception:
        pass
    gc.disable()
    sections, undefined = assemble_file(assembly)
    assembly = ""
    native = NativeObject.from_sections(sections, undefined=undefined)
    del sections
    del undefined
    encoded = encode_native_object(native)
    del native
    return encoded


def assemble_asm_path_to_encoded(path: str) -> bytes:
    with open(path, "r", encoding="utf-8") as stream:
        assembly = stream.read()
    return assemble_asm_text_to_encoded(assembly)


__all__ = ["assemble_asm_path_to_encoded", "assemble_asm_text_to_encoded"]
