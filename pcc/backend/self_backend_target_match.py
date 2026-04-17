from __future__ import annotations

"""Pure target-triple matchers for the self backend."""


def is_aarch64_darwin_triple(triple: str) -> bool:
    triple = triple.lower()
    return (
        (triple.startswith("arm64-") or triple.startswith("aarch64-"))
        and "apple" in triple
        and "darwin" in triple
    )


def is_x86_64_linux_triple(triple: str) -> bool:
    triple = triple.lower()
    return (
        (triple.startswith("x86_64-") or triple.startswith("amd64-"))
        and ("linux" in triple or "gnu" in triple)
    )
