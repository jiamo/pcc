from __future__ import annotations

import re
import subprocess
import textwrap


def _generate_ir(source: str) -> str:
    from pcc.parse.py_lift import parse_and_lift
    from pcc.py_frontend import type_infer
    from pcc.py_frontend.codegen import layer1

    ast_mod = parse_and_lift(source, "<valueclass-unboxed>", "value_mod")
    typed = type_infer.infer_module(ast_mod)
    cg = layer1.L1CodeGen(typed, ir_scaffold_mode="on")
    return str(cg.generate(typed))


def test_valueclass_local_scalar_payload_avoids_instance_allocation():
    ir_text = _generate_ir(
        textwrap.dedent(
            """
            import pcc

            @pcc.valueclass
            class Point:
                x: int
                y: int

            p = Point(1, 2)
            z = p.x + p.y
            print(z)
            """
        )
    )

    assert not re.search(r"\bcall\b[^\n]*@py_instance_new\b", ir_text)
    assert "extractvalue" in ir_text


def test_valueclass_direct_function_arg_uses_payload_abi():
    ir_text = _generate_ir(
        textwrap.dedent(
            """
            import pcc

            @pcc.valueclass
            class Point:
                x: int
                y: int

            def norm2(p: Point) -> int:
                return p.x * p.x + p.y * p.y

            p = Point(3, 4)
            print(norm2(p))
            """
        )
    )

    assert re.search(
        r"define external ptr @user_value_mod_norm2\(\{ i64, i64 \} %p\)",
        ir_text,
    )
    assert not re.search(r"\bcall\b[^\n]*@py_instance_new\b", ir_text)
    assert "extractvalue" in ir_text


def test_valueclass_return_constructor_uses_payload_abi():
    ir_text = _generate_ir(
        textwrap.dedent(
            """
            import pcc

            @pcc.valueclass
            class Point:
                x: int
                y: int

            def make_point(x: int, y: int) -> Point:
                return Point(x, y)

            p = make_point(3, 4)
            print(p.x + p.y)
            """
        )
    )

    assert re.search(
        r"define external \{ i64, i64 \} @user_value_mod_make_point",
        ir_text,
    )
    assert re.search(r"\bret \{ i64, i64 \}", ir_text)
    assert not re.search(r"\bcall\b[^\n]*@py_instance_new\b", ir_text)


def test_valueclass_return_payload_compiles_with_default_ir_passes(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "valueclass_return.py"
    exe = tmp_path / "valueclass_return"
    src.write_text(
        textwrap.dedent(
            """
            import pcc

            @pcc.valueclass
            class Point:
                x: int
                y: int

            def make_point(x: int, y: int) -> Point:
                return Point(x, y)

            p = make_point(3, 4)
            print(p.x + p.y)
            """
        )
    )

    compile_python(str(src), str(exe), libpython_mode="off", ir_scaffold_mode="on")
    proc = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert proc.stdout.strip() == "7"


def test_valueclass_direct_method_receiver_uses_payload_abi():
    ir_text = _generate_ir(
        textwrap.dedent(
            """
            import pcc

            @pcc.valueclass
            class Point:
                x: int
                y: int

                def norm2(self) -> int:
                    return self.x * self.x + self.y * self.y

            p = Point(3, 4)
            print(p.norm2())
            """
        )
    )

    assert re.search(
        r"define external ptr @user_value_mod_Point_norm2"
        r"\(\{ i64, i64 \} %self\)",
        ir_text,
    )
    assert re.search(
        r"\bcall ptr \(\{ i64, i64 \}\) @user_value_mod_Point_norm2",
        ir_text,
    )
    assert not re.search(r"\bextractvalue ptr\b", ir_text)
    assert not re.search(r"\bcall\b[^\n]*@py_instance_new\b", ir_text)


def test_valueclass_payload_equality_uses_fieldwise_compare():
    ir_text = _generate_ir(
        textwrap.dedent(
            """
            import pcc

            @pcc.valueclass
            class Point:
                x: int
                y: int

            p = Point(1, 2)
            q = Point(1, 2)
            r = Point(1, 3)
            if p == q:
                print(1)
            if p != r:
                print(2)
            """
        )
    )

    main_ir = ir_text[ir_text.index("define i32 @main") :]
    assert "value.eq.icmp" in ir_text
    assert "value.eq.and" in ir_text
    assert not re.search(r"\bcall\b[^\n]*@user_value_mod_Point___eq__\b", main_ir)
    assert not re.search(r"\bcall\b[^\n]*@py_obj_eq\b", main_ir)
    assert not re.search(r"\bcall\b[^\n]*@py_instance_new\b", main_ir)


def test_valueclass_payload_equality_compiles_with_default_ir_passes(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "valueclass_eq.py"
    exe = tmp_path / "valueclass_eq"
    src.write_text(
        textwrap.dedent(
            """
            import pcc

            @pcc.valueclass
            class Point:
                x: int
                y: int

            p = Point(1, 2)
            q = Point(1, 2)
            r = Point(1, 3)
            if p == q:
                print(1)
            else:
                print(0)
            if p != r:
                print(2)
            else:
                print(0)
            """
        )
    )

    compile_python(str(src), str(exe), libpython_mode="off", ir_scaffold_mode="on")
    proc = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert proc.stdout.strip().splitlines() == ["1", "2"]


def test_valueclass_payload_boxes_at_dyn_function_boundary(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    source = textwrap.dedent(
        """
        import pcc
        from typing import Any

        @pcc.valueclass
        class Point:
            x: int
            y: int

        def ident(x: Any) -> Any:
            return x

        p = Point(1, 2)
        d = ident(p)
        print(d.x)
        print(d.y)
        """
    )
    ir_text = _generate_ir(source)
    main_ir = ir_text[ir_text.index("define i32 @main") :]

    assert re.search(r"\bcall\b[^\n]*@py_valuebox_new\b", main_ir)
    assert re.search(r"\bcall\b[^\n]*@py_valuebox_set_field\b", main_ir)
    assert not re.search(r"\bcall\b[^\n]*@user_value_mod_Point___init__\b", main_ir)

    src = tmp_path / "valueclass_box.py"
    exe = tmp_path / "valueclass_box"
    src.write_text(source)
    compile_python(str(src), str(exe), libpython_mode="off", ir_scaffold_mode="on")
    proc = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert proc.stdout.strip().splitlines() == ["1", "2"]


def test_valueclass_payload_unboxes_from_dyn_function_boundary(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    source = textwrap.dedent(
        """
        import pcc
        from typing import Any

        @pcc.valueclass
        class Point:
            x: int
            y: int

        def ident(x: Any) -> Any:
            return x

        def total(p: Point) -> int:
            return p.x + p.y

        p = Point(3, 4)
        d = ident(p)
        print(total(d))
        """
    )
    ir_text = _generate_ir(source)
    main_ir = ir_text[ir_text.index("define i32 @main") :]

    assert re.search(r"\bcall\b[^\n]*@py_valuebox_get_field\b", main_ir)
    assert re.search(r"\bcall ptr \(\{ i64, i64 \}\) @user_value_mod_total", main_ir)
    assert not re.search(r"\bcall\b[^\n]*@user_value_mod_Point___init__\b", main_ir)

    src = tmp_path / "valueclass_unbox.py"
    exe = tmp_path / "valueclass_unbox"
    src.write_text(source)
    compile_python(str(src), str(exe), libpython_mode="off", ir_scaffold_mode="on")
    proc = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert proc.stdout.strip() == "7"


def test_valueclass_payload_unbox_rejects_wrong_dyn_type(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    source = textwrap.dedent(
        """
        import pcc
        from typing import Any

        @pcc.valueclass
        class Point:
            x: int
            y: int

        def ident(x: Any) -> Any:
            return x

        def total(p: Point) -> int:
            return p.x + p.y

        d = ident(123)
        print(total(d))
        """
    )
    ir_text = _generate_ir(source)
    assert "py_obj_isinstance" in ir_text
    assert "value.Point.unbox.typeerror" in ir_text

    src = tmp_path / "valueclass_unbox_wrong_type.py"
    exe = tmp_path / "valueclass_unbox_wrong_type"
    src.write_text(source)
    compile_python(str(src), str(exe), libpython_mode="off", ir_scaffold_mode="on")
    proc = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "TypeError" in (proc.stdout + proc.stderr)


def test_valueclass_pointer_payload_crosses_dyn_boundary(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    source = textwrap.dedent(
        """
        import gc
        import pcc
        from typing import Any

        @pcc.valueclass
        class Bag:
            items: list
            count: int

        def ident(x: Any) -> Any:
            return x

        def total(b: Bag) -> int:
            return len(b.items) + b.count

        bag = Bag([1, 2, 3], 4)
        d = ident(bag)
        gc.collect()
        print(total(d))
        print(len(d.items))
        """
    )
    ir_text = _generate_ir(source)
    main_ir = ir_text[ir_text.index("define i32 @main") :]

    assert re.search(r"\bcall\b[^\n]*@py_valuebox_new\b", main_ir)
    assert re.search(r"\bcall\b[^\n]*@py_valuebox_set_field\b", main_ir)
    assert re.search(r"\bcall\b[^\n]*@py_valuebox_get_field\b", main_ir)
    assert re.search(r"\bcall ptr \(\{ ptr, i64 \}\) @user_value_mod_total", main_ir)

    src = tmp_path / "valueclass_pointer_payload.py"
    exe = tmp_path / "valueclass_pointer_payload"
    src.write_text(source)
    compile_python(str(src), str(exe), libpython_mode="off", ir_scaffold_mode="on")
    proc = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert proc.stdout.strip().splitlines() == ["7", "3"]
