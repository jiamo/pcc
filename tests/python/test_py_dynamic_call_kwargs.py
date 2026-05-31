import textwrap

from pcc.py_frontend.pipeline import compile_python


def test_dynamic_call_merges_explicit_kwargs_and_starstar_for_codegen(tmp_path):
    src = tmp_path / "dynamic_call_mixed_kwargs.py"
    src.write_text(
        textwrap.dedent(
            """
            def target(**kwargs):
                return kwargs

            fn = target
            extra = {"b": 2}
            result = fn(a=1, **extra)
            print(result)
            """
        )
    )
    exe = tmp_path / "dynamic_call_mixed_kwargs.out"
    compile_python(str(src), str(exe), libpython_mode="off", ir_scaffold_mode="on")
