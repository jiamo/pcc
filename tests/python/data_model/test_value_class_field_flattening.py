from __future__ import annotations

from pcc import valueclass
from pcc.value_model import (
    SpecializedArray,
    box_value,
    flatten_fields,
    specialized_array,
    specialize_generic_signature,
    unbox_value,
    value_model_status,
)


def test_valueclass_field_flattening_preserves_nested_field_names():
    @valueclass
    class Point:
        x: int
        y: int

    @valueclass
    class Segment:
        start: Point
        end: Point

    assert flatten_fields(Point) == (("x", "int"), ("y", "int"))
    assert flatten_fields(Segment) == (
        ("start.x", "int"),
        ("start.y", "int"),
        ("end.x", "int"),
        ("end.y", "int"),
    )


def test_valueclass_box_unbox_and_specialized_array_use_flattened_payloads():
    @valueclass
    class Point:
        x: int
        y: int
        z: bool

    p1 = Point(1, 2, True)
    p2 = Point(3, 4, False)
    boxed = box_value(p1)
    assert unbox_value(boxed).values == (1, 2, True)

    arr = specialized_array([p1, p2])
    assert isinstance(arr, SpecializedArray)
    assert len(arr) == 2
    assert arr[0].unbox().values == (1, 2, True)
    assert arr[1].unbox().values == (3, 4, False)


def test_valueclass_generic_signature_and_status_reporting_surface_v1():
    spec = specialize_generic_signature("Pair", "Point", int)
    assert spec.payload_abi.endswith("::value_payload")
    status = value_model_status()
    assert (
        status["implemented_through"]
        == "V1-direct-scalar-and-nested-payload-eq-checked-marshal-"
        "v2-pointer-and-nested-dyn-boundary-partial"
    )
    assert "ValueClassType frontend model" in status["implemented"]
