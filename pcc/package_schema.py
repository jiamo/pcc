"""Self-host-safe package manifest, wheel-tag, and capability contract."""

PACKAGE_MANIFEST_SCHEMA = "pcc.package-manifest.v1"
PACKAGE_MANIFEST_SCHEMA_VERSION = 1
PCC_NATIVE_PYTHON_TAG = "pcc3"
PCC_NATIVE_ABI_TAG = "pcc_native"
PCC_CAPI_HEADERS = (
    "Python.h",
    "structmember.h",
    "pymem.h",
    "frameobject.h",
    "pythread.h",
    "pyerrors.h",
    "abstract.h",
    "datetime.h",
)

CAMPAIGN_PROFILES = {
    "numpy-core-l6": {
        "root_parts": ("numpy", "_core", "tests"),
        "default_area": "core",
        "area": "numpy-core",
        "description": (
            "NumPy L6 useful core-test subset profile. It selects stable "
            "numpy/_core/tests files that map to L6.2-L6.6 feature domains; "
            "it does not mark those tests passing."
        ),
        "selection_rule": (
            "fixed NumPy L6 core-test filename profile under numpy/_core/tests"
        ),
        "files": {
            "test_multiarray.py": ("L6.2", "shape-strides-dtype"),
            "test_numeric.py": ("L6.2", "shape-strides-dtype"),
            "test_shape_base.py": ("L6.2", "shape-strides-dtype"),
            "test_dtype.py": ("L6.2", "shape-strides-dtype"),
            "test_array_coercion.py": ("L6.3", "scalar-coercion"),
            "test_scalarmath.py": ("L6.3", "scalar-types"),
            "test_indexing.py": ("L6.4", "indexing-slicing-broadcast"),
            "test_stride_tricks.py": ("L6.4", "indexing-slicing-broadcast"),
            "test_umath.py": ("L6.5", "ufunc-add-sub-mul-div"),
            "test_ufunc.py": ("L6.5", "ufunc-add-sub-mul-div"),
            "test_arrayprint.py": ("L6.6", "array-repr-print"),
        },
    }
}


def campaign_profile(name: str):
    return CAMPAIGN_PROFILES.get(name)


def pcc_native_wheel_tag(platform_tag: str) -> str:
    return PCC_NATIVE_PYTHON_TAG + "-" + PCC_NATIVE_ABI_TAG + "-" + platform_tag


def pcc_native_extension_suffix(platform_tag: str) -> str:
    """Return the explicit pcc-native extension ABI suffix for a platform."""

    return "." + pcc_native_wheel_tag(platform_tag) + ".so"


def _basename(path: str) -> str:
    last = -1
    i = 0
    while i < len(path):
        if path[i] == "/" or path[i] == "\\":
            last = i
        i += 1
    return path[last + 1 :]


def wheel_tag_fields(path: str):
    """Return ``[name, python, abi, platform]`` without host-only packaging."""
    text = str(path or "")
    base = _basename(text)
    stem = base[: len(base) - 4] if base.lower().endswith(".whl") else base
    parts = stem.split("-")
    if len(parts) >= 5 and text.lower().endswith(".whl"):
        return [parts[0], parts[len(parts) - 3], parts[len(parts) - 2], parts[-1]]
    return [parts[0] if len(parts) > 0 else stem, "", "", ""]


def wheel_tags(path: str) -> dict:
    fields = wheel_tag_fields(path)
    return {
        "python_tag": fields[1] or None,
        "abi_tag": fields[2] or None,
        "platform_tag": fields[3] or None,
    }


def execution_mode(abi_mode: str) -> str:
    if abi_mode == "libpython" or abi_mode == "cpython-compat":
        return "cpython-compat"
    return "pcc-native"


def capability_profile(
    abi_mode: str,
    has_artifact_scan: bool,
    links_libpython: bool,
    uses_cpython_extension_abi: bool,
) -> dict:
    mode = execution_mode(abi_mode)
    native_claim = (
        mode == "pcc-native"
        and has_artifact_scan
        and not links_libpython
        and not uses_cpython_extension_abi
    )
    return {
        "execution_mode": mode,
        "native_package_claim": native_claim,
        "no_libpython_runtime": (
            mode == "pcc-native"
            and not links_libpython
            and not uses_cpython_extension_abi
        ),
    }
