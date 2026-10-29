"""Generate the README architecture SVG and editable Excalidraw source offline."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
WIDTH, HEIGHT = 1520, 1040


def render() -> tuple[str, str]:
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">',
        '<title id="title">MedTech Planning in Microsoft Fabric IQ</title>',
        '<desc id="description">Synthetic data feeds a shared semantic model and native Planning sheets. Native writeback persists budget, forecast and scenarios in SQL. Budget and forecast import back into the model after refresh. Executive Overview reads the model; Scenario Overview reads the planning sheet directly. PowerTable is a separate reference-data workflow. Product metadata is managed by Fabric.</desc>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 Z" fill="context-stroke"/></marker></defs>',
        '<rect width="1520" height="1040" fill="#ffffff"/>',
        '<g font-family="Segoe UI, Helvetica, Arial, sans-serif" fill="#000000">',
    ]
    elements = []

    def base(kind, x, y, w, h):
        return {"id": f"element-{len(elements) + 1}", "type": kind, "x": x, "y": y, "width": w, "height": h,
                "angle": 0, "strokeWidth": 2, "strokeStyle": "solid", "fillStyle": "solid", "roughness": 0,
                "opacity": 100, "groupIds": [], "seed": len(elements) + 1, "version": 1, "versionNonce": 1,
                "isDeleted": False, "boundElements": None, "updated": 1, "link": None, "locked": False}

    def text(x, y, value, size=18, bold=False):
        svg.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{600 if bold else 400}">{html.escape(value)}</text>')
        element = base("text", x, y - size, max(len(value) * size * 0.58, 20), size * 2.5)
        element.update(text=value, originalText=value, fontSize=size, fontFamily=2, textAlign="left",
                       verticalAlign="top", strokeColor="#000000", backgroundColor="transparent", lineHeight=1.25,
                       containerId=None, autoResize=True)
        elements.append(element)

    def box(x, y, w, h, stroke, fill, dashed=False):
        dash = ' stroke-dasharray="7 6"' if dashed else ""
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{fill if fill != "transparent" else "none"}" stroke="{stroke}" stroke-width="2"{dash}/>')
        element = base("rectangle", x, y, w, h)
        element.update(strokeColor=stroke, backgroundColor=fill, roundness={"type": 3}, strokeStyle="dashed" if dashed else "solid")
        elements.append(element)

    def card(x, y, w, h, title, lines, stroke, fill):
        box(x, y, w, h, stroke, fill)
        text(x + 22, y + 35, title, 21, True)
        for i, line in enumerate(lines):
            text(x + 22, y + 68 + i * 25, line, 17)

    def arrow(points, color="#0078D4", both=False):
        coords = " ".join(f"{x},{y}" for x, y in points)
        start = ' marker-start="url(#arrow)"' if both else ""
        svg.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" marker-end="url(#arrow)"{start}/>')
        x, y = points[0]
        element = base("arrow", x, y, max(p[0] for p in points) - min(p[0] for p in points),
                       max(p[1] for p in points) - min(p[1] for p in points))
        element.update(strokeColor=color, backgroundColor="transparent", points=[[px - x, py - y] for px, py in points],
                       startArrowhead="arrow" if both else None, endArrowhead="arrow", startBinding=None, endBinding=None)
        elements.append(element)

    text(54, 42, "MEDTECH + LIFE SCIENCES  /  REUSABLE SE DEMO", 15, True)
    text(54, 92, "Planning where the data lives", 38, True)
    text(54, 123, "Native experiences. Explicit writeback. Shared business definitions. No custom planning UI.", 20)
    box(24, 146, 1472, 848, "#CBD5E1", "transparent")
    text(54, 177, "YOUR MICROSOFT FABRIC WORKSPACE", 15, True)

    arrow([(302, 344), (378, 344)])
    text(310, 325, "TMDL", 15)
    arrow([(668, 344), (754, 344)])
    text(690, 325, "Read", 15)
    arrow([(523, 256), (523, 206), (1317, 206), (1317, 256)], "#5C2D91")
    text(838, 196, "Model measures + filters", 16)
    arrow([(904, 432), (904, 540)], "#107C10")
    text(920, 488, "Native Writeback", 16)
    arrow([(754, 607), (523, 607), (523, 432)], "#0078D4")
    text(536, 585, "Refresh + import", 17, True)
    text(536, 633, "Budget + forecast only", 15)
    arrow([(1054, 402), (1100, 402), (1100, 522), (1317, 522), (1317, 540)], "#5C2D91")
    text(1134, 508, "From Sheets (direct)", 16)

    card(54, 256, 248, 176, "Synthetic data",
         ["Fixed FY2026 story", "5 regions / 6 categories", "Actuals through Sep 2026", "No patient or customer data"], "#0078D4", "#EDF5FE")
    card(378, 256, 290, 176, "Shared semantic model",
         ["Actuals + P&L drivers", "Budget + forecast measures", "Region-aware calculations", "Import mode / TMDL"], "#0078D4", "#EDF5FE")
    card(754, 256, 300, 176, "Native Planning sheets",
         ["Budget / rolling forecast", "Scenarios / read-only P&L", "Infobridge shares forecast", "with the scenario sheet"], "#107C10", "#EFF8EB")
    card(1170, 256, 294, 176, "Executive Overview",
         ["Revenue + profit KPIs", "Actuals / budget / forecast", "Region + category filters", "Reads the semantic model"], "#5C2D91", "#F5EFFA")
    card(754, 540, 300, 134, "Business writeback SQL",
         ["Budget / forecast / scenarios", "Latest values + history", "Created by native Writeback"], "#0C8599", "#E8F7F8")
    card(1170, 540, 294, 134, "Scenario Overview",
         ["Base / Upside / Downside", "Sales + COGS cards / trends", "Reads saved planning values"], "#5C2D91", "#F5EFFA")
    card(54, 530, 350, 157, "Deploy in three stages",
         ["1  Provision foundation with REST", "2  Configure native sheets in the UI", "3  Attach SQL reporting + refresh", "No cross-tenant Plan import assumed"], "#64748B", "#F8FAFC")

    text(54, 754, "COMPANION WORKFLOW  /  NOT CONNECTED TO REVENUE CALCULATIONS", 15, True)
    arrow([(364, 858), (508, 858)], "#0C8599", both=True)
    text(382, 837, "Read / save", 16)
    card(54, 790, 310, 152, "PowerTable apps",
         ["Assets / Employees / Locations", "Required fields + controlled lists", "Lookups + numeric validation"], "#107C10", "#EFF8EB")
    card(508, 790, 310, 152, "Reference-data SQL",
         ["Medical-device registry", "Synthetic staff + locations", "Separate database / no PHI"], "#0C8599", "#E8F7F8")
    box(988, 790, 476, 152, "#64748B", "#F8FAFC", dashed=True)
    text(1010, 825, "Product-managed Planning metadata", 21, True)
    text(1010, 858, "Automatically provisioned by Fabric.", 17)
    text(1010, 883, "Not a business writeback destination.", 17)
    text(1010, 908, "This asset never queries or modifies it.", 17)
    text(54, 974, "Solid arrows: data flow. SQL reporting requires a model refresh. Native UI setup is a required deployment stage.", 17)
    text(54, 1024, "Illustrative architecture  |  Synthetic demo data  |  Not a production security or approval design", 16)
    svg.extend(["</g>", "</svg>"])
    source = {"type": "excalidraw", "version": 2, "source": "fabric-iq-medtech-planning-demo",
              "elements": elements, "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}}
    return "\n".join(svg) + "\n", json.dumps(source, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if checked-in diagram sources differ")
    args = parser.parse_args()
    svg, source = render()
    for name, content in (("architecture.svg", svg), ("architecture.excalidraw", source)):
        path = OUT / name
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                print(f"Outdated diagram: {name}; run python scripts/render_architecture.py")
                return 1
        else:
            OUT.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    print("Architecture sources match." if args.check else "Wrote SVG and editable Excalidraw source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
