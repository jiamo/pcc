"""5-GC contract: direct valueclass payload locals root pointer fields.

ValueBox tests cover object-boundary boxing. This gate keeps a valueclass in
the direct payload form and verifies that object pointer fields embedded in the
payload remain live across explicit collections under every GC backend.
"""
from __future__ import annotations

import os
import subprocess
import textwrap

import pytest


_PROGRAM = textwrap.dedent(
    """
    import gc
    import sys
    import pcc

    finalizer_events = []

    class Track:
        def __init__(self, name: str) -> None:
            self.name = name

        def __del__(self) -> None:
            if self.name == 'shutdown':
                sys.stderr.write('del:' + self.name + '\\n')
            else:
                finalizer_events.append('del:' + self.name)

    @pcc.valueclass
    class Bag:
        items: list
        label: str

        def touch_self(self) -> None:
            gc.collect()
            self.items.append(5)
            gc.collect()
            print(len(self.items))
            print(self.items[4])
            print(self.label)

    @pcc.valueclass
    class Nested:
        items: list
        label: str

    @pcc.valueclass
    class Holder:
        nested: Nested
        trailer: list
        title: str

        def touch_holder(self) -> None:
            gc.collect()
            self.nested.items.append(8)
            self.trailer.append(9)
            gc.collect()
            print(len(self.nested.items))
            print(self.nested.items[2])
            print(self.nested.label)
            print(len(self.trailer))
            print(self.trailer[2])
            print(self.title)

    def touch(b: Bag) -> None:
        gc.collect()
        b.items.append(4)
        gc.collect()
        print(len(b.items))
        print(b.items[3])
        print(b.label)

    def touch_holder(h: Holder) -> None:
        gc.collect()
        h.nested.items.append(7)
        h.trailer.append(8)
        gc.collect()
        print(len(h.nested.items))
        print(h.nested.items[1])
        print(h.nested.label)
        print(len(h.trailer))
        print(h.trailer[1])
        print(h.title)

    def touch_keyword_argument() -> None:
        gc.collect()
        touch_holder(
            h=Holder(
                Nested([210], 'kw-arg-nested'),
                ['kw-arg-head'],
                'kw-arg-holder',
            )
        )
        selected = Holder(
            Nested([211], 'kw-local-nested'),
            ['kw-local-head'],
            'kw-local-holder',
        )
        gc.collect()
        touch_holder(h=selected)
        gc.collect()
        print(len(selected.nested.items))
        print(selected.nested.label)
        print(len(selected.trailer))
        print(selected.title)

    class Toucher:
        def touch_holder_arg(self, h: Holder) -> None:
            gc.collect()
            touch_holder(h)
            gc.collect()

    def touch_method_keyword_argument() -> None:
        toucher = Toucher()
        gc.collect()
        toucher.touch_holder_arg(
            h=Holder(
                Nested([220], 'method-kw-arg-nested'),
                ['method-kw-arg-head'],
                'method-kw-arg-holder',
            )
        )
        selected = Holder(
            Nested([221], 'method-kw-local-nested'),
            ['method-kw-local-head'],
            'method-kw-local-holder',
        )
        gc.collect()
        toucher.touch_holder_arg(h=selected)
        gc.collect()
        print(len(selected.nested.items))
        print(selected.nested.label)
        print(len(selected.trailer))
        print(selected.title)

    module_global_holder = Holder(
        Nested([200], 'global-nested'),
        ['global-head'],
        'global-holder',
    )

    @pcc.valueclass
    class FinalizerHolder:
        item: Track
        label: str

    module_global_finalizer_holder = FinalizerHolder(
        Track('old'),
        'old-holder',
    )
    module_global_shutdown_holder = FinalizerHolder(
        Track('shutdown'),
        'shutdown-holder',
    )

    def make_holder() -> Holder:
        gc.collect()
        return Holder(Nested([20], 'ret-nested'), ['ret-head'], 'ret-holder')

    def touch_conditional(flag: bool) -> None:
        selected = (
            Holder(Nested([70], 'cond-true-nested'), ['cond-true-head'], 'cond-true-holder')
            if flag
            else Holder(Nested([71], 'cond-false-nested'), ['cond-false-head'], 'cond-false-holder')
        )
        gc.collect()
        touch_holder(selected)
        gc.collect()
        print(len(selected.nested.items))
        print(selected.nested.label)
        print(len(selected.trailer))
        print(selected.title)

    def touch_loop_carried() -> None:
        current = Holder(Nested([80], 'loop-seed-nested'), ['loop-seed-head'], 'loop-seed-holder')
        i = 0
        while i < 2:
            gc.collect()
            if i == 0:
                current = Holder(Nested([81], 'loop-first-nested'), ['loop-first-head'], 'loop-first-holder')
            else:
                current = Holder(Nested([82], 'loop-second-nested'), ['loop-second-head'], 'loop-second-holder')
            gc.collect()
            i = i + 1
        touch_holder(current)
        gc.collect()
        print(len(current.nested.items))
        print(current.nested.label)
        print(len(current.trailer))
        print(current.title)

    def touch_try_flow() -> None:
        protected = Holder(Nested([90], 'try-seed-nested'), ['try-seed-head'], 'try-seed-holder')
        try:
            gc.collect()
            protected = Holder(Nested([91], 'try-live-nested'), ['try-live-head'], 'try-live-holder')
            gc.collect()
            raise ValueError('try-flow')
        except ValueError:
            gc.collect()
        finally:
            gc.collect()
        touch_holder(protected)
        gc.collect()
        print(len(protected.nested.items))
        print(protected.nested.label)
        print(len(protected.trailer))
        print(protected.title)

    def touch_closure_capture() -> None:
        captured = Holder(
            Nested([100], 'closure-nested'),
            ['closure-head'],
            'closure-holder',
        )

        def inner() -> None:
            gc.collect()
            touch_holder(captured)
            gc.collect()
            print(len(captured.nested.items))
            print(captured.nested.label)
            print(len(captured.trailer))
            print(captured.title)

        gc.collect()
        inner()

    def touch_tuple_unpack() -> None:
        left, right = (
            Holder(
                Nested([110], 'unpack-left-nested'),
                ['unpack-left-head'],
                'unpack-left-holder',
            ),
            Holder(
                Nested([111], 'unpack-right-nested'),
                ['unpack-right-head'],
                'unpack-right-holder',
            ),
        )
        gc.collect()
        touch_holder(left)
        touch_holder(right)
        gc.collect()
        print(len(left.nested.items))
        print(left.nested.label)
        print(len(left.trailer))
        print(left.title)
        print(len(right.nested.items))
        print(right.nested.label)
        print(len(right.trailer))
        print(right.title)

    def touch_for_loop_target() -> None:
        last = Holder(Nested([119], 'for-seed-nested'), ['for-seed-head'], 'for-seed-holder')
        for current in [
            Holder(
                Nested([120], 'for-first-nested'),
                ['for-first-head'],
                'for-first-holder',
            ),
            Holder(
                Nested([121], 'for-second-nested'),
                ['for-second-head'],
                'for-second-holder',
            ),
        ]:
            gc.collect()
            touch_holder(current)
            gc.collect()
            last = current
        gc.collect()
        print(len(last.nested.items))
        print(last.nested.label)
        print(len(last.trailer))
        print(last.title)

    def keep_comprehension_target() -> bool:
        gc.collect()
        return True

    def touch_comprehension_target() -> None:
        values = [
            current.nested.label
            for current in [
                Holder(
                    Nested([130], 'comp-first-nested'),
                    ['comp-first-head'],
                    'comp-first-holder',
                ),
                Holder(
                    Nested([131], 'comp-second-nested'),
                    ['comp-second-head'],
                    'comp-second-holder',
                ),
            ]
            if keep_comprehension_target()
        ]
        gc.collect()
        print(values[0])
        print(values[1])

    def touch_comprehension_target_collections() -> None:
        labels = {
            current.nested.label
            for current in [
                Holder(
                    Nested([140], 'set-first-nested'),
                    ['set-first-head'],
                    'set-first-holder',
                ),
                Holder(
                    Nested([141], 'set-second-nested'),
                    ['set-second-head'],
                    'set-second-holder',
                ),
            ]
            if keep_comprehension_target()
        }
        table = {
            current.title: current.nested.label
            for current in [
                Holder(
                    Nested([150], 'dict-first-nested'),
                    ['dict-first-head'],
                    'dict-first-holder',
                ),
                Holder(
                    Nested([151], 'dict-second-nested'),
                    ['dict-second-head'],
                    'dict-second-holder',
                ),
            ]
            if keep_comprehension_target()
        }
        gc.collect()
        print(len(labels))
        print('set-first-nested' in labels)
        print('set-second-nested' in labels)
        print(table['dict-first-holder'])
        print(table['dict-second-holder'])

    def touch_subscript_targets() -> None:
        list_values = [
            Holder(
                Nested([160], 'sub-first-nested'),
                ['sub-first-head'],
                'sub-first-holder',
            ),
            Holder(
                Nested([161], 'sub-second-nested'),
                ['sub-second-head'],
                'sub-second-holder',
            ),
        ]
        picked_list = list_values[1]
        gc.collect()
        touch_holder(picked_list)
        gc.collect()
        print(len(picked_list.nested.items))
        print(picked_list.nested.label)
        print(len(picked_list.trailer))
        print(picked_list.title)

        tuple_values = (
            Holder(
                Nested([170], 'tuple-sub-first-nested'),
                ['tuple-sub-first-head'],
                'tuple-sub-first-holder',
            ),
            Holder(
                Nested([171], 'tuple-sub-second-nested'),
                ['tuple-sub-second-head'],
                'tuple-sub-second-holder',
            ),
        )
        picked_tuple = tuple_values[1]
        gc.collect()
        touch_holder(picked_tuple)
        gc.collect()
        print(len(picked_tuple.nested.items))
        print(picked_tuple.nested.label)
        print(len(picked_tuple.trailer))
        print(picked_tuple.title)

    def touch_boolop_targets() -> None:
        left = Holder(
            Nested([180], 'bool-left-nested'),
            ['bool-left-head'],
            'bool-left-holder',
        )
        right = Holder(
            Nested([181], 'bool-right-nested'),
            ['bool-right-head'],
            'bool-right-holder',
        )
        selected_or = left or right
        gc.collect()
        touch_holder(selected_or)
        gc.collect()
        print(len(selected_or.nested.items))
        print(selected_or.nested.label)
        print(len(selected_or.trailer))
        print(selected_or.title)

        first = Holder(
            Nested([190], 'bool-first-nested'),
            ['bool-first-head'],
            'bool-first-holder',
        )
        second = Holder(
            Nested([191], 'bool-second-nested'),
            ['bool-second-head'],
            'bool-second-holder',
        )
        selected_and = first and second
        gc.collect()
        touch_holder(selected_and)
        gc.collect()
        print(len(selected_and.nested.items))
        print(selected_and.nested.label)
        print(len(selected_and.trailer))
        print(selected_and.title)

    def touch_module_global_target() -> None:
        gc.collect()
        touch_holder(module_global_holder)
        gc.collect()
        print(len(module_global_holder.nested.items))
        print(module_global_holder.nested.label)
        print(len(module_global_holder.trailer))
        print(module_global_holder.title)

    def touch_module_global_reassignment() -> None:
        global module_global_finalizer_holder
        gc.collect()
        module_global_finalizer_holder = FinalizerHolder(
            Track('new'),
            'new-holder',
        )
        gc.collect()
        print(module_global_finalizer_holder.label)
        print(module_global_finalizer_holder.item.name)
        print(len(finalizer_events))
        if len(finalizer_events) > 0:
            print(finalizer_events[0])

    def main() -> None:
        bag = Bag([1, 2, 3], 'bag')
        gc.collect()
        touch(bag)
        gc.collect()
        print(len(bag.items))
        print(bag.label)
        bag.touch_self()
        gc.collect()
        print(len(bag.items))
        print(bag.label)

        holder = Holder(Nested([10], 'nested'), ['head'], 'holder')
        gc.collect()
        touch_holder(holder)
        gc.collect()
        print(len(holder.nested.items))
        print(holder.nested.label)
        print(len(holder.trailer))
        print(holder.title)
        holder.touch_holder()
        gc.collect()
        print(len(holder.nested.items))
        print(len(holder.trailer))
        print(holder.title)

        returned = make_holder()
        gc.collect()
        touch_holder(returned)
        gc.collect()
        print(len(returned.nested.items))
        print(returned.nested.label)
        print(len(returned.trailer))
        print(returned.title)
        returned.touch_holder()
        gc.collect()
        print(len(returned.nested.items))
        print(len(returned.trailer))
        print(returned.title)

        touch_holder(Holder(Nested([30], 'arg-nested'), ['arg-head'], 'arg-holder'))
        touch_keyword_argument()
        touch_method_keyword_argument()
        Holder(
            Nested([40, 41], 'method-nested'),
            ['method-head', 'method-tail'],
            'method-holder',
        ).touch_holder()

        if (walrus := Holder(Nested([50], 'walrus-nested'), ['walrus-head'], 'walrus-holder')):
            gc.collect()
            touch_holder(walrus)
            gc.collect()
            print(len(walrus.nested.items))
            print(walrus.nested.label)
            print(len(walrus.trailer))
            print(walrus.title)

        reassigned = Holder(Nested([60], 'old-nested'), ['old-head'], 'old-holder')
        gc.collect()
        reassigned = Holder(
            Nested([61], 'reassigned-nested'),
            ['reassigned-head'],
            'reassigned-holder',
        )
        gc.collect()
        touch_holder(reassigned)
        gc.collect()
        print(len(reassigned.nested.items))
        print(reassigned.nested.label)
        print(len(reassigned.trailer))
        print(reassigned.title)

        touch_conditional(True)
        touch_conditional(False)
        touch_loop_carried()
        touch_try_flow()
        touch_closure_capture()
        touch_tuple_unpack()
        touch_for_loop_target()
        touch_comprehension_target()
        touch_comprehension_target_collections()
        touch_subscript_targets()
        touch_boolop_targets()
        touch_module_global_target()
        touch_module_global_reassignment()

    main()
    """
)


@pytest.fixture(scope="module")
def _valueclass_direct_payload_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_valueclass_direct_payload")
    src = tmp / "valueclass_direct_payload.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "valueclass_direct_payload_bin"
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
def test_direct_valueclass_pointer_payload_survives_gc(
    _valueclass_direct_payload_exe,
    backend,
):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    run = subprocess.run(
        [_valueclass_direct_payload_exe],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert run.returncode == 0, (
        f"backend #{backend} rc={run.returncode}: {run.stderr.strip()[:400]}"
    )
    assert run.stdout.splitlines() == [
        "4",
        "4",
        "bag",
        "4",
        "bag",
        "5",
        "5",
        "bag",
        "5",
        "bag",
        "2",
        "7",
        "nested",
        "2",
        "8",
        "holder",
        "2",
        "nested",
        "2",
        "holder",
        "3",
        "8",
        "nested",
        "3",
        "9",
        "holder",
        "3",
        "3",
        "holder",
        "2",
        "7",
        "ret-nested",
        "2",
        "8",
        "ret-holder",
        "2",
        "ret-nested",
        "2",
        "ret-holder",
        "3",
        "8",
        "ret-nested",
        "3",
        "9",
        "ret-holder",
        "3",
        "3",
        "ret-holder",
        "2",
        "7",
        "arg-nested",
        "2",
        "8",
        "arg-holder",
        "2",
        "7",
        "kw-arg-nested",
        "2",
        "8",
        "kw-arg-holder",
        "2",
        "7",
        "kw-local-nested",
        "2",
        "8",
        "kw-local-holder",
        "2",
        "kw-local-nested",
        "2",
        "kw-local-holder",
        "2",
        "7",
        "method-kw-arg-nested",
        "2",
        "8",
        "method-kw-arg-holder",
        "2",
        "7",
        "method-kw-local-nested",
        "2",
        "8",
        "method-kw-local-holder",
        "2",
        "method-kw-local-nested",
        "2",
        "method-kw-local-holder",
        "3",
        "8",
        "method-nested",
        "3",
        "9",
        "method-holder",
        "2",
        "7",
        "walrus-nested",
        "2",
        "8",
        "walrus-holder",
        "2",
        "walrus-nested",
        "2",
        "walrus-holder",
        "2",
        "7",
        "reassigned-nested",
        "2",
        "8",
        "reassigned-holder",
        "2",
        "reassigned-nested",
        "2",
        "reassigned-holder",
        "2",
        "7",
        "cond-true-nested",
        "2",
        "8",
        "cond-true-holder",
        "2",
        "cond-true-nested",
        "2",
        "cond-true-holder",
        "2",
        "7",
        "cond-false-nested",
        "2",
        "8",
        "cond-false-holder",
        "2",
        "cond-false-nested",
        "2",
        "cond-false-holder",
        "2",
        "7",
        "loop-second-nested",
        "2",
        "8",
        "loop-second-holder",
        "2",
        "loop-second-nested",
        "2",
        "loop-second-holder",
        "2",
        "7",
        "try-live-nested",
        "2",
        "8",
        "try-live-holder",
        "2",
        "try-live-nested",
        "2",
        "try-live-holder",
        "2",
        "7",
        "closure-nested",
        "2",
        "8",
        "closure-holder",
        "2",
        "closure-nested",
        "2",
        "closure-holder",
        "2",
        "7",
        "unpack-left-nested",
        "2",
        "8",
        "unpack-left-holder",
        "2",
        "7",
        "unpack-right-nested",
        "2",
        "8",
        "unpack-right-holder",
        "2",
        "unpack-left-nested",
        "2",
        "unpack-left-holder",
        "2",
        "unpack-right-nested",
        "2",
        "unpack-right-holder",
        "2",
        "7",
        "for-first-nested",
        "2",
        "8",
        "for-first-holder",
        "2",
        "7",
        "for-second-nested",
        "2",
        "8",
        "for-second-holder",
        "2",
        "for-second-nested",
        "2",
        "for-second-holder",
        "comp-first-nested",
        "comp-second-nested",
        "2",
        "True",
        "True",
        "dict-first-nested",
        "dict-second-nested",
        "2",
        "7",
        "sub-second-nested",
        "2",
        "8",
        "sub-second-holder",
        "2",
        "sub-second-nested",
        "2",
        "sub-second-holder",
        "2",
        "7",
        "tuple-sub-second-nested",
        "2",
        "8",
        "tuple-sub-second-holder",
        "2",
        "tuple-sub-second-nested",
        "2",
        "tuple-sub-second-holder",
        "2",
        "7",
        "bool-left-nested",
        "2",
        "8",
        "bool-left-holder",
        "2",
        "bool-left-nested",
        "2",
        "bool-left-holder",
        "2",
        "7",
        "bool-second-nested",
        "2",
        "8",
        "bool-second-holder",
        "2",
        "bool-second-nested",
        "2",
        "bool-second-holder",
        "2",
        "7",
        "global-nested",
        "2",
        "8",
        "global-holder",
        "2",
        "global-nested",
        "2",
        "global-holder",
        "new-holder",
        "new",
        "1",
        "del:old",
    ], run.stdout
    assert "del:shutdown" in run.stderr
