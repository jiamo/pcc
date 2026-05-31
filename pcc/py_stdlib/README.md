# pcc/py_stdlib — native-compilable stdlib replacements

P6C.4 requirement: pcc's self-host binary must not depend on
CPython's stdlib. Every stdlib module that pcc's own source imports
gets either (a) a pure-Python replacement here, compilable by pcc,
or (b) an extern-C binding declared under `pcc/extern/` + a small
wrapper here.

Scope is narrow — **only** the surface area pcc itself uses. We are
not rebuilding all of CPython.

## Modules

| Module | Status | Notes |
|---|---|---|
| `sys` | skeleton | argv, exit, platform, version |
| `os` | skeleton | env, getcwd, exists, path submodule |
| `os.path` | skeleton | join, basename, dirname, exists |
| `io` | skeleton | StringIO minimal |
| `re` | stub | binds PCRE2 via extern; surface matches CPython's `re` |
| `json` | stub | loads/dumps for JSON subset used by pcc |
| `math` | stub | binds libm via extern (sqrt, pow, floor, ...) |
| `functools` | skeleton | lru_cache, wraps, partial |
| `itertools` | skeleton | chain, repeat, islice |
| `collections` | skeleton | OrderedDict, defaultdict, namedtuple, deque |
| `pathlib` | skeleton | PurePath, Path |
| `hashlib` | stub | binds OpenSSL via extern |
| `time` | stub | binds libc via extern (time, monotonic, sleep) |
| `string` | skeleton | ascii_lowercase/uppercase/digits constants |
| `base64` | skeleton | b64encode, b64decode |
| `urllib.parse` | skeleton | package-style native dotted import; quote, unquote, urlparse subset |
| `typing` | noop+ | Generic/Protocol/TypeVar/NewType/Annotated markers, get_origin/get_args |
| `types` | skeleton | SimpleNamespace, ModuleType, MappingProxyType, marker type aliases |
| `abc` | skeleton+ | ABCMeta, abstract decorators, register/cache token |
| `enum` | skeleton+ | Enum/IntEnum, auto, unique, iteration/value lookup |
| `inspect` | skeleton | signature, predicates, getmembers/getdoc/unwrap |
| `weakref` | skeleton | ref/proxy/WeakValueDictionary/WeakKeyDictionary/WeakSet/finalize |
| `dataclasses` | skeleton | @dataclass decorator |
| `builtins` | skeleton | int/str/bytes/bool/list/dict/... constructors |

Each `status=skeleton` module has a real module file with API shape
but bodies raising `NotImplementedError` on untested paths. `status=stub`
modules have the extern bindings in place but no tests yet.

## Audit

```
python scripts/audit_selfhost.py pcc -v | grep unstubbed-import
```

Should hit zero once every import in `pcc/*.py` resolves to one of
the modules above (or to the extern / llvm_capi scaffolds).

## Build

Each module file is ordinary Python. pcc compiles them through the
same frontend it uses for user code. They live on-disk under
`pcc/py_stdlib/<name>.py`; when the self-hosted binary starts up it
reads its own stdlib from disk via the parser — no runtime load
from CPython.

Eventually the stdlib ships embedded in the pcc binary (bytecode-
style compilation of the Python source into the executable segment).
That's P6C.6-adjacent work and not yet wired.
