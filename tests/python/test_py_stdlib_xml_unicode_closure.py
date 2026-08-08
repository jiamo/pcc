"""Focused source for Meson's Unicode-width and ElementTree closure."""
from __future__ import annotations

import os
import subprocess
import sys
import unicodedata as host_unicodedata
import xml.etree.ElementTree as host_etree

import pytest

from pcc.py_stdlib import unicodedata as port_unicodedata
from pcc.py_stdlib.xml.etree import ElementTree as port_etree


@pytest.mark.parametrize(
    "character",
    ["A", "\u4e2d", "\u1100", "\uff21", "\uff71", "\U0001f642"],
)
def test_east_asian_width_matches_cpython_for_build_tool_classes(character):
    assert port_unicodedata.east_asian_width(character) == (
        host_unicodedata.east_asian_width(character)
    )


def _tree(module):
    root = module.Element("testsuites", tests="1", errors="0")
    suite = module.SubElement(root, "testsuite", {"name": "native & owned"})
    case = module.SubElement(suite, "testcase", name="case-1")
    module.SubElement(case, "system-out").text = "left < right"
    module.SubElement(case, "skipped")
    return root


def test_element_construction_and_serialization_match_cpython():
    assert port_etree.tostring(_tree(port_etree), encoding="unicode") == (
        host_etree.tostring(_tree(host_etree), encoding="unicode")
    )
    assert port_etree.tostring(
        _tree(port_etree), encoding="utf-8", xml_declaration=True
    ) == host_etree.tostring(
        _tree(host_etree), encoding="utf-8", xml_declaration=True
    )


def test_element_parse_text_tail_and_xpath_subset_match_cpython():
    source = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<testsuites><!--comment--><testsuite name='one'>"
        "prefix<testcase result='ok'>value &amp; more</testcase>tail"
        "<testcase timestamp='now' /></testsuite></testsuites>"
    )
    port_root = port_etree.fromstring(source)
    host_root = host_etree.fromstring(source)
    assert port_root.tag == host_root.tag
    assert [item.tag for item in port_root.findall(".")] == [
        item.tag for item in host_root.findall(".")
    ]
    assert [item.attrib for item in port_root.findall(".//testcase")] == [
        item.attrib for item in host_root.findall(".//testcase")
    ]
    assert [item.text for item in port_root.findall(".//testcase[@result]")] == [
        item.text for item in host_root.findall(".//testcase[@result]")
    ]
    assert port_root.find(".//testcase").tail == host_root.find(
        ".//testcase"
    ).tail


def test_element_tree_file_roundtrip_matches_cpython(tmp_path):
    port_path = tmp_path / "port.xml"
    host_path = tmp_path / "host.xml"
    port_etree.ElementTree(_tree(port_etree)).write(
        port_path, encoding="utf-8", xml_declaration=True
    )
    host_etree.ElementTree(_tree(host_etree)).write(
        host_path, encoding="utf-8", xml_declaration=True
    )
    assert port_path.read_bytes() == host_path.read_bytes()
    reparsed = port_etree.parse(port_path)
    assert reparsed.getroot().find(".//testcase").attrib == {"name": "case-1"}


def test_unowned_xml_surfaces_fail_closed():
    with pytest.raises(port_etree.ParseError, match="DTD"):
        port_etree.fromstring("<!DOCTYPE root><root />")
    with pytest.raises(NotImplementedError, match="XPath"):
        port_etree.Element("root").findall(".//item[1]")
    with pytest.raises(NotImplementedError, match="encoding"):
        port_etree.tostring(port_etree.Element("root"), encoding="utf-16")


@pytest.mark.parametrize(
    "module_name,suffix",
    [
        ("unicodedata", "/pcc/py_stdlib/unicodedata.py"),
        ("xml", "/pcc/py_stdlib/xml/__init__.py"),
        ("xml.etree", "/pcc/py_stdlib/xml/etree/__init__.py"),
        ("xml.etree.ElementTree", "/pcc/py_stdlib/xml/etree/ElementTree.py"),
    ],
)
def test_xml_unicode_family_is_selected_by_recursive_stdlib_registry(
    module_name, suffix
):
    from pcc.py_frontend import pipeline

    source = pipeline._locate_native_stdlib_module_source(module_name)
    assert source is not None
    assert source.endswith(suffix)
    assert pipeline._classify_python_import(module_name) == "native_stdlib"


@pytest.mark.integration
def test_xml_unicode_match_cpython_strict_self_no_libpython(tmp_path):
    source = '''\
import unicodedata
import xml.etree.ElementTree as ET

root = ET.Element("testsuites", tests="1", errors="0")
suite = ET.SubElement(root, "testsuite", name="native & owned")
case = ET.SubElement(suite, "testcase", result="ok")
case.text = "left < right"
xml = ET.tostring(root, encoding="unicode")
parsed = ET.fromstring(xml)
print("width", [unicodedata.east_asian_width(c) for c in "A\u4e2d\uff21\uff71\U0001f642"])
print("xml", xml)
print("find", parsed.find(".//testcase[@result]").text)
'''
    src = tmp_path / "xml_unicode_probe.py"
    src.write_text(source, encoding="utf-8")
    executable = tmp_path / "xml_unicode_probe"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env.pop("PCC_RUNTIME_CC", None)
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
            str(executable),
        ],
        text=True,
        capture_output=True,
        timeout=900,
        env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    assert "PCC-PY-COMPILE-001" not in build.stdout + build.stderr

    no_host_env = env.copy()
    no_host_env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    no_host_env["PATH"] = str(tmp_path / "no-host-python")
    actual = subprocess.run(
        [str(executable)],
        text=True,
        capture_output=True,
        timeout=120,
        env=no_host_env,
    )
    expected = subprocess.run(
        [sys.executable, str(src)],
        text=True,
        capture_output=True,
        timeout=120,
        env=env,
    )
    assert actual.returncode == 0, actual.stdout + actual.stderr
    assert expected.returncode == 0, expected.stdout + expected.stderr
    assert actual.stdout == expected.stdout
