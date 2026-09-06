from __future__ import annotations

"""Pure target-triple matchers for the self backend."""


def _target_components(triple: str) -> list[str]:
    components = triple.lower().split("-")
    if len(components) < 2 or len(components) > 4:
        return []
    for component in components:
        if not component:
            return []
        for char in component:
            if char not in "abcdefghijklmnopqrstuvwxyz0123456789_.":
                return []
    return components


def _matches_versioned_os(component: str, name: str) -> bool:
    if component == name:
        return True
    if not component.startswith(name):
        return False
    version = component[len(name) :]
    for part in version.split("."):
        if not part:
            return False
        for char in part:
            if char not in "0123456789":
                return False
    return True


def is_aarch64_darwin_triple(triple: str) -> bool:
    components = _target_components(triple)
    if len(components) < 3:
        return False
    return (
        (components[0] == "arm64" or components[0] == "aarch64")
        and components[1] == "apple"
        and (
            _matches_versioned_os(components[2], "darwin")
            or _matches_versioned_os(components[2], "macosx")
        )
    )


def is_x86_64_linux_triple(triple: str) -> bool:
    components = _target_components(triple)
    if not components:
        return False
    if components[0] != "x86_64" and components[0] != "amd64":
        return False
    # Keep unambiguous compact arch-OS and arch-OS-ABI aliases. In the
    # canonical arch-vendor-OS[-ABI] form only the OS field selects Linux;
    # GNU is an ABI/environment label also used by Windows and other OSes.
    if len(components) == 2:
        return _matches_versioned_os(components[1], "linux")
    if len(components) == 3 and components[2] in ("gnu", "musl"):
        return _matches_versioned_os(components[1], "linux")
    return _matches_versioned_os(components[2], "linux")
