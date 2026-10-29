from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from scripts.check_repo import check_files, heading_ids
from scripts.render_architecture import render


def test_diagram_is_parseable_accessible_and_has_editable_visible_text():
    svg, source = render()
    root = ET.fromstring(svg)
    assert root.attrib["width"] == "1520" and root.attrib["role"] == "img"
    assert root.find("{http://www.w3.org/2000/svg}title").text
    assert "From Sheets" in svg and "Refresh + import" in svg and "PowerTable" in svg
    assert "<script" not in svg and "href=" not in svg
    diagram = json.loads(source)
    assert diagram["type"] == "excalidraw" and diagram["version"] == 2
    assert len({e["id"] for e in diagram["elements"]}) == len(diagram["elements"])
    for element in diagram["elements"]:
        if element["type"] == "text":
            assert element["width"] > 0 and element["height"] > 0 and element["strokeColor"] == "#000000"
    assert render() == (svg, source)


def test_repo_checker_detects_dangling_links_and_runtime_files(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n[Missing](missing.md)\n", encoding="utf-8")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "manifest.json").write_text("{}", encoding="utf-8")
    problems = check_files(tmp_path, ["README.md", "state/manifest.json"])
    assert any("missing/unsafe link" in p for p in problems)
    assert any("must not be tracked" in p for p in problems)


def test_repo_checker_checks_headings_but_ignores_code_examples_and_external_links(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n[Guide](guide.md#safe-heading)\n[Web](https://example.test)\n```\n[Not a link](missing.md)\n```\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Safe heading\n", encoding="utf-8")
    assert check_files(tmp_path, ["README.md", "guide.md"]) == []
    assert heading_ids("# Same\n# Same\n") == {"same", "same-1"}
