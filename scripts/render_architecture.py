"""Generate the README's self-contained visual assets.

    python scripts/render_architecture.py                 # SVGs + editable architecture
    python scripts/render_architecture.py --render-png    # also PNGs + native-screen showcase
    python scripts/render_architecture.py --check         # deterministic vector-source check

PNG rendering uses a locally installed Edge (or --browser-channel chrome). No remote renderer,
font service, tenant access or network request is needed.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
WIDTH, HEIGHT = 1520, 1040
HERO_HEIGHT = 620
INK = "#173E48"
MUTED = "#506C72"
TEAL = "#087F8C"
BLUE = "#386B94"
GREEN = "#22786C"
CORAL = "#B65D40"
MIST = "#F1F7F6"
BORDER = "#CFDFDC"
DISPLAY = "Bahnschrift, 'Arial Narrow', Arial, sans-serif"
BODY = "'Trebuchet MS', Verdana, sans-serif"
MONO = "Consolas, 'Courier New', monospace"


def render() -> tuple[str, str]:
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">',
        '<title id="title">MedTech Planning in Microsoft Fabric IQ</title>',
        '<desc id="description">Synthetic data feeds a shared semantic model and native Planning sheets. Native writeback persists budget, forecast and scenarios in SQL. Budget and forecast import back into the model after refresh. Executive Overview reads the model; Scenario Overview reads the planning sheet directly. PowerTable is a separate reference-data workflow. Product metadata is managed by Fabric.</desc>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 Z" fill="context-stroke"/></marker></defs>',
        f'<rect width="1520" height="1040" fill="{MIST}"/>',
        '<rect x="24" y="146" width="1472" height="848" rx="20" fill="#ffffff"/>',
        f'<g font-family="{BODY}" fill="{INK}">',
    ]
    elements = []

    def base(kind, x, y, w, h):
        return {"id": f"element-{len(elements) + 1}", "type": kind, "x": x, "y": y, "width": w, "height": h,
                "angle": 0, "strokeWidth": 2, "strokeStyle": "solid", "fillStyle": "solid", "roughness": 0,
                "opacity": 100, "groupIds": [], "seed": len(elements) + 1, "version": 1, "versionNonce": 1,
                "isDeleted": False, "boundElements": None, "updated": 1, "link": None, "locked": False}

    def text(x, y, value, size=18, bold=False, *, family=None, color=INK):
        family = family or (DISPLAY if bold else BODY)
        svg.append(f'<text x="{x}" y="{y}" fill="{color}" font-family="{family}" font-size="{size}" font-weight="{600 if bold else 400}">{html.escape(value)}</text>')
        element = base("text", x, y - size, max(len(value) * size * 0.58, 20), size * 2.5)
        element.update(text=value, originalText=value, fontSize=size, fontFamily=2, textAlign="left",
                       verticalAlign="top", strokeColor="#000000", backgroundColor="transparent", lineHeight=1.25,
                       containerId=None, autoResize=True)
        elements.append(element)

    def box(x, y, w, h, stroke, fill, dashed=False):
        dash = ' stroke-dasharray="7 6"' if dashed else ""
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill if fill != "transparent" else "none"}" stroke="{stroke}" stroke-width="1.5"{dash}/>')
        element = base("rectangle", x, y, w, h)
        element.update(strokeColor=stroke, backgroundColor=fill, roundness={"type": 3}, strokeStyle="dashed" if dashed else "solid")
        elements.append(element)

    def card(x, y, w, h, title, lines, stroke, fill):
        box(x, y, w, h, stroke, fill)
        svg.append(f'<path d="M {x + 22} {y + 10} h 32" stroke="{stroke}" stroke-width="3" stroke-linecap="round"/>')
        text(x + 22, y + 35, title, 21, True)
        for i, line in enumerate(lines):
            text(x + 22, y + 68 + i * 25, line, 17, color=MUTED)

    def arrow(points, color=BLUE, both=False):
        coords = " ".join(f"{x},{y}" for x, y in points)
        start = ' marker-start="url(#arrow)"' if both else ""
        svg.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" marker-end="url(#arrow)"{start}/>')
        x, y = points[0]
        element = base("arrow", x, y, max(p[0] for p in points) - min(p[0] for p in points),
                       max(p[1] for p in points) - min(p[1] for p in points))
        element.update(strokeColor=color, backgroundColor="transparent", points=[[px - x, py - y] for px, py in points],
                       startArrowhead="arrow" if both else None, endArrowhead="arrow", startBinding=None, endBinding=None)
        elements.append(element)

    text(54, 42, "SYSTEM MAP  /  MICROSOFT FABRIC IQ", 16, family=MONO, color=MUTED)
    text(54, 92, "Every decision has a data path.", 42, True)
    text(54, 123, "Native inputs. Queryable commits. Explicit reporting refresh.", 21, color=MUTED)
    box(24, 146, 1472, 848, BORDER, "transparent")
    text(54, 177, "YOUR FABRIC WORKSPACE", 15, family=MONO, color=MUTED)
    for x, label in ((54, "GENERATE"), (378, "DEFINE"), (754, "PLAN"), (1170, "ANALYZE")):
        text(x, 239, label, 15, family=MONO, color=MUTED)

    arrow([(302, 344), (378, 344)])
    text(310, 325, "TMDL", 15)
    arrow([(668, 344), (754, 344)])
    text(690, 325, "Read", 15)
    arrow([(523, 256), (523, 206), (1317, 206), (1317, 256)], TEAL)
    text(838, 196, "Model measures + filters", 16, color=MUTED)
    arrow([(904, 432), (904, 540)], GREEN)
    text(920, 488, "Native Writeback", 17)
    arrow([(754, 607), (523, 607), (523, 432)], BLUE)
    text(536, 585, "Refresh + import", 17, True)
    text(536, 633, "Budget + forecast only", 15)
    arrow([(1054, 402), (1100, 402), (1100, 522), (1317, 522), (1317, 540)], TEAL)
    text(1134, 508, "From Sheets (direct)", 17)

    card(54, 256, 248, 176, "Synthetic actuals",
         ["Fixed FY2026 story", "5 regions / 6 categories", "Actuals to Sep 2026", "No real patient data"], BLUE, "#F1F6FA")
    card(378, 256, 290, 176, "Semantic model",
         ["Actuals + P&L drivers", "Budget + forecast measures", "Region-aware calculations", "Import mode / TMDL"], BLUE, "#F1F6FA")
    card(754, 256, 300, 176, "Native Planning sheets",
         ["Budget / rolling forecast", "Scenarios / read-only P&L", "Infobridge shares forecast", "with the scenario sheet"], GREEN, "#EDF6F0")
    card(1170, 256, 294, 176, "Executive Overview",
         ["Revenue + profit KPIs", "Actuals / budget / forecast", "Region + category filters", "Reads the semantic model"], TEAL, "#EDF8F7")
    card(754, 540, 300, 134, "Business writeback SQL",
         ["Budget / forecast / scenarios", "Latest values + history", "Created by native Writeback"], TEAL, "#E4F2EF")
    card(1170, 540, 294, 134, "Scenario Overview",
         ["Base / Upside / Downside", "Sales + COGS cards / trends", "Reads saved planning values"], TEAL, "#EDF8F7")
    card(54, 530, 350, 157, "Three deliberate stages",
         ["1  Provision foundation with REST", "2  Configure native sheets in the UI", "3  Attach SQL reporting + refresh", "No cross-tenant Plan import assumed"], "#64748B", "#F8FAFC")

    svg.append(f'<path d="M 54 721 H 1464" stroke="{BORDER}"/>')
    text(54, 754, "COMPANION WORKFLOW  /  SEPARATE FROM REVENUE CALCULATIONS", 15, family=MONO, color=MUTED)
    arrow([(364, 858), (508, 858)], TEAL, both=True)
    text(382, 837, "Read / save", 16)
    card(54, 790, 310, 152, "PowerTable apps",
         ["Assets / Employees / Locations", "Required fields + controlled lists", "Lookups + numeric validation"], GREEN, "#EDF6F0")
    card(508, 790, 310, 152, "Reference-data SQL",
         ["Medical-device registry", "Synthetic staff + locations", "Separate database / no PHI"], TEAL, "#E4F2EF")
    box(988, 790, 476, 152, BORDER, "#F7FAF9", dashed=True)
    text(1010, 825, "Product-managed Planning metadata", 21, True)
    text(1010, 858, "Automatically provisioned by Fabric.", 17)
    text(1010, 883, "Not a business writeback destination.", 17)
    text(1010, 908, "This asset never queries or modifies it.", 17)
    text(54, 974, "Solid arrows show data flow. SQL reporting requires refresh. Native sheet setup is an explicit deployment stage.", 17, color=MUTED)
    text(54, 1024, "SYNTHETIC DEMO  /  Not a production security or approval design", 15, family=MONO, color=MUTED)
    svg.extend(["</g>", "</svg>"])
    source = {"type": "excalidraw", "version": 2, "source": "fabric-iq-medtech-planning-demo",
              "elements": elements, "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}}
    return "\n".join(svg) + "\n", json.dumps(source, indent=2) + "\n"


def render_hero() -> str:
    """A scenario-branch motif, explicitly illustrative rather than fabricated product data."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HERO_HEIGHT}" viewBox="0 0 {WIDTH} {HERO_HEIGHT}" role="img" aria-labelledby="hero-title hero-description">',
        '<title id="hero-title">From actuals to a plan you can prove</title>',
        '<desc id="hero-description">MedTech and Life Sciences planning lab for Microsoft Fabric IQ. An illustrative historical trace branches into Upside, Base and Downside. Nine native sheets, SQL writeback, and deployment in your own tenant with synthetic data.</desc>',
        f'<rect width="{WIDTH}" height="{HERO_HEIGHT}" fill="{MIST}"/>',
        f'<rect x="20" y="20" width="1480" height="580" rx="24" fill="#ffffff" stroke="{BORDER}"/>',
        '<rect x="794" y="44" width="680" height="425" rx="16" fill="#EFF7F5"/>',
        f'<g font-family="{BODY}" fill="{INK}">',
    ]

    def text(x, y, value, size=22, *, family=BODY, color=INK, weight=400):
        parts.append(f'<text x="{x}" y="{y}" fill="{color}" font-family="{family}" font-size="{size}" font-weight="{weight}">{html.escape(value)}</text>')

    text(64, 82, "MEDTECH + LIFE SCIENCES", 18, family=MONO, color=MUTED)
    text(64, 182, "From actuals.", 70, family=DISPLAY, weight=600)
    text(64, 260, "To a plan", 70, family=DISPLAY, weight=600)
    text(64, 338, "you can prove.", 70, family=DISPLAY, color=TEAL, weight=600)
    text(66, 399, "A native planning lab for Microsoft Fabric IQ.", 23)
    text(66, 432, "Build the plan. Commit it. Trace it back.", 22, color=MUTED)

    text(828, 86, "ONE HISTORY. THREE POSSIBLE FUTURES.", 17, family=MONO)
    for x in range(844, 1435, 58):
        parts.append(f'<path d="M {x} 125 V 386" stroke="#D8E8E3" stroke-width="1"/>')
    for y in range(146, 387, 48):
        parts.append(f'<path d="M 828 {y} H 1438" stroke="#D8E8E3" stroke-width="1"/>')
    parts.append('<rect x="1102" y="126" width="336" height="260" fill="#E0EFEB" opacity=".55"/>')
    parts.append(f'<path d="M 1102 126 V 386" stroke="{MUTED}" stroke-dasharray="4 6"/>')
    parts.append(f'<path d="M 844 322 L 900 304 L 956 314 L 1012 274 L 1060 282 L 1102 252" fill="none" stroke="{INK}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>')
    branches = [
        ("M 1102 252 L 1172 219 L 1242 194 L 1346 155", TEAL, "Upside", 161),
        ("M 1102 252 L 1172 254 L 1242 242 L 1346 232", MUTED, "Base", 238),
        ("M 1102 252 L 1172 281 L 1242 310 L 1346 340", CORAL, "Downside", 346),
    ]
    for path, color, label, y in branches:
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>')
        text(1360, y, label, 18, color=color, weight=600)
    parts.append(f'<circle cx="1102" cy="252" r="8" fill="#ffffff" stroke="{TEAL}" stroke-width="3"/>')
    for x, y in ((844, 322), (956, 314), (1060, 282)):
        parts.append(f'<circle cx="{x}" cy="{y}" r="4" fill="{INK}"/>')
    text(844, 418, "HISTORICAL ACTUALS", 16, family=MONO, color=MUTED)
    text(1130, 418, "SCENARIO PLANNING", 16, family=MONO, color=MUTED)
    text(844, 450, "Illustrative paths / not plotted financial results", 15, color=MUTED)

    parts.append(f'<path d="M 64 493 H 1456" stroke="{BORDER}"/>')
    for x, label, detail in (
        (64, "9 native sheets", "Planning + PowerTable + Intelligence"),
        (430, "SQL writeback", "Persisted values, not just visuals"),
        (796, "Your tenant", "Reusable setup + explicit UI steps"),
        (1162, "Synthetic by design", "No real patient or customer data"),
    ):
        parts.append(f'<path d="M {x} 521 v 40" stroke="{TEAL}" stroke-width="3"/>')
        text(x + 14, 538, label, 22, family=DISPLAY, weight=600)
        text(x + 14, 566, detail, 15, color=MUTED)
    parts.extend(["</g>", "</svg>"])
    return "\n".join(parts) + "\n"


def render_pngs(channel: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel, headless=True)
        try:
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2)
            page.route("**/*", lambda route: route.abort())
            for name in ("hero", "architecture"):
                svg = (OUT / f"{name}.svg").read_text(encoding="utf-8")
                page.set_content(f'<html><body style="margin:0;background:white">{svg}</body></html>')
                page.evaluate("document.fonts.ready")
                page.locator("svg").screenshot(path=str(OUT / f"{name}.png"), timeout=60000)
        finally:
            browser.close()


def render_showcase() -> None:
    """Compose the already-redacted reference screenshots without altering their contents."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    def font(size: int, bold: bool = False):
        candidates = ("bahnschrift.ttf", "DejaVuSans-Bold.ttf") if bold else ("trebuc.ttf", "DejaVuSans.ttf")
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
        return ImageFont.load_default(size=size)

    canvas = Image.new("RGB", (WIDTH, 1110), MIST)
    draw = ImageDraw.Draw(canvas)
    draw.text((44, 30), "INSIDE THE NATIVE DEMO", fill=MUTED, font=font(18))
    draw.text((44, 62), "Real screens. One connected planning story.", fill=INK, font=font(40, True))
    draw.text((44, 113), "Reference deployment / synthetic data / no custom planning application", fill=MUTED, font=font(19))
    screens = [
        ("11-intelligence-page.jpg", "Executive Overview", "ANALYZE", TEAL),
        ("14-scenario-overview.jpg", "Scenario Overview", "COMPARE", TEAL),
        ("01-budget-top-down.jpg", "Revenue Budget", "PLAN", GREEN),
        ("05-powertable-assets.jpg", "Medical-device registry", "OPERATE", BLUE),
    ]
    for index, (filename, title, category, color) in enumerate(screens):
        x, y = 44 + (index % 2) * 732, 164 + (index // 2) * 450
        draw.rounded_rectangle((x, y, x + 700, y + 426), radius=16, fill="#ffffff", outline=BORDER, width=2)
        draw.line((x + 22, y + 26, x + 22, y + 52), fill=color, width=4)
        draw.text((x + 38, y + 18), title, fill=INK, font=font(27, True))
        category_font = font(15)
        category_width = draw.textlength(category, font=category_font)
        draw.text((x + 678 - category_width, y + 29), category, fill=color, font=category_font)
        draw.line((x + 22, y + 66, x + 678, y + 66), fill=BORDER, width=1)
        with Image.open(OUT / filename) as image:
            preview = ImageOps.contain(image.convert("RGB"), (656, 326), Image.Resampling.LANCZOS)
        canvas.paste(preview, (x + 22 + (656 - preview.width) // 2, y + 82 + (326 - preview.height) // 2))
    draw.text((44, 1070), "Open the full-resolution screenshots from the README. Validate your own tenant before presenting.", fill=MUTED, font=font(18))
    canvas.save(OUT / "demo-showcase.png", optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if checked-in diagram sources differ")
    parser.add_argument("--render-png", action="store_true", help="render PNGs locally and compose the screenshot showcase")
    parser.add_argument("--browser-channel", choices=["msedge", "chrome"], default="msedge")
    args = parser.parse_args()
    if args.check and args.render_png:
        parser.error("--check cannot be combined with --render-png")
    svg, source = render()
    for name, content in (("architecture.svg", svg), ("architecture.excalidraw", source), ("hero.svg", render_hero())):
        path = OUT / name
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                print(f"Outdated visual: {name}; run python scripts/render_architecture.py")
                return 1
        else:
            OUT.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if args.render_png:
        render_pngs(args.browser_channel)
        render_showcase()
    print("Visual sources match." if args.check else "Wrote visual sources" + (" and rendered previews." if args.render_png else "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
