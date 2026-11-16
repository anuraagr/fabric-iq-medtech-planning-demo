from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from scripts.check_repo import check_files, heading_ids
from scripts.render_architecture import render, render_hero


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


def test_hero_has_accessible_context_and_does_not_depend_on_external_assets():
    svg = render_hero()
    root = ET.fromstring(svg)
    namespace = "{http://www.w3.org/2000/svg}"
    labels = root.attrib["aria-labelledby"].split()
    ids = {e.attrib["id"] for e in root.iter() if "id" in e.attrib}
    assert set(labels) <= ids
    description = root.find(f"{namespace}desc").text
    assert "illustrative" in description and "synthetic" in description
    assert all(f"<{element}" not in svg for element in ("script", "image", "foreignObject"))
    assert "href=" not in svg and "@import" not in svg and "https://" not in svg
    assert svg == render_hero()


def test_visual_check_includes_cover_and_refuses_a_stale_generated_source(tmp_path, monkeypatch, capsys):
    from scripts import render_architecture as visuals

    monkeypatch.setattr(visuals, "OUT", tmp_path)
    monkeypatch.setattr("sys.argv", ["render_architecture.py"])
    assert visuals.main() == 0
    monkeypatch.setattr("sys.argv", ["render_architecture.py", "--check"])
    assert visuals.main() == 0
    (tmp_path / "hero.svg").write_text("<svg/>", encoding="utf-8")
    assert visuals.main() == 1
    assert "Outdated visual: hero.svg" in capsys.readouterr().out


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
