"""Actual tuple setters retain ownership while avoiding no-op completion scans."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/py_runtime"
FRESH_ALLOC = 16384


class _PortMemory:
    """Unsafe/extern oracle; tuple construction and cycle checks stay real."""

    def __init__(self, module, backend, initialized):
        self.module = module
        self.backend = backend
        self.initialized = initialized
        self.memory = {}
        self.next_address = 4096
        self.events = []
        self.publications = []
        self.reads = []

    def allocate(self, size, tag, flags):
        address = self.next_address
        self.next_address += size + 64
        self.memory[address] = 1
        self.memory[address + 8] = tag
        # Backend1/2 tracking also uses FRESH_ALLOC as allocation grace;
        # only GC4's bit is cleared by constructor publication.
        self.memory[address + 12] = flags | (FRESH_ALLOC if self.backend in (1, 2, 4) else 0)
        return address

    def read_i32(self, address, offset):
        if address == -1:
            return int(self.initialized)
        if address == -2:
            return self.backend
        return self.memory.get(address + offset, 0)

    def get_backend(self):
        self.initialized = True
        return self.backend

    def incref(self, item):
        self.events.append(("incref", item))
        if item:
            self.memory[item] += 1

    def store(self, owner, slot, item):
        self.events.append(("barrier", owner, slot, item))
        self.get_backend()
        self.incref(item)
        self.memory[slot] = item

    def load(self, owner, slot):
        self.reads.append((owner, slot))
        return self.memory.get(slot, 0)

    def track(self, owner):
        self.events.append(("track", owner))
        self.memory[owner + 12] |= self.module.PY_FLAG_GC_TRACKED

    def publish(self, owner):
        length = self.memory[owner + self.module.PYTUPLEOBJECT_LEN_OFFSET]
        snapshot = tuple(
            self.memory.get(owner + self.module.PYTUPLEOBJECT_ITEMS_OFFSET + index * 8, 0)
            for index in range(length)
        )
        self.publications.append((owner, snapshot))
        if self.backend == 4:
            self.memory[owner + 12] &= ~FRESH_ALLOC

    def memset(self, address, value, size):
        assert value == 0 and size % 8 == 0
        for offset in range(0, size, 8):
            self.memory[address + offset] = 0

    def install(self, monkeypatch):
        primitives = {
            "ptr_is_null": lambda value: value == 0,
            "null": lambda: 0,
            "ptr_add": lambda value, offset: value + offset,
            "is_tagged_int": lambda value: False,
            "global_addr": lambda name: {
                "pcc_gc_config_initialized": -1, "pcc_gc_backend_selected": -2,
            }[name],
            "load_i32": self.read_i32,
            "load_i64": lambda address, offset: self.memory.get(address + offset, 0),
            "store_i64": lambda address, offset, value: self.memory.__setitem__(address + offset, value),
            "store_ptr": lambda address, offset, value: self.memory.__setitem__(address + offset, value),
            "memset": self.memset,
            "py_incref": self.incref,
            "py_gc_track": self.track,
            "pcc_gc_alloc": self.allocate,
            "pcc_gc_backend": self.get_backend,
            "pcc_gc_store_ptr": self.store,
            "pcc_gc_load_ptr": self.load,
            "pcc_gc_pointer_is_managed": lambda address: address in self.memory,
            "pcc_gc_publish_initialized": self.publish,
        }
        for name, value in primitives.items():
            monkeypatch.setattr(self.module, name, value)


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("initialized", [True, False])
def test_python_port_completion_reads_and_publication_order(monkeypatch, backend, initialized):
    from pcc.py_runtime.py import py_tuple

    memory = _PortMemory(py_tuple, backend, initialized)
    memory.install(monkeypatch)
    owner = py_tuple.py_tuple_new(4)
    child = memory.allocate(24, py_tuple.PY_TYPE_LIST, 0)
    initial_fresh = bool(memory.memory[owner + 12] & FRESH_ALLOC)
    read_counts = []
    for index, value in ((2, child), (0, child), (1, 0), (3, child), (1, child)):
        before = len(memory.reads)
        py_tuple.py_tuple_set_item(owner, index, value)
        read_counts.append(len(memory.reads) - before)
        filled = all(memory.memory[owner + 24 + index * 8] for index in range(4))
        expected_fresh = not filled if backend == 4 else initial_fresh
        assert bool(memory.memory[owner + 12] & FRESH_ALLOC) == expected_fresh
        if not filled:
            assert memory.publications == []
    assert memory.memory[child] == 5
    assert memory.memory[owner + 12] & py_tuple.PY_FLAG_GC_TRACKED
    assert [event for event in memory.events if event[0] == "track"] == [("track", owner)]
    barriers = [event for event in memory.events if event[0] == "barrier"]
    assert len(barriers) == (5 if backend != 0 else int(not initialized))
    # A list child exercises real cycle tracking without nested-tuple reads.
    assert read_counts == ([1, 2, 2, 2, 4] if backend == 4 else [0] * 5)
    assert memory.publications == ([(owner, (child,) * 4)] if backend == 4 else [])


@pytest.mark.parametrize("backend", range(5))
def test_python_port_empty_null_and_invalid_stores_keep_publication_guards(monkeypatch, backend):
    from pcc.py_runtime.py import py_tuple

    memory = _PortMemory(py_tuple, backend, True)
    memory.install(monkeypatch)
    empty = py_tuple.py_tuple_new(0)
    assert memory.publications == [(empty, ())]
    assert bool(memory.memory[empty + 12] & FRESH_ALLOC) == (backend in (1, 2))
    owner = py_tuple.py_tuple_new(1)
    before = (list(memory.events), list(memory.publications), dict(memory.memory))
    py_tuple.py_tuple_set_item(0, 0, 0)
    py_tuple.py_tuple_set_item(empty, 0, 0)
    py_tuple.py_tuple_set_item(owner, -1, 0)
    py_tuple.py_tuple_set_item(owner, 1, 0)
    assert (memory.events, memory.publications, memory.memory) == before
    assert memory.reads == []
    py_tuple.py_tuple_set_item(owner, 0, 0)
    assert memory.publications == [(empty, ())]
    assert len(memory.reads) == (1 if backend == 4 else 0)


def test_python_port_publication_backend_is_selected_per_call(monkeypatch):
    from pcc.py_runtime.py import py_tuple

    memory = _PortMemory(py_tuple, 0, True)
    memory.install(monkeypatch)
    child = memory.allocate(24, py_tuple.PY_TYPE_LIST, 0)
    first = py_tuple.py_tuple_new(1)
    py_tuple.py_tuple_set_item(first, 0, child)
    assert memory.reads == [] and memory.publications == []
    memory.backend = 4
    partial = py_tuple.py_tuple_new(2)
    py_tuple.py_tuple_set_item(partial, 0, child)
    assert len(memory.reads) == 2 and memory.publications == []
    memory.backend = 3
    other = py_tuple.py_tuple_new(1)
    py_tuple.py_tuple_set_item(other, 0, child)
    assert len(memory.reads) == 2 and memory.publications == []
    memory.backend = 4
    py_tuple.py_tuple_set_item(partial, 1, child)
    assert len(memory.reads) == 4
    assert memory.publications == [(partial, (child, child))]


def test_python_port_none_object_fills_a_slot_but_null_pointer_does_not(monkeypatch):
    from pcc.py_runtime.py import py_tuple
    from pcc.py_runtime.py.py_abi_constants import PY_TYPE_NONE

    memory = _PortMemory(py_tuple, 4, True)
    memory.install(monkeypatch)
    owner = py_tuple.py_tuple_new(1)
    none_object = memory.allocate(16, PY_TYPE_NONE, 0)
    py_tuple.py_tuple_set_item(owner, 0, 0)
    assert memory.publications == []
    assert memory.memory[owner + 12] & FRESH_ALLOC
    py_tuple.py_tuple_set_item(owner, 0, none_object)
    assert memory.publications == [(owner, (none_object,))]
    assert not memory.memory[owner + 12] & FRESH_ALLOC


_C_DRIVER = r'''
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>

extern PyObject *probe_py_tuple_new(int64_t n);
extern void probe_py_tuple_set_item(PyObject *, int64_t, PyObject *);
static PyObject *watched;
static int64_t completion_reads;

PyObject *probe_tuple_load_ptr(PyObject *owner, PyObject **slot) {
    if (owner == watched) completion_reads++;
    return pcc_gc_load_ptr(owner, slot);
}

static int fresh(PyObject *owner) {
    return (py_header(owner)->flags & PY_FLAG_GC_FRESH_ALLOC) != 0;
}

static int selectable(PyObject *owner) {
    pcc_gc_reset_relocation_set();
    int result = (int)pcc_gc_backend4_relocation_set_add(owner);
    pcc_gc_reset_relocation_set();
    return result;
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    int backend = atoi(argv[1]);
    if (pcc_gc_set_backend(backend) != 0) return 3;
    PyObject *empty = probe_py_tuple_new(0);
    watched = probe_py_tuple_new(4);
    PyObject *child = py_list_new(0);
    if (!empty || !watched || !child) return 4;
    if (backend == 4 && fresh(empty)) return 5;
    if (backend == 4 && selectable(empty) != 1) return 6;
    int initial_fresh = fresh(watched);
    int64_t refcount_before = py_header(child)->refcount;
    probe_py_tuple_set_item(NULL, 0, child);
    probe_py_tuple_set_item(empty, 0, child);
    probe_py_tuple_set_item(watched, -1, child);
    probe_py_tuple_set_item(watched, 4, child);
    if (completion_reads || py_header(child)->refcount != refcount_before) return 7;
    int indexes[5] = {2, 0, 1, 3, 1};
    for (int step = 0; step < 5; step++) {
        int64_t before = completion_reads;
        probe_py_tuple_set_item(watched, indexes[step], step == 2 ? NULL : child);
        /* GC1/2 keep their allocation-grace bit until root seeding. Only
         * GC4 changes this bit when the constructor becomes complete. */
        int expected_fresh = backend == 4 ? step < 4 : initial_fresh;
        if (fresh(watched) != expected_fresh) return 10 + step;
        if (!(py_header(watched)->flags & PY_FLAG_GC_TRACKED)) return 20 + step;
        if (backend == 4 && selectable(watched) != (step == 4)) return 30 + step;
        printf("%lld\n", (long long)(completion_reads - before));
    }
    if (py_header(child)->refcount != refcount_before + 4) return 40;
    for (int index = 0; index < 4; index++) {
        if (((PyTupleObject *)watched)->items[index] != child) return 41;
    }
    py_decref(watched);
    watched = NULL;
    py_decref(child);
    py_decref(empty);
    puts("ownership-publication-ok");
    return 0;
}
'''


@pytest.fixture(scope="module")
def c_tuple_completion_probe(tmp_path_factory):
    archive_name = os.environ.get("PCC_RUNTIME_ARCHIVE")
    if not archive_name:
        pytest.fail("requires explicit immutable PCC_RUNTIME_ARCHIVE")
    archive = Path(archive_name).resolve(strict=True)
    tuple_source = RUNTIME / "src/py_tuple.c"
    source_bytes = tuple_source.read_bytes()
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    exported = re.findall(
        r"^(?:PyObject\s*\*|int64_t|void)\s*(py_tuple_\w+)\s*\(",
        source_bytes.decode(), re.MULTILINE,
    )
    assert len(exported) == len(set(exported))
    assert "py_tuple_new" in exported and "py_tuple_set_item" in exported
    tmp_path = tmp_path_factory.mktemp("tuple_completion_probe")
    wrapper = tmp_path / "instrumented_tuple.c"
    wrapper.write_text(
        "".join(f"#define {name} probe_{name}\n" for name in exported)
        + '#include "py_internal.h"\n'
        + "extern PyObject *probe_tuple_load_ptr(PyObject *, PyObject **);\n"
        + "#define pcc_gc_load_ptr probe_tuple_load_ptr\n"
        + f'#include "{tuple_source}"\n',
        encoding="utf-8",
    )
    driver = tmp_path / "driver.c"
    driver.write_text(_C_DRIVER, encoding="utf-8")
    cc = os.environ.get("CC", "cc")
    common = [cc, "-std=c11", "-O0", f"-I{RUNTIME / 'include'}", f"-I{RUNTIME / 'src'}"]
    instrumented = tmp_path / "instrumented_tuple.o"
    driver_object = tmp_path / "driver.o"
    for source, output in ((wrapper, instrumented), (driver, driver_object)):
        built = subprocess.run([*common, "-c", str(source), "-o", str(output)], capture_output=True, text=True, timeout=30)
        assert built.returncode == 0, built.stdout + built.stderr
    symbols = subprocess.run(["nm", "-g", str(instrumented)], capture_output=True, text=True, timeout=10)
    assert symbols.returncode == 0, symbols.stderr
    assert re.search(r"\bT _?probe_py_tuple_set_item$", symbols.stdout, re.MULTILINE)
    assert not re.search(r"\b_?py_tuple_\w+$", symbols.stdout, re.MULTILINE)
    driver_symbols = subprocess.run(["nm", "-u", str(driver_object)], capture_output=True, text=True, timeout=10)
    assert driver_symbols.returncode == 0, driver_symbols.stderr
    assert re.search(r"\b_?probe_py_tuple_set_item$", driver_symbols.stdout, re.MULTILINE)
    assert not re.search(r"\b_?py_tuple_set_item$", driver_symbols.stdout, re.MULTILINE)
    executable = tmp_path / "tuple_completion_probe"
    linked = subprocess.run([cc, str(driver_object), str(instrumented), str(archive), "-lm", "-o", str(executable)], capture_output=True, text=True, timeout=30)
    assert linked.returncode == 0, linked.stdout + linked.stderr
    assert tuple_source.read_bytes() == source_bytes
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == archive_hash
    return executable


@pytest.mark.pcc_gate(env="PCC_RUNTIME_ARCHIVE")
@pytest.mark.parametrize("backend", range(5))
def test_c_tuple_completion_reads_and_relocation_eligibility(c_tuple_completion_probe, backend):
    result = subprocess.run([str(c_tuple_completion_probe), str(backend)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    counts = [1, 2, 2, 2, 4] if backend == 4 else [0] * 5
    assert result.stdout == "".join(f"{count}\n" for count in counts) + "ownership-publication-ok\n"
