"""5-GC common production contract: valuebox pointer payload roots.

Part of the 5-GC Production Equality Rule (docs/goal/goal-prompt.md G-track) and
the value-model obligation: a boxed valueclass that crosses an object boundary
must trace pointer-bearing payload fields under every backend.

The program boxes ``Bag(items: list, label: str, count: int)`` and
``Holder(bag: Bag, label: str)`` through ``Any`` boundaries, container literals,
container mutation, object attribute stores, call boundaries, defaults,
function returns, method returns, decorated method returns, and property
returns. It runs
gc.collect(), mutates pointer payload lists through the boxed objects, runs
gc.collect() again, then reads the same fields back through dynamic attribute
access. A crash or wrong output means the valuebox object failed to
retain/update one of its pointer payloads, including nested valuebox payload
slots.
"""
from __future__ import annotations

import os
import subprocess

import pytest


_PROGRAM = (
    "import gc\n"
    "import pcc\n"
    "from typing import Any\n"
    "\n"
    "events = []\n"
    "\n"
    "class Track:\n"
    "    def __init__(self, name: str) -> None:\n"
    "        self.name = name\n"
    "\n"
    "    def __del__(self) -> None:\n"
    "        events.append('del:' + self.name)\n"
    "\n"
    "@pcc.valueclass\n"
    "class Bag:\n"
    "    items: list\n"
    "    label: str\n"
    "    count: int\n"
    "\n"
    "@pcc.valueclass\n"
    "class Holder:\n"
    "    bag: Bag\n"
    "    label: str\n"
    "\n"
    "@pcc.valueclass\n"
    "class FinalizerHolder:\n"
    "    item: Track\n"
    "    label: str\n"
    "\n"
    "class Cell:\n"
    "    def __init__(self):\n"
    "        self.value = None\n"
    "\n"
    "class Factory:\n"
    "    def make_any(self) -> Any:\n"
    "        return FinalizerHolder(Track('method-return-any-old'), 'method-return-any-holder')\n"
    "\n"
    "    def make_list(self) -> Any:\n"
    "        return [FinalizerHolder(Track('method-return-list-old'), 'method-return-list-holder')]\n"
    "\n"
    "    @staticmethod\n"
    "    def make_static_any() -> Any:\n"
    "        return FinalizerHolder(Track('staticmethod-return-any-old'), 'staticmethod-return-any-holder')\n"
    "\n"
    "    @classmethod\n"
    "    def make_class_list(cls) -> Any:\n"
    "        return [FinalizerHolder(Track('classmethod-return-list-old'), 'classmethod-return-list-holder')]\n"
    "\n"
    "    @property\n"
    "    def prop_any(self) -> Any:\n"
    "        return FinalizerHolder(Track('property-return-any-old'), 'property-return-any-holder')\n"
    "\n"
    "    @property\n"
    "    def prop_list(self) -> Any:\n"
    "        return [FinalizerHolder(Track('property-return-list-old'), 'property-return-list-holder')]\n"
    "\n"
    "def ident(x: Any) -> Any:\n"
    "    return x\n"
    "\n"
    "def first_vararg(*args):\n"
    "    return args[0]\n"
    "\n"
    "def kwarg_slot(**kwargs):\n"
    "    return kwargs['slot']\n"
    "\n"
    "def consume_any(x: Any) -> None:\n"
    "    print(x.label)\n"
    "\n"
    "def make_default_reader():\n"
    "    def read_default(x: Any = Holder(Bag([70], 'func-default-inner', 16), 'func-default-outer')) -> Any:\n"
    "        return x\n"
    "    return read_default\n"
    "\n"
    "def make_default_finalizer_reader():\n"
    "    def read_default(x: Any = FinalizerHolder(Track('func-default-old'), 'func-default-holder')) -> Any:\n"
    "        return x\n"
    "    return read_default\n"
    "\n"
    "def make_return_any_finalizer() -> Any:\n"
    "    return FinalizerHolder(Track('return-any-old'), 'return-any-holder')\n"
    "\n"
    "def make_return_list_finalizer() -> Any:\n"
    "    return [FinalizerHolder(Track('return-list-old'), 'return-list-holder')]\n"
    "\n"
    "def main():\n"
    "    bag = Bag([1, 2, 3], 'bag', 4)\n"
    "    d = ident(bag)\n"
    "    gc.collect()\n"
    "    d.items.append(5)\n"
    "    gc.collect()\n"
    "    print(len(d.items))\n"
    "    print(d.label)\n"
    "    print(d.items[3])\n"
    "    print(d.count)\n"
    "    print(type(d).__name__)\n"
    "    print(str(d))\n"
    "    print(repr(d))\n"
    "    nested_items = [1, 2]\n"
    "    holder = Holder(Bag(nested_items, 'inner', 5), 'outer')\n"
    "    hd = ident(holder)\n"
    "    gc.collect()\n"
    "    hd.bag.items.append(3)\n"
    "    gc.collect()\n"
    "    print(len(nested_items))\n"
    "    print(len(hd.bag.items) + hd.bag.count + len(hd.label))\n"
    "    print(hd.bag.count)\n"
    "    print(hd.label)\n"
    "    print(str(hd))\n"
    "    list_items = [10, 11]\n"
    "    boxed_list = [Holder(Bag(list_items, 'list-inner', 6), 'list-outer')]\n"
    "    boxed_tuple = (Holder(Bag([20], 'tuple-inner', 7), 'tuple-outer'),)\n"
    "    boxed_dict = {'k': Holder(Bag([30], 'dict-inner', 8), 'dict-outer')}\n"
    "    gc.collect()\n"
    "    boxed_list[0].bag.items.append(12)\n"
    "    boxed_tuple[0].bag.items.append(21)\n"
    "    boxed_dict['k'].bag.items.append(31)\n"
    "    gc.collect()\n"
    "    print(len(list_items))\n"
    "    print(len(boxed_list[0].bag.items))\n"
    "    print(boxed_list[0].bag.items[2])\n"
    "    print(boxed_list[0].bag.label)\n"
    "    print(boxed_list[0].label)\n"
    "    print(len(boxed_tuple[0].bag.items))\n"
    "    print(boxed_tuple[0].bag.items[1])\n"
    "    print(boxed_tuple[0].bag.label)\n"
    "    print(boxed_tuple[0].label)\n"
    "    print(len(boxed_dict['k'].bag.items))\n"
    "    print(boxed_dict['k'].bag.items[1])\n"
    "    print(boxed_dict['k'].bag.label)\n"
    "    print(boxed_dict['k'].label)\n"
    "    append_items = [40]\n"
    "    appended = []\n"
    "    appended.append(Holder(Bag(append_items, 'append-inner', 9), 'append-outer'))\n"
    "    replaced = [ident(Holder(Bag([41], 'replace-old-inner', 10), 'replace-old-outer'))]\n"
    "    replaced[0] = Holder(Bag([42], 'replace-inner', 11), 'replace-outer')\n"
    "    assigned = {}\n"
    "    assigned['slot'] = Holder(Bag([43], 'assign-inner', 12), 'assign-outer')\n"
    "    cell = Cell()\n"
    "    cell.value = Holder(Bag([44], 'attr-inner', 13), 'attr-outer')\n"
    "    gc.collect()\n"
    "    appended[0].bag.items.append(45)\n"
    "    replaced[0].bag.items.append(46)\n"
    "    assigned['slot'].bag.items.append(47)\n"
    "    cell.value.bag.items.append(48)\n"
    "    gc.collect()\n"
    "    print(len(append_items))\n"
    "    print(len(appended[0].bag.items))\n"
    "    print(appended[0].bag.items[1])\n"
    "    print(appended[0].bag.label)\n"
    "    print(appended[0].label)\n"
    "    print(len(replaced[0].bag.items))\n"
    "    print(replaced[0].bag.items[1])\n"
    "    print(replaced[0].bag.label)\n"
    "    print(replaced[0].label)\n"
    "    print(len(assigned['slot'].bag.items))\n"
    "    print(assigned['slot'].bag.items[1])\n"
    "    print(assigned['slot'].bag.label)\n"
    "    print(assigned['slot'].label)\n"
    "    print(len(cell.value.bag.items))\n"
    "    print(cell.value.bag.items[1])\n"
    "    print(cell.value.bag.label)\n"
    "    print(cell.value.label)\n"
    "    vararg_items = [50]\n"
    "    vararg_value = first_vararg(Holder(Bag(vararg_items, 'vararg-inner', 14), 'vararg-outer'))\n"
    "    kwarg_value = kwarg_slot(slot=Holder(Bag([60], 'kwarg-inner', 15), 'kwarg-outer'))\n"
    "    gc.collect()\n"
    "    vararg_value.bag.items.append(51)\n"
    "    kwarg_value.bag.items.append(61)\n"
    "    gc.collect()\n"
    "    print(len(vararg_items))\n"
    "    print(len(vararg_value.bag.items))\n"
    "    print(vararg_value.bag.items[1])\n"
    "    print(vararg_value.bag.label)\n"
    "    print(vararg_value.label)\n"
    "    print(len(kwarg_value.bag.items))\n"
    "    print(kwarg_value.bag.items[1])\n"
    "    print(kwarg_value.bag.label)\n"
    "    print(kwarg_value.label)\n"
    "    finalizer_cell = [FinalizerHolder(Track('old'), 'old-holder')]\n"
    "    gc.collect()\n"
    "    finalizer_cell[0] = FinalizerHolder(Track('new'), 'new-holder')\n"
    "    gc.collect()\n"
    "    print(finalizer_cell[0].label)\n"
    "    print(finalizer_cell[0].item.name)\n"
    "    print(len(events))\n"
    "    if len(events) > 0:\n"
    "        print(events[0])\n"
    "    tuple_cell = (FinalizerHolder(Track('tuple-old'), 'tuple-old-holder'),)\n"
    "    gc.collect()\n"
    "    tuple_cell = ()\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 1:\n"
    "        print(events[1])\n"
    "    dict_literal_cell = {'k': FinalizerHolder(Track('dict-lit-old'), 'dict-lit-old-holder')}\n"
    "    gc.collect()\n"
    "    dict_literal_cell = {}\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 2:\n"
    "        print(events[2])\n"
    "    append_cell = []\n"
    "    append_cell.append(FinalizerHolder(Track('append-old'), 'append-old-holder'))\n"
    "    gc.collect()\n"
    "    append_cell = []\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 3:\n"
    "        print(events[3])\n"
    "    dict_set_cell = {}\n"
    "    dict_set_cell['k'] = FinalizerHolder(Track('dict-set-old'), 'dict-set-old-holder')\n"
    "    gc.collect()\n"
    "    dict_set_cell = {}\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 4:\n"
    "        print(events[4])\n"
    "    attr_cell = Cell()\n"
    "    attr_cell.value = FinalizerHolder(Track('attr-old'), 'attr-old-holder')\n"
    "    gc.collect()\n"
    "    attr_cell.value = None\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 5:\n"
    "        print(events[5])\n"
    "    consume_any(FinalizerHolder(Track('call-arg-old'), 'call-arg-holder'))\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 6:\n"
    "        print(events[6])\n"
    "    default_reader = make_default_reader()\n"
    "    gc.collect()\n"
    "    default_value = default_reader()\n"
    "    gc.collect()\n"
    "    default_value.bag.items.append(71)\n"
    "    gc.collect()\n"
    "    print(len(default_value.bag.items))\n"
    "    print(default_value.bag.items[1])\n"
    "    print(default_value.bag.label)\n"
    "    print(default_value.label)\n"
    "    default_finalizer_reader = make_default_finalizer_reader()\n"
    "    gc.collect()\n"
    "    default_finalizer_value = default_finalizer_reader()\n"
    "    print(default_finalizer_value.label)\n"
    "    print(default_finalizer_value.item.name)\n"
    "    default_finalizer_value = None\n"
    "    default_finalizer_reader = None\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 7:\n"
    "        print(events[7])\n"
    "    return_any_value = make_return_any_finalizer()\n"
    "    gc.collect()\n"
    "    print(return_any_value.label)\n"
    "    print(return_any_value.item.name)\n"
    "    return_any_value = None\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 8:\n"
    "        print(events[8])\n"
    "    return_list_cell = make_return_list_finalizer()\n"
    "    gc.collect()\n"
    "    print(return_list_cell[0].label)\n"
    "    print(return_list_cell[0].item.name)\n"
    "    return_list_cell = []\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 9:\n"
    "        print(events[9])\n"
    "    factory = Factory()\n"
    "    method_any_value = factory.make_any()\n"
    "    gc.collect()\n"
    "    print(method_any_value.label)\n"
    "    print(method_any_value.item.name)\n"
    "    method_any_value = None\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 10:\n"
    "        print(events[10])\n"
    "    method_list_cell = factory.make_list()\n"
    "    gc.collect()\n"
    "    print(method_list_cell[0].label)\n"
    "    print(method_list_cell[0].item.name)\n"
    "    method_list_cell = []\n"
    "    factory = None\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 11:\n"
    "        print(events[11])\n"
    "    static_any_value = Factory.make_static_any()\n"
    "    gc.collect()\n"
    "    print(static_any_value.label)\n"
    "    print(static_any_value.item.name)\n"
    "    static_any_value = None\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 12:\n"
    "        print(events[12])\n"
    "    classmethod_list_cell = Factory.make_class_list()\n"
    "    gc.collect()\n"
    "    print(classmethod_list_cell[0].label)\n"
    "    print(classmethod_list_cell[0].item.name)\n"
    "    classmethod_list_cell = []\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 13:\n"
    "        print(events[13])\n"
    "    property_factory = Factory()\n"
    "    property_any_value = property_factory.prop_any\n"
    "    gc.collect()\n"
    "    print(property_any_value.label)\n"
    "    print(property_any_value.item.name)\n"
    "    property_any_value = None\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 14:\n"
    "        print(events[14])\n"
    "    property_list_cell = property_factory.prop_list\n"
    "    gc.collect()\n"
    "    print(property_list_cell[0].label)\n"
    "    print(property_list_cell[0].item.name)\n"
    "    property_list_cell = []\n"
    "    property_factory = None\n"
    "    gc.collect()\n"
    "    print(len(events))\n"
    "    if len(events) > 15:\n"
    "        print(events[15])\n"
    "\n"
    "main()\n"
)
_EXPECTED = [
    "4",
    "bag",
    "5",
    "4",
    "Bag",
    "Bag(items=[1, 2, 3, 5], label='bag', count=4)",
    "Bag(items=[1, 2, 3, 5], label='bag', count=4)",
    "3",
    "13",
    "5",
    "outer",
    "Holder(bag=Bag(items=[1, 2, 3], label='inner', count=5), label='outer')",
    "3",
    "3",
    "12",
    "list-inner",
    "list-outer",
    "2",
    "21",
    "tuple-inner",
    "tuple-outer",
    "2",
    "31",
    "dict-inner",
    "dict-outer",
    "2",
    "2",
    "45",
    "append-inner",
    "append-outer",
    "2",
    "46",
    "replace-inner",
    "replace-outer",
    "2",
    "47",
    "assign-inner",
    "assign-outer",
    "2",
    "48",
    "attr-inner",
    "attr-outer",
    "2",
    "2",
    "51",
    "vararg-inner",
    "vararg-outer",
    "2",
    "61",
    "kwarg-inner",
    "kwarg-outer",
    "new-holder",
    "new",
    "1",
    "del:old",
    "2",
    "del:tuple-old",
    "3",
    "del:dict-lit-old",
    "4",
    "del:append-old",
    "5",
    "del:dict-set-old",
    "6",
    "del:attr-old",
    "call-arg-holder",
    "7",
    "del:call-arg-old",
    "2",
    "71",
    "func-default-inner",
    "func-default-outer",
    "func-default-holder",
    "func-default-old",
    "8",
    "del:func-default-old",
    "return-any-holder",
    "return-any-old",
    "9",
    "del:return-any-old",
    "return-list-holder",
    "return-list-old",
    "10",
    "del:return-list-old",
    "method-return-any-holder",
    "method-return-any-old",
    "11",
    "del:method-return-any-old",
    "method-return-list-holder",
    "method-return-list-old",
    "12",
    "del:method-return-list-old",
    "staticmethod-return-any-holder",
    "staticmethod-return-any-old",
    "13",
    "del:staticmethod-return-any-old",
    "classmethod-return-list-holder",
    "classmethod-return-list-old",
    "14",
    "del:classmethod-return-list-old",
    "property-return-any-holder",
    "property-return-any-old",
    "15",
    "del:property-return-any-old",
    "property-return-list-holder",
    "property-return-list-old",
    "16",
    "del:property-return-list-old",
]


@pytest.fixture(scope="module")
def _valuebox_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_valuebox")
    src = tmp / "valuebox.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "valuebox_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


@pytest.mark.parametrize("backend", ["0", "1", "2", "3", "4"])
def test_valuebox_pointer_payload_survives_gc(_valuebox_exe, backend):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    run = subprocess.run(
        [_valuebox_exe],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert run.returncode == 0, (
        f"backend #{backend} rc={run.returncode}: {run.stderr.strip()[:200]}"
    )
    assert run.stdout.splitlines()[: len(_EXPECTED)] == _EXPECTED, run.stdout
