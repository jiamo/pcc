from __future__ import annotations

import subprocess
from pathlib import Path


def test_exception_accessor_symbols_are_wired_in_c_py_and_abi():
    c_src = Path("pcc/py_runtime/src/py_exc_objects.c").read_text(encoding="utf-8")
    py_src = Path("pcc/py_runtime/py/py_exc_objects.py").read_text(encoding="utf-8")
    header = Path("pcc/py_runtime/include/py_runtime.h").read_text(encoding="utf-8")
    abi = Path("pcc/py_frontend/codegen/runtime_abi.py").read_text(encoding="utf-8")

    assert "PyObject *py_exc_get_cause(PyObject *exc)" in c_src
    assert "PyObject *py_exc_get_context(PyObject *exc)" in c_src
    assert "int64_t py_exc_traceback_len(PyObject *exc)" in c_src

    assert '@c_abi_export("py_exc_get_cause")' in py_src
    assert '@c_abi_export("py_exc_get_context")' in py_src
    assert '@c_abi_export("py_exc_traceback_len")' in py_src

    assert "PyObject *py_exc_get_cause(PyObject *exc);" in header
    assert '"py_exc_get_cause": (_PYOBJ, [_PYOBJ], False)' in abi
    assert '"py_exc_traceback_len": (_I64, [_PYOBJ], False)' in abi


def test_source_aware_traceback_contract_is_mirrored_and_outermost_first():
    c_src = Path("pcc/py_runtime/src/py_exc_traceback.c").read_text(
        encoding="utf-8"
    )
    py_src = Path("pcc/py_runtime/py/py_exc_traceback.py").read_text(
        encoding="utf-8"
    )
    header = Path("pcc/py_runtime/include/py_runtime.h").read_text(
        encoding="utf-8"
    )
    abi = Path("pcc/py_frontend/codegen/runtime_abi.py").read_text(
        encoding="utf-8"
    )
    lowering = Path("pcc/py_frontend/codegen/exception_lowering.py").read_text(
        encoding="utf-8"
    )

    assert "const char *source_line;" in Path(
        "pcc/py_runtime/src/py_internal.h"
    ).read_text(encoding="utf-8")
    assert "void py_exc_append_frame_source(PyObject *exc," in header
    assert '"py_exc_append_frame_source": (' in abi
    assert "self.runtime[\"py_exc_append_frame_source\"]" in lowering
    assert "source_stream.read().splitlines()" in lowering

    assert "for (int32_t i = e->n_frames - 1; i >= 0; i--)" in c_src
    assert "fr->source_line" in c_src
    assert "i: int = n_frames - 1" in py_src
    assert "while i >= 0:" in py_src
    assert "source_line = load_ptr(fr, 16)" in py_src


def test_nested_unhandled_traceback_is_outermost_first_with_source(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    source_path = tmp_path / "nested_traceback_order.py"
    source_path.write_text(
        "def leaf() -> None:\n"
        "    raise RuntimeError(\"boom\")\n"
        "\n"
        "def middle() -> None:\n"
        "    leaf()\n"
        "\n"
        "def outer() -> None:\n"
        "    middle()\n"
        "\n"
        "outer()\n",
        encoding="utf-8",
    )
    executable = tmp_path / "nested_traceback_order.out"
    compile_python(
        str(source_path),
        str(executable),
        libpython_mode="off",
        ir_scaffold_mode="on",
        backend="self",
    )
    run = subprocess.run(
        [str(executable)],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert run.returncode == 1
    expected_frames = (
        f'File "{source_path}", line 10, in <module>',
        f'File "{source_path}", line 8, in outer',
        f'File "{source_path}", line 5, in middle',
        f'File "{source_path}", line 2, in leaf',
    )
    offsets = []
    for frame in expected_frames:
        assert frame in run.stderr
        offsets.append(run.stderr.index(frame))
    assert offsets == sorted(offsets)
    for source_line in (
        "    outer()\n",
        "    middle()\n",
        "    leaf()\n",
        '    raise RuntimeError("boom")\n',
    ):
        assert source_line in run.stderr


def test_unhandled_implicit_chain_keeps_the_original_failure(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    source_path = tmp_path / "implicit_exception_chain.py"
    source_path.write_text(
        "def wrap() -> None:\n"
        "    try:\n"
        "        raise ValueError(\"root failure\")\n"
        "    except ValueError:\n"
        "        raise RuntimeError(\"reported failure\")\n"
        "\n"
        "wrap()\n",
        encoding="utf-8",
    )
    executable = tmp_path / "implicit_exception_chain.out"
    compile_python(
        str(source_path),
        str(executable),
        libpython_mode="off",
        ir_scaffold_mode="on",
        backend="self",
    )
    run = subprocess.run(
        [str(executable)],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert run.returncode == 1
    root = "ValueError: root failure"
    separator = (
        "During handling of the above exception, another exception occurred:"
    )
    reported = "RuntimeError: reported failure"
    for fragment in (root, separator, reported):
        assert fragment in run.stderr
    assert run.stderr.index(root) < run.stderr.index(separator)
    assert run.stderr.index(separator) < run.stderr.index(reported)


def test_runtime_contract_error_names_its_helper_in_an_innermost_frame():
    c_traceback = Path("pcc/py_runtime/src/py_exc_traceback.c").read_text(
        encoding="utf-8"
    )
    py_traceback = Path("pcc/py_runtime/py/py_exc_traceback.py").read_text(
        encoding="utf-8"
    )
    header = Path("pcc/py_runtime/include/py_runtime.h").read_text(
        encoding="utf-8"
    )
    abi = Path("pcc/py_frontend/codegen/runtime_abi.py").read_text(
        encoding="utf-8"
    )
    dispatch_c = Path("pcc/py_runtime/src/py_obj_ops_dispatch.c").read_text(
        encoding="utf-8"
    )
    dispatch_py = Path("pcc/py_runtime/py/py_obj_ops_dispatch.py").read_text(
        encoding="utf-8"
    )
    func_c = Path("pcc/py_runtime/src/py_func.c").read_text(encoding="utf-8")
    func_py = Path("pcc/py_runtime/py/py_func.py").read_text(encoding="utf-8")

    assert "PyObject *py_runtime_error_if_unset(" in header
    assert '"py_runtime_error_if_unset": (_PYOBJ, [_CSTR, _CSTR], False)' in abi
    for source in (c_traceback, py_traceback):
        assert "py_runtime_error_if_unset" in source
        assert '"<pcc runtime>"' in source
        assert "runtime contract: NULL result without an exception" in source
        assert "py_exc_append_frame_source" in source

    for source in (dispatch_c, dispatch_py, func_c, func_py):
        assert "py_runtime_error_if_unset" in source
    for source in (dispatch_c, dispatch_py):
        assert '"py_tuple_new"' in source
        assert "bound method call could not allocate its argument tuple" in source
    assert '"py_func_bind_signature"' in func_c
    assert 'cstr("py_func_bind_signature")' in func_py
    assert 'f->name != NULL ? f->name : "<compiled native function>"' in func_c
    assert "entry_name = load_ptr(fn, 72)" in func_py

    capi_sources = (
        Path("pcc/py_runtime/src/py_capi_shim.c").read_text(encoding="utf-8"),
        Path("pcc/py_runtime/src/py_capi_shim_oracle.c").read_text(
            encoding="utf-8"
        ),
        Path("pcc/py_runtime/py/py_capi_object_call_runtime.py").read_text(
            encoding="utf-8"
        ),
        Path("pcc/py_runtime/py/py_capi_cext_runtime.py").read_text(
            encoding="utf-8"
        ),
    )
    for source in capi_sources:
        assert "py_runtime_error_if_unset" in source
    for source in (capi_sources[0], capi_sources[1], capi_sources[3]):
        assert '"C extension tp_call"' in source


def test_call_boundaries_set_or_preserve_the_callee_owned_exception():
    dispatch_c = Path("pcc/py_runtime/src/py_obj_ops_dispatch.c").read_text(
        encoding="utf-8"
    )
    dispatch_py = Path("pcc/py_runtime/py/py_obj_ops_dispatch.py").read_text(
        encoding="utf-8"
    )
    func_c = Path("pcc/py_runtime/src/py_func.c").read_text(encoding="utf-8")
    func_py = Path("pcc/py_runtime/py/py_func.py").read_text(encoding="utf-8")
    capi_py = Path(
        "pcc/py_runtime/py/py_capi_object_call_runtime.py"
    ).read_text(encoding="utf-8")
    capi_c = Path("pcc/py_runtime/src/py_capi_shim.c").read_text(
        encoding="utf-8"
    )
    capi_oracle = Path("pcc/py_runtime/src/py_capi_shim_oracle.c").read_text(
        encoding="utf-8"
    )

    for source in (dispatch_c, dispatch_py):
        assert "py_obj_call received NULL callable" in source
        assert "returned NULL without setting an exception" in source
        assert "instance has no __call__ method" in source
        assert "py_obj_call_method1 received NULL object" in source
        assert "py_obj_call_method1 received NULL method name" in source
        assert "py_obj_call_method1 received NULL argument" in source
        assert "py_obj_call_method1 callee returned NULL" in source

    for source in (func_c, func_py):
        assert "native function call received NULL callable" in source
        assert "native function object has no entry point" in source
        assert "compiled native function returned NULL without exception" in source

    contract_message = "py_obj_call returned NULL without setting an exception"
    for source in (capi_py, capi_c, capi_oracle):
        assert contract_message in source
        call_pos = source.index("result = py_obj_call(callable, call_args, kwargs)")
        guard_pos = source.index("py_runtime_error_if_unset(", call_pos)
        cleanup_pos = source.index("py_decref(call_args)", call_pos)
        assert call_pos < guard_pos < cleanup_pos

    c_method_call = dispatch_c.index(
        "PyObject *out = ((M1)(uintptr_t)method)(self, a0);"
    )
    c_method_guard = dispatch_c.index(
        "bound native method returned NULL without setting an exception",
        c_method_call,
    )
    c_method_cleanup = dispatch_c.index("py_decref(a0);", c_method_call)
    assert c_method_call < c_method_guard < c_method_cleanup

    py_method_call = dispatch_py.index("out = call_ptr2(method, self_obj, a0)")
    py_method_guard = dispatch_py.index(
        "bound native method returned NULL without setting an exception",
        py_method_call,
    )
    py_method_cleanup = dispatch_py.index("py_decref(a0)", py_method_call)
    assert py_method_call < py_method_guard < py_method_cleanup

    for source, cleanup in (
        (dispatch_c, "if (arg != NULL) py_decref(arg);"),
        (dispatch_py, "if ptr_is_null(arg) == 0:"),
    ):
        guard_pos = source.index(
            "native builtin constructor returned NULL without setting an exception"
        )
        cleanup_pos = source.index(cleanup, guard_pos)
        assert guard_pos < cleanup_pos

    for source, call, cleanup in (
        (
            dispatch_c,
            "PyObject *out = py_obj_call(method, args, py_None);",
            "py_decref(method);",
        ),
        (
            dispatch_py,
            'out = py_obj_call(method, args, global_load_ptr("py_None"))',
            "py_decref(method)",
        ),
    ):
        call_pos = source.index(call)
        guard_pos = source.index(
            "py_obj_call_method1 callee returned NULL without setting an exception",
            call_pos,
        )
        cleanup_pos = source.index(cleanup, call_pos)
        assert call_pos < guard_pos < cleanup_pos

    assert "pcc-Python bound native method supports at most one argument" in dispatch_py
    assert '_type_error(cstr("object is not callable"))' not in capi_py
    assert 'PyErr_SetString(PyExc_TypeError, "object is not callable")' not in capi_c
    assert (
        'PyErr_SetString(PyExc_TypeError, "object is not callable")'
        not in capi_oracle
    )


def test_c_extension_pointer_slots_guard_silent_null_at_the_callback_boundary():
    c_sources = (
        Path("pcc/py_runtime/src/py_capi_shim.c").read_text(encoding="utf-8"),
        Path("pcc/py_runtime/src/py_capi_shim_oracle.c").read_text(
            encoding="utf-8"
        ),
    )
    port = Path("pcc/py_runtime/py/py_capi_cext_runtime.py").read_text(
        encoding="utf-8"
    )
    number_port = Path(
        "pcc/py_runtime/py/py_capi_number_runtime.py"
    ).read_text(encoding="utf-8")

    callback_contracts = (
        "tp_iter returned NULL without setting an exception",
        "tp_repr returned NULL without setting an exception",
        "mp_subscript returned NULL without setting an exception",
        "sq_item returned NULL without setting an exception",
        "tp_getattro returned NULL without setting an exception",
        "nb_absolute returned NULL without setting an exception",
        "tp_new returned NULL without setting an exception",
        "getset getter returned NULL without setting an exception",
    )
    for source in (*c_sources, port):
        assert "py_runtime_error_if_unset" in source
        for message in callback_contracts:
            assert message in source

    # getattr owns the temporary name object.  Attribute callbacks must be
    # checked before that cleanup can run arbitrary deallocators and obscure
    # the original silent-NULL contract violation.
    for source, call, cleanup in (
        (c_sources[0], "result = getattro(o, name_obj);", "py_decref(name_obj);"),
        (c_sources[1], "result = getattro(o, name_obj);", "py_decref(name_obj);"),
        (port, "result = call_ptr2(getattro, o, name_obj)", "py_decref(name_obj)"),
    ):
        call_pos = source.index(call)
        guard_pos = source.index(
            "tp_getattro returned NULL without setting an exception", call_pos
        )
        cleanup_pos = source.index(cleanup, call_pos)
        assert call_pos < guard_pos < cleanup_pos

    # tp_iternext is the one intentional exception: NULL without an error is
    # normal exhaustion and must become StopIteration, not RuntimeError.
    for source, start, end in (
        (
            c_sources[0],
            "PyObject *pcc_capi_cext_object_next(",
            "int64_t pcc_capi_cext_object_is_iterator(",
        ),
        (
            c_sources[1],
            "PyObject *pcc_capi_cext_object_next(",
            "int64_t pcc_capi_cext_object_is_iterator(",
        ),
        (
            port,
            "def pcc_capi_cext_object_next(",
            "def pcc_capi_cext_object_is_iterator(",
        ),
    ):
        body = source[source.index(start) : source.index(end, source.index(start))]
        assert "StopIteration" in body
        assert "py_runtime_error_if_unset" not in body

    for source in (*c_sources, number_port):
        call_pos = source.index("result = ", source.index("call_int_conversion_slot"))
        guard_pos = source.index(
            "integer conversion slot returned NULL without setting an exception",
            call_pos,
        )
        type_check_pos = source.index("is_intlike", call_pos)
        assert call_pos < guard_pos < type_check_pos


def test_c_extension_status_slots_guard_failure_before_owned_cleanup():
    c_sources = (
        Path("pcc/py_runtime/src/py_capi_shim.c").read_text(encoding="utf-8"),
        Path("pcc/py_runtime/src/py_capi_shim_oracle.c").read_text(
            encoding="utf-8"
        ),
    )
    port = Path("pcc/py_runtime/py/py_capi_cext_runtime.py").read_text(
        encoding="utf-8"
    )
    messages = (
        "mp_length returned a negative result without setting an exception",
        "sq_length returned a negative result without setting an exception",
        "tp_setattro returned failure without setting an exception",
        "getset setter returned failure without setting an exception",
        "tp_init returned failure without setting an exception",
    )
    for source in (*c_sources, port):
        for message in messages:
            assert message in source

    for source, call, guard, cleanup in (
        (
            c_sources[0],
            "result = setattro(o, name_obj, value);",
            "tp_setattro returned failure without setting an exception",
            "py_decref(name_obj);",
        ),
        (
            c_sources[1],
            "result = setattro(o, name_obj, value);",
            "tp_setattro returned failure without setting an exception",
            "py_decref(name_obj);",
        ),
        (
            port,
            "call_ptr3(setattro, o, name_obj, value)",
            "tp_setattro returned failure without setting an exception",
            "py_decref(name_obj)",
        ),
        (
            c_sources[0],
            "if (tp_init(result, args, call_kwargs) != 0)",
            "tp_init returned failure without setting an exception",
            "py_decref(result);",
        ),
        (
            c_sources[1],
            "if (tp_init(result, args, call_kwargs) != 0)",
            "tp_init returned failure without setting an exception",
            "py_decref(result);",
        ),
        (
            port,
            "call_i64_ptr3(tp_init, result, args, call_kwargs)",
            "tp_init returned failure without setting an exception",
            "py_decref(result)",
        ),
    ):
        call_pos = source.index(call)
        guard_pos = source.index(guard, call_pos)
        cleanup_pos = source.index(cleanup, call_pos)
        assert call_pos < guard_pos < cleanup_pos

def test_user_protocol_dunder_calls_guard_silent_null_before_cleanup():
    protocol_c = Path("pcc/py_runtime/src/py_protocol.c").read_text(
        encoding="utf-8"
    )
    protocol_py = Path(
        "pcc/py_runtime/py/py_protocol_runtime.py"
    ).read_text(encoding="utf-8")
    dunder_c = Path("pcc/py_runtime/src/py_dunder.c").read_text(
        encoding="utf-8"
    )
    dunder_py = Path("pcc/py_runtime/py/py_dunder.py").read_text(
        encoding="utf-8"
    )

    # A missing method is a deliberate lookup sentinel.  Once a method was
    # found, however, both raw and PyFunc call paths must preserve its pending
    # exception or synthesize an attributed runtime-contract error.
    for source in (protocol_c, protocol_py):
        assert "protocol_require_result" in source
        assert "user protocol argument tuple allocation failed" in source
        assert "user protocol callback returned NULL without an exception" in source

    for source, call, guard, cleanup in (
        (
            protocol_c,
            "PyObject *out = py_func_call(method, args);",
            "protocol_require_result(",
            "py_decref(args);",
        ),
        (
            protocol_py,
            "result = py_func_call(method, args)",
            "_protocol_require_result(",
            "py_decref(args)",
        ),
    ):
        call_pos = source.index(call)
        guard_pos = source.index(guard, call_pos)
        cleanup_pos = source.index(cleanup, call_pos)
        assert call_pos < guard_pos < cleanup_pos

    for source in (dunder_c, dunder_py):
        assert "dunder_require_result" in source
        assert "user dunder argument tuple allocation failed" in source
        assert "user dunder callback returned NULL without an exception" in source

    for source, call, guard, cleanup in (
        (
            dunder_c,
            "PyObject *out = py_func_call(func, args);",
            "dunder_require_result(",
            "py_decref(args);",
        ),
        (
            dunder_py,
            "out = py_func_call(func, args)",
            "_dunder_require_result(",
            "py_decref(args)",
        ),
    ):
        call_pos = source.index(call)
        guard_pos = source.index(guard, call_pos)
        cleanup_pos = source.index(cleanup, call_pos)
        assert call_pos < guard_pos < cleanup_pos


def test_class_descriptor_callbacks_guard_silent_null_before_cleanup():
    class_c = Path("pcc/py_runtime/src/py_class.c").read_text(encoding="utf-8")
    attrs_c = Path("pcc/py_runtime/src/py_class_attrs.c").read_text(
        encoding="utf-8"
    )
    class_py = Path("pcc/py_runtime/py/py_class.py").read_text(encoding="utf-8")
    for source in (class_c, attrs_c, class_py):
        assert "require_result" in source
        assert "class callback argument tuple allocation failed" in source
        assert "class callback returned NULL without setting an exception" in source

    for source, call, guard, cleanup in (
        (
            class_c,
            "PyObject *out = py_func_call(func, args);",
            "class_require_result(",
            "py_decref(args);",
        ),
        (
            attrs_c,
            "PyObject *out = py_func_call(func, args);",
            "class_attrs_require_result(",
            "py_decref(args);",
        ),
        (
            class_py,
            "out = py_obj_call(fget, args, global_load_ptr(\"py_None\"))",
            "_class_require_result(",
            "py_decref(args)",
        ),
    ):
        call_pos = source.index(call)
        guard_pos = source.index(guard, call_pos)
        cleanup_pos = source.index(cleanup, call_pos)
        assert call_pos < guard_pos < cleanup_pos


def test_format_and_copy_protocols_guard_silent_null_before_cleanup():
    format_c = Path("pcc/py_runtime/src/py_format.c").read_text(encoding="utf-8")
    format_py = Path("pcc/py_runtime/py/py_format_runtime.py").read_text(
        encoding="utf-8"
    )
    copy_c = Path("pcc/py_runtime/src/py_pickle_copy.c").read_text(
        encoding="utf-8"
    )
    copy_py = Path(
        "pcc/py_runtime/py/py_pickle_copy_runtime.py"
    ).read_text(encoding="utf-8")

    for source in (format_c, format_py):
        assert "format_require_result" in source
        assert "format callback argument tuple allocation failed" in source
        assert "format callback returned NULL without setting an exception" in source
    for source in (copy_c, copy_py):
        assert "copy_require_result" in source
        assert "copy callback argument tuple allocation failed" in source
        assert "copy callback returned NULL without setting an exception" in source

    for source, call, guard, cleanup in (
        (
            format_c,
            "PyObject *out = py_func_call(method, args);",
            "format_require_result(",
            "py_decref(args);",
        ),
        (
            format_py,
            "result = py_obj_call(method, args, global_load_ptr(\"py_None\"))",
            "_format_require_result(",
            "py_decref(args)",
        ),
        (
            copy_c,
            "PyObject *out = py_func_call(method, full_args);",
            "copy_require_result(",
            "py_decref(full_args);",
        ),
        (
            copy_py,
            "result = py_func_call(method, full_args)",
            "_copy_require_result(",
            "py_decref(full_args)",
        ),
    ):
        call_pos = source.index(call)
        guard_pos = source.index(guard, call_pos)
        cleanup_pos = source.index(cleanup, call_pos)
        assert call_pos < guard_pos < cleanup_pos


def test_weakref_callback_is_explicit_unraisable_owned_result_boundary():
    c_source = Path("pcc/py_runtime/src/py_weakref.c").read_text(
        encoding="utf-8"
    )
    py_source = Path("pcc/py_runtime/py/py_weakref.py").read_text(
        encoding="utf-8"
    )

    for source, call, result_cleanup, args_cleanup, clear in (
        (
            c_source,
            "PyObject *result = py_obj_call(callback, args, py_None);",
            "if (result != NULL) py_decref(result);",
            "py_decref(args);",
            "py_clear_exception();",
        ),
        (
            py_source,
            "result = py_obj_call(callback, args, _py_none())",
            "py_decref(result)",
            "py_decref(args)",
            "py_clear_exception()",
        ),
    ):
        call_pos = source.index(call)
        result_cleanup_pos = source.index(result_cleanup, call_pos)
        args_cleanup_pos = source.index(args_cleanup, result_cleanup_pos)
        clear_pos = source.index(clear, args_cleanup_pos)
        assert call_pos < result_cleanup_pos < args_cleanup_pos < clear_pos
        assert "unraisable boundary" in source[call_pos - 500 : clear_pos]


def test_c_extension_method_callbacks_guard_silent_null_before_cleanup():
    sources = (
        Path("pcc/py_runtime/src/py_capi_shim.c").read_text(encoding="utf-8"),
        Path("pcc/py_runtime/src/py_capi_shim_oracle.c").read_text(
            encoding="utf-8"
        ),
        Path("pcc/py_runtime/py/py_capi_method_bridge_runtime.py").read_text(
            encoding="utf-8"
        ),
        Path("pcc/py_runtime/py/py_capi_type_descriptor_runtime.py").read_text(
            encoding="utf-8"
        ),
    )
    for source in sources:
        assert "C extension method returned NULL without setting an exception" in source
        assert "require_result" in source

    for source, call, guard, cleanup in (
        (
            sources[0],
            "method->ml_meth(self, arg)",
            "pcc_capi_method_require_result(",
            "py_decref(arg);",
        ),
        (
            sources[1],
            "method->ml_meth(self, arg)",
            "pcc_capi_method_require_result(",
            "py_decref(arg);",
        ),
        (
            sources[2],
            "call_ptr2(load_ptr(method, 8), self_obj, arg)",
            "_method_require_result(",
            "py_decref(arg)",
        ),
        (
            sources[3],
            "call_ptr2(load_ptr(method, 8), self, arg)",
            "_descriptor_require_result(",
            "py_decref(arg)",
        ),
    ):
        call_pos = source.index(call)
        guard_pos = source.rfind(guard, 0, call_pos)
        cleanup_pos = source.index(cleanup, call_pos)
        assert guard_pos != -1
        assert guard_pos < call_pos < cleanup_pos

def test_splat_call_boundaries_attribute_silent_null_before_cleanup():
    c_source = Path("pcc/py_runtime/src/py_call_splat.c").read_text(
        encoding="utf-8"
    )
    py_source = Path("pcc/py_runtime/py/py_call_splat_runtime.py").read_text(
        encoding="utf-8"
    )

    messages = (
        "call splat could not allocate the base argument tuple",
        "call splat could not allocate the merged argument tuple",
        "call splat could not read a base positional argument",
        "call splat could not read a starred positional argument",
        "zip splat could not read an input row",
        "zip splat row length failed without setting an exception",
        "zip splat element lookup failed without setting an exception",
        "call splat could not allocate the merged keyword dictionary",
        "call splat could not merge positional arguments",
        "call splat could not merge keyword arguments",
        "call splat callee returned NULL without setting an exception",
    )
    for source in (c_source, py_source):
        assert "py_runtime_error_if_unset" in source
        for message in messages:
            assert message in source

    for source, call, guard, cleanup in (
        (
            c_source,
            "PyObject *out = py_obj_call(callable, args, kwargs);",
            "call splat callee returned NULL without setting an exception",
            "py_decref(args);",
        ),
        (
            py_source,
            "out = py_obj_call(callable_obj, args, kwargs)",
            "call splat callee returned NULL without setting an exception",
            "py_decref(args)",
        ),
        (
            c_source,
            "PyObject *out = py_tuple_new(base_len + star_len);",
            "call splat could not allocate the merged argument tuple",
            "py_decref(base_tuple);",
        ),
        (
            py_source,
            "out = py_tuple_new(base_len + star_len)",
            "call splat could not allocate the merged argument tuple",
            "py_decref(base_tuple)",
        ),
    ):
        call_pos = source.index(call)
        guard_pos = source.index(guard, call_pos)
        cleanup_pos = source.index(cleanup, call_pos)
        assert call_pos < guard_pos < cleanup_pos
