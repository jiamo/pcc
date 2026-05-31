from pcc.py_frontend.export_meta import decode_type
from pcc.py_frontend.py_ast import ClassType, IntType, ListType


def test_decode_type_caches_tuple_descriptors():
    desc = (
        "list",
        (
            "class",
            "Node",
            "pkg.model",
            (("value", ("int", 64, True)),),
            (),
        ),
    )

    first = decode_type(desc)
    second = decode_type(desc)

    assert first is second
    assert isinstance(first, ListType)
    assert isinstance(first.elem, ClassType)
    assert first.elem.name == "Node"
    assert first.elem.fields == (("value", IntType(name="int", width=64, signed=True)),)


def test_decode_type_preserves_existing_type_passthrough():
    ty = IntType(name="int", width=32, signed=False)

    assert decode_type(ty) is ty
