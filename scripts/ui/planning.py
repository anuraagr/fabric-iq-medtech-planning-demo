"""Reusable helpers for driving the native Fabric IQ Planning UI with Playwright (see scripts/ui/session.py).

Facts these helpers encode (verified against the live UI, October 2026):
  * The Planning app lives in a cross-origin frame (pbifpw.powerbi.com/<plan id>); frames attach asynchronously.
  * Fields are placed by dragging a Data-pane item (`div.react-fields-keeper-mapping-column-content-wrapper`) onto the
    Rows / Columns / Values drop zones (`.react-fields-keeper-mapping-content-input`, in that order). Dropping on a
    zone inserts at the top of that zone.
  * Ribbon tabs are buttons named like "Planning, main menu, 2 of 6, not selected".
  * The Data pane caches the semantic-model schema; the refresh icon next to the "Data" title reloads it.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Sequence

from .session import planning_frame

REPO_ROOT = Path(__file__).resolve().parents[2]
SHOT_DIR = REPO_ROOT / "state" / "ui"
ZONES = {"rows": 0, "columns": 1, "values": 2}
FIELD_ITEM = "div.react-fields-keeper-mapping-column-content-wrapper[role=button]"
DROP_ZONE = ".react-fields-keeper-mapping-content-input"
REFRESH_ICON = 'svg:has(path[d^="M3 12a9 9 0 0 1 9-9"])'


def nearest_cell_index(left_edges: Sequence[float], target_center: float, width: float, tolerance: float = 0.6) -> int:
    """Index of the rendered cell whose centre is closest to `target_center`. The grid is virtualised, so the wanted column may not be rendered:
    when no cell lies within `tolerance` of a column width, raise instead of returning a neighbour (typing into it would write the wrong cell)."""
    if not left_edges:
        raise LookupError("the row has no rendered cells")
    best = min(range(len(left_edges)), key=lambda i: abs(left_edges[i] + width / 2 - target_center))
    if abs(left_edges[best] + width / 2 - target_center) > width * tolerance:
        raise LookupError("the wanted column is not rendered (scroll it into view); the nearest rendered cell belongs to a neighbouring column")
    return best


class PlanUI:
    def __init__(self, page) -> None:
        self.page = page

    @classmethod
    def attach(cls, context, plan_id: str) -> "PlanUI":
        for page in context.pages:
            if plan_id.lower() in page.url.lower():
                page.mouse.up()
                page.bring_to_front()  # screenshots hang on a background tab
                return cls(page)
        raise LookupError(f"No open Edge tab for plan {plan_id}")

    # --- frame / inspection -------------------------------------------------------------------
    @property
    def fr(self):
        frame = planning_frame(self.page, 30)
        if frame is None:
            raise RuntimeError("Planning frame is not available")
        return frame

    @property
    def vf(self):
        """The visible nested Inforiver visual frame (one per sheet) hosting the grid and the side panels."""
        deadline = time.time() + 30
        while time.time() < deadline:
            candidates = [f for f in self.page.frames if "inforiver-visual/index.html" in f.url]
            for frame in reversed(candidates):
                try:
                    if frame.frame_element().is_visible():
                        return frame
                except Exception:
                    continue
            self.wait(500)
        raise RuntimeError("Planning visual frame is not available")

    def pt_visual(self, timeout: float = 30.0):
        """The nested editable-visual frame that hosts the PowerTable grid (one is attached per PowerTable sheet)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for frame in reversed([f for f in self.page.frames if "editable-visual/index.html" in f.url]):
                try:
                    if frame.frame_element().is_visible():
                        return frame
                except Exception:  # noqa: BLE001 - the frame can detach while the sheet re-renders
                    continue
            self.wait(500)
        raise RuntimeError("PowerTable visual frame is not available")

    def pt_wizard(self, timeout: float = 15.0):
        """The create-table wizard frame (Select Fabric SQL Connection / Configure source dialogs)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for frame in self.page.frames:
                if "power-tables/create-table" in frame.url:
                    return frame
            self.wait(500)
        raise RuntimeError("PowerTable create-table frame is not available")

    def snapshot(self, *, visual: bool = False) -> str:
        return (self.vf if visual else self.fr).locator("body").aria_snapshot()

    def section(self, start: str, length: int = 3000, *, visual: bool = False) -> str:
        text = self.snapshot(visual=visual)
        index = text.find(start)
        return text[index:index + length] if index >= 0 else f"<{start!r} not found>"

    def shot(self, name: str, attempts: int = 3) -> Path:
        """Save a screenshot. A capture right after a pane or zoom change can stall until its timeout while a retry a moment later takes about a second,
        so a short timeout with a few retries is faster than one long wait."""
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOT_DIR / f"{name}.png"
        for attempt in range(1, attempts + 1):
            try:
                self.page.screenshot(path=str(path), timeout=20000)
                return path
            except Exception:  # noqa: BLE001 - Playwright raises TimeoutError; anything else is retried the same way
                if attempt == attempts:
                    raise
                self.wait(3000)
        return path

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)

    def doc_image(self, name: str, width: int = 1400, quality: int = 82) -> Path:
        """Save the current page as a documentation image `docs/images/<name>.jpg`, scaled to `width` pixels (the evidence images in the docs)."""
        from PIL import Image

        source = self.shot(f"doc-{name}")
        target = REPO_ROOT / "docs" / "images" / f"{name}.jpg"
        with Image.open(source) as image:
            rgb = image.convert("RGB")
            rgb.resize((width, round(rgb.height * width / rgb.width)), Image.Resampling.LANCZOS).save(target, "JPEG", quality=quality, optimize=True)
        return target

    def wait_for_text(self, text: str, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if text in self.snapshot():
                return True
            self.wait(1000)
        return False

    def toasts(self) -> list[str]:
        text = self.section('region "Notifications', 1500)
        return [line.strip("- ").strip() for line in text.splitlines()[1:] if line.strip() and "list" not in line][:6]

    # --- reading / editing mode ---------------------------------------------------------------
    def current_mode(self) -> str:
        """'Editing', 'Reading' or 'unknown' (no plan UI yet): read from the mode combobox in the top-right corner."""
        try:
            match = re.search(r'combobox "(Editing|Reading) selected"', self.snapshot())
        except Exception:  # noqa: BLE001 - the frame can be missing while the plan loads or after a crash
            return "unknown"
        return match.group(1) if match else "unknown"

    def open_plan(self, url: str, timeout: float = 120.0) -> str:
        """Load the plan URL and wait until its mode combobox shows; a plan always opens in Reading mode. Returns the mode."""
        self.page.goto(url, wait_until="domcontentloaded", timeout=90000)
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.wait(3000)
            mode = self.current_mode()
            if mode != "unknown":
                self.wait(3000)
                return mode
        raise RuntimeError("The plan did not render within the timeout (is the capacity throttled? see docs/architecture.md section 9)")

    def set_mode(self, mode: str, *, keep_changes: bool = False, timeout: float = 120.0) -> None:
        """Switch between 'Reading' and 'Editing'. Switching with a sheet open asks whether to retain properties changed in the current mode:
        `keep_changes` picks *Save and Proceed* instead of *Discard and Proceed*.

        If the plan area turns white instead, the Planning app crashed while rendering the editing toolbar. The known cause is a stale cached Graph
        token (see `drop_cached_graph_token`); drop it, `open_plan` again and retry."""
        if self.current_mode() == mode:
            return
        self.fr.get_by_role("combobox", name=re.compile(r"^(Editing|Reading) selected$")).click(timeout=15000)
        self.wait(1200)
        self.fr.get_by_role("option", name=mode).click(timeout=15000)
        deadline = time.time() + timeout
        answered = False
        while time.time() < deadline:
            self.wait(2500)
            if not answered:
                dialog = self.fr.get_by_role("button", name="Save and Proceed" if keep_changes else "Discard and Proceed")
                try:
                    if dialog.count():
                        dialog.first.click(timeout=8000)
                        answered = True
                        continue
                except Exception:  # noqa: BLE001 - the dialog can close while it is being clicked
                    pass
            if self.current_mode() == mode:
                self.wait(3000)
                return
        raise RuntimeError(f"The plan did not switch to {mode} mode: if the page is blank, drop the cached Graph token (scripts.ui.session.drop_cached_graph_token) and reopen it")

    # --- ribbon ---------------------------------------------------------------------------------
    def tab(self, name: str) -> None:
        pattern = re.compile(rf"^{re.escape(name)}, main menu")
        for attempt in range(3):
            try:
                self.fr.get_by_role("button", name=pattern).click(timeout=10000)
                self.wait(1500)
                return
            except Exception:  # noqa: BLE001 - the app can be busy re-rendering; retry with a fresh frame handle
                self.page.mouse.up()
                self.wait(3000)
        self.fr.get_by_role("button", name=pattern).click(timeout=20000, force=True)
        self.wait(1500)

    def ribbon(self) -> list[str]:
        """Names of the ribbon controls currently shown (menuitems and buttons before the main area)."""
        text = self.snapshot()
        head = text[: text.find("- main:")] if "- main:" in text else text
        return [m for m in re.findall(r'- (?:menuitem|button|combobox) "([^"]+)"', head)]

    def click_ribbon(self, name: str, *, exact: bool = True) -> None:
        for role in ("menuitem", "button"):
            target = self.fr.get_by_role(role, name=name, exact=exact)
            if target.count():
                target.first.click(timeout=10000)
                self.wait(1500)
                return
        raise LookupError(f"Ribbon control {name!r} not found")

    def save(self) -> bool:
        """Click Save when there is something to save; returns True when a save was triggered."""
        button = self.fr.get_by_role("button", name="Save", exact=True).first
        try:
            if not button.is_enabled():
                return False
            button.click(timeout=8000)
        except Exception:  # noqa: BLE001 - nothing to save or the button is not actionable
            return False
        self.wait(7000)
        return True

    # --- grid ------------------------------------------------------------------------------------
    def grid(self) -> list[dict]:
        """Visible grid rows (virtualised): [{'label', 'y', 'cells': [{'x', 'text', 'index'}]}] ordered top to bottom.

        The gridcells are one flat DOM list: first the frozen row-label column (equal x, increasing y), then one group per
        row holding an off-screen copy of the label followed by the data cells. `index` addresses a cell with
        `vf.locator('[role=gridcell]').nth(index)`."""
        raw = self.vf.locator("[role=gridcell]").evaluate_all(
            "els => els.map((e, i) => { const r = e.getBoundingClientRect(); return {i, x: Math.round(r.x), y: Math.round(r.y), t: e.innerText.trim()}; })"
        )
        if not raw:
            return []
        frozen_x, frozen = raw[0]["x"], []
        for cell in raw:
            if cell["x"] != frozen_x or (frozen and cell["y"] <= frozen[-1]["y"]):
                break
            frozen.append(cell)
        by_y: dict[int, list[dict]] = {}
        for cell in raw[len(frozen):]:
            by_y.setdefault(cell["y"], []).append(cell)
        rows = []
        for label_cell in frozen:
            group = sorted(by_y.get(label_cell["y"], []), key=lambda c: c["x"])
            rows.append({
                "label": label_cell["t"], "y": label_cell["y"], "label_index": label_cell["i"],
                "cells": [{"x": c["x"], "text": c["t"], "index": c["i"]} for c in group[1:]],
            })
        return rows

    def find_row(self, label: str, *, after: str | None = None) -> dict:
        rows, start = self.grid(), 0
        if after is not None:
            start = next((i for i, r in enumerate(rows) if r["label"] == after), None)
            if start is None:
                raise LookupError(f"Row {after!r} is not visible")
        for row in rows[start:]:
            if row["label"] == label:
                return row
        raise LookupError(f"Row {label!r} not found" + (f" after {after!r}" if after else ""))

    def cell(self, row: dict, column: int):
        return self.vf.locator("[role=gridcell]").nth(row["cells"][column]["index"])

    def read_cell(self, row: dict, column: int) -> str:
        return self.cell(row, column).inner_text().strip()

    def _hierarchy_headers(self, label: str):
        vf = self.vf
        return vf.locator("div.hierarchy-header").filter(has=vf.locator("span.hierarchy-header-text", has_text=re.compile(rf"^{re.escape(label)}$")))

    def _hierarchy_header(self, label: str):
        return self._hierarchy_headers(label).first

    def expand_column(self, label: str) -> None:
        """Expand a collapsed column-hierarchy header (Year > Quarter > Month Short) by its text; the chevron sits left of the label."""
        icon = self._hierarchy_header(label).locator("span.isColumnExpandCollapseIconHeader")
        if icon.get_attribute("aria-expanded") == "false":
            icon.click(timeout=8000)
            self.wait(3500)

    def column_index(self, row: dict, group_label: str, position: int, of: int) -> int:
        """Index in `row['cells']` of the `position`-th (0-based) of `of` measure columns under the column group `group_label` (for example 'Oct').

        Every measure column of a group has the same width and the group header spans all of them, so the column is found by its x position.
        Raises LookupError when the label is ambiguous (the same month under several expanded parents) or the wanted column is not rendered."""
        headers = self._hierarchy_headers(group_label)
        if headers.count() != 1:
            raise LookupError(f"{headers.count()} column headers are labelled {group_label!r}: expand only the wanted parent group")
        box = headers.first.evaluate("e => { const r = e.getBoundingClientRect(); return {x: r.x, w: r.width}; }")
        width = box["w"] / of
        return nearest_cell_index([cell["x"] for cell in row["cells"]], box["x"] + width * (position + 0.5), width)

    def enter_value(self, row: dict, column: int, value: str) -> None:
        """Type a value into a data cell (never into the label column: that renames the member).

        The cell opens an inline editor on double-click; typing too fast loses characters, hence the key delay."""
        target = self.cell(row, column)
        target.dblclick(timeout=8000)
        self.wait(1200)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.type(value, delay=120)
        self.wait(600)
        self.page.keyboard.press("Enter")
        self.wait(3500)

    def confirm(self, answer: str = "Yes") -> bool:
        """Answer a 'Confirm your action' style dialog in the visual frame when one is showing."""
        button = self.vf.get_by_role("button", name=answer, exact=True)
        if button.count():
            button.first.click(timeout=8000)
            self.wait(2500)
            return True
        return False

    # --- columns ------------------------------------------------------------------------------------
    def set_title(self, title: str) -> None:
        """Replace the Title field (a CodeMirror editor: `fill` would append to the default text)."""
        editor = self.vf.locator(".CodeMirror").first
        editor.click(timeout=8000)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.type(title, delay=40)
        self.wait(600)

    def insert_number_copy(self, source: str, title: str) -> None:
        """Planning > Insert Column > Number > Copy from another series > <source>, then name it."""
        self.tab("Planning")
        self.click_ribbon("Insert Column")
        self.fr.get_by_role("menuitem", name="Number", exact=True).first.hover()
        self.wait(800)
        self.fr.get_by_role("menuitem", name="Copy from another series").first.click(timeout=8000)
        self.wait(1200)
        self.fr.get_by_role("menuitem", name=source, exact=True).first.click(timeout=8000)
        self.wait(2500)
        save = self.fr.get_by_role("button", name="Save and continue")
        if save.count():
            save.first.click(timeout=8000)
            self.wait(9000)
        self.set_title(title)
        self.vf.get_by_role("button", name="Create", exact=True).click(timeout=8000)
        self.wait(7000)

    def insert_formula(self, title: str, formula: str) -> None:
        """Planning > Insert Column > Formula: title and formula are both CodeMirror editors (title first)."""
        self.tab("Planning")
        self.click_ribbon("Insert Column")
        self.fr.get_by_role("menuitem", name="Formula", exact=True).first.click(timeout=8000)
        self.wait(2500)
        save = self.fr.get_by_role("button", name="Save and continue")
        if save.count():
            save.first.click(timeout=8000)
            self.wait(9000)
        editors = self.vf.locator(".CodeMirror")
        editors.nth(0).click(timeout=8000)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.type(title, delay=40)
        editors.nth(1).click(timeout=8000)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.type(formula, delay=40)
        self.wait(800)
        self.page.keyboard.press("Escape")
        self.wait(600)

    # --- writeback ------------------------------------------------------------------------------------
    def configure_writeback(self, table: str, database_row: str, connection_hint: str, keep_series: list[str], wb_type: str = "Long with Changes") -> None:
        """Writeback > WB Type, Add destination (existing shared Fabric SQL connection), select it and limit the series.

        Changing the writeback type clears the selected destinations, so the type is set first and the destination is
        (re)selected afterwards."""
        self.tab("Writeback")
        self.click_ribbon("WB Type")
        self.fr.get_by_role("menuitem", name=wb_type, exact=True).first.click(timeout=8000)
        self.wait(3000)
        proceed = self.vf.get_by_role("button", name="Proceed")
        if proceed.count():
            proceed.first.click(timeout=8000)
            self.wait(3000)
        self.add_destination(table, database_row, connection_hint)
        self.limit_writeback_series(table, keep_series)

    def add_destination(self, table: str, database_row: str, connection_hint: str) -> None:
        """Writeback > Add destination: connection, Fabric SQL database item, schema `dbo` (the default) and a new table name."""
        self.tab("Writeback")
        self.click_ribbon("Add destination")
        self.wait(2500)
        dialog = self.fr.get_by_role("dialog", name="Add Destination")
        connection = dialog.get_by_role("combobox", name="Select a Connection")
        if connection_hint.casefold() not in connection.inner_text().casefold():  # the dialog pre-selects the only matching shared connection
            connection.click(timeout=8000)
            self.wait(1500)
            self.fr.get_by_text(connection_hint, exact=False).first.click(timeout=8000)
            self.wait(3500)
        dialog.get_by_role("combobox").filter(has_text="Select a Database").first.click(timeout=10000)
        self.wait(4000)
        picker = self.page.get_by_role("dialog", name="Select destination database")
        picker.get_by_role("row", name=f"Name {database_row}").first.click(timeout=10000)
        self.wait(800)
        picker.get_by_role("button", name="Add", exact=True).click(timeout=10000)
        self.wait(3000)
        dialog.get_by_role("textbox", name="Enter the Table Name").fill(table)
        self.wait(600)
        dialog.get_by_role("button", name="Add", exact=True).click(timeout=20000)  # waits for the button to be enabled again after the name is validated
        deadline = time.time() + 60
        while dialog.count() and time.time() < deadline:  # the dialog closes once the destination is registered
            self.wait(2000)

    def limit_writeback_series(self, table: str, keep_series: list[str]) -> None:
        """Writeback > Settings: make sure the destination is selected on the Destinations tab and allow only `keep_series` on the Data tab."""
        self.tab("Writeback")
        vf = self.vf
        if not vf.get_by_text("Writeback Settings", exact=True).count():
            self.click_ribbon("Settings")
            self.wait(2500)
            vf = self.vf
        vf.get_by_role("tab", name="Destinations").click(timeout=8000)
        self.wait(1500)
        # A newly added destination is already ticked; the checkbox is not named after the table, so find it through the row that shows the table name.
        box = vf.locator(f"xpath=//*[normalize-space(text())='{table}']/ancestor::*[.//input[@type='checkbox']][1]//input[@type='checkbox']").first
        if box.count() and not box.is_checked():
            box.check(force=True, timeout=8000)
            self.wait(2500)
        vf.get_by_role("tab", name="Data").click(timeout=8000)
        self.wait(1500)
        labels = vf.locator("label.bf-ui-form-checkbox")
        inputs = vf.locator("input[type=checkbox]")
        wanted = {s.casefold() for s in keep_series}
        for index in range(1, labels.count()):
            text = labels.nth(index).evaluate("e => (e.parentElement ? e.parentElement.innerText : e.innerText) || ''").casefold()
            checked = inputs.nth(index).is_checked()
            if checked != any(name in text for name in wanted):
                labels.nth(index).click(timeout=8000)
                self.wait(1000)
        vf.get_by_role("button", name="Close Writeback Settings").click(timeout=8000)
        self.wait(1500)

    def run_writeback(self, *, all_sheets: bool = False) -> list[str]:
        """Writeback > Writeback (current sheet); waits until the ribbon item is enabled again and returns the toasts seen meanwhile.

        Older builds of the ribbon showed a Writeback button with a Writeback / Writeback All dropdown; the current build has one `Writeback` item that
        writes the sheet being shown (Writeback All is then not offered there)."""
        self.tab("Writeback")
        item = self.fr.get_by_role("menuitem", name="Writeback", exact=True)
        if item.count():
            if all_sheets:
                raise NotImplementedError("this ribbon has a single Writeback item for the current sheet; run it on each sheet")
            item.first.click(timeout=8000)
        else:
            self.fr.get_by_role("button", name="Writeback", exact=True).first.click(timeout=8000)
            self.wait(1500)
            self.fr.get_by_role("menuitem", name="Writeback All" if all_sheets else "Writeback", exact=True).first.click(timeout=8000)
        self.wait(3000)
        seen: list[str] = []
        for _ in range(40):
            current = self.toasts()
            seen.extend(t for t in current if t not in seen)
            busy = any("in progress" in t.lower() or "loading" in t.lower() for t in current)
            running = item.count() and item.first.get_attribute("aria-disabled") == "true"
            if not busy and not running:
                break
            self.wait(3000)
        self.wait(2000)
        return seen

    # --- Data pane ------------------------------------------------------------------------------
    def refresh_data_pane(self) -> None:
        self.fr.locator(REFRESH_ICON).first.click(timeout=10000)
        self.wait(10000)

    def search(self, text: str) -> None:
        box = self.fr.get_by_role("textbox", name="Search").last  # the Data pane's box; Explorer and the visual gallery have search boxes of their own
        box.click(timeout=8000)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.type(text, delay=30)
        self.page.keyboard.press("Escape")
        self.wait(1800)

    def clear_search(self) -> None:
        self.fr.get_by_role("textbox", name="Search").last.fill("")
        self.wait(800)

    def drag_field(self, name: str, zone: str, *, position: str | None = None, attempts: int = 3) -> None:
        """Drag the first Data-pane item whose text is exactly `name` into a well. `position` 'top' / 'bottom' drops on the zone's upper / lower edge so the
        field lands first / last (the default drop on the zone's centre lands in the middle of a long list)."""
        last: Exception | None = None
        for _ in range(attempts):
            try:
                self.page.mouse.up()
                self.search(name)
                source = self.fr.locator(FIELD_ITEM).filter(has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")).first
                target = self.fr.locator(DROP_ZONE).nth(ZONES[zone])
                spot = None
                if position:
                    box = target.bounding_box()
                    spot = {"x": box["width"] / 2, "y": 3 if position == "top" else max(box["height"] - 3, 3)}
                source.drag_to(target, target_position=spot, timeout=20000)
                self.wait(2500)
                return
            except Exception as exc:  # noqa: BLE001 - UI timing; retry after releasing the mouse
                last = exc
                self.page.mouse.up()
                self.wait(2500)
        raise RuntimeError(f"Could not drag {name!r} to {zone}") from last

    def well(self) -> str:
        """Text of the Fields pane (Rows / Columns / Values wells)."""
        return self.section('tabpanel "Assign Data"', 1800)

    # --- Intelligence sheets ---------------------------------------------------------------------
    def drag_to_named_well(self, field: str, well: str, *, pick: int = 0) -> None:
        """Intelligence canvas: search the Data pane for `field` and drag it into the visual's well labelled `well`
        (for example 'Actual(s)', 'Comparison 1 (vs Actuals)', 'Trellis Row'). Uses explicit mouse events: `drag_to` is flaky here."""
        fr = self.fr
        self.search(field)  # also closes the 'Recent' suggestions popup that otherwise covers the result list
        self.wait(1500)
        source = fr.locator(FIELD_ITEM).filter(has=fr.get_by_text(field, exact=True)).nth(pick)
        zone = fr.locator(f'xpath=//*[normalize-space(text())="{well}"]/following::div[contains(@class,"react-fields-keeper-mapping-content-input")][1]')
        src_box, dst_box = source.bounding_box(), zone.first.bounding_box()
        self.page.mouse.move(src_box["x"] + src_box["width"] / 2, src_box["y"] + src_box["height"] / 2)
        self.page.mouse.down()
        self.wait(300)
        self.page.mouse.move(src_box["x"] + 8, src_box["y"] + 8, steps=4)
        self.wait(300)
        self.page.mouse.move(dst_box["x"] + dst_box["width"] / 2, dst_box["y"] + dst_box["height"] / 2, steps=25)
        self.wait(600)
        self.page.mouse.up()
        self.wait(4000)

    def refresh_report_data(self, wait_ms: int = 25000) -> None:
        """Intelligence ribbon > Refresh Data (the visuals do not query until this is pressed after their wells change)."""
        self.fr.get_by_role("menuitem", name="Update all the visuals with the latest semantic model data.").first.click(timeout=8000, force=True)
        self.wait(wait_ms)

    def dismiss_error_dialog(self) -> bool:
        """Close a 'Something went wrong' modal. It is rendered by the top-level Fabric shell, hides the app behind a dark backdrop and
        blocks every click until closed, so look for its 'Get help' button in every frame."""
        for frame in self.page.frames:
            try:
                if frame.get_by_role("button", name="Get help").count():
                    frame.get_by_role("button", name="Close", exact=True).last.click(timeout=5000, force=True)
                    self.wait(1000)
                    return True
            except Exception:  # noqa: BLE001 - a detached frame is simply skipped
                continue
        return False

    def visuals(self) -> list[dict]:
        """Intelligence visuals on the canvas: [{'id', 'type', 'text'}] in creation order (each lives in an `analytics-plus-visual` frame)."""
        found = []
        for frame in self.page.frames:
            if "analytics-plus-visual" in frame.url and "visualId=" in frame.url:
                try:
                    found.append({
                        "id": int(frame.url.split("visualId=")[1].split("&")[0]),
                        "type": frame.url.split("chartType=")[1].split("&")[0] if "chartType=" in frame.url else "?",
                        "text": frame.locator("body").inner_text().replace("\n", " | "),
                    })
                except Exception:  # noqa: BLE001 - the frame can detach while the canvas re-renders
                    continue
        return sorted(found, key=lambda v: v["id"])

    def build_visual(self, gallery_button: str, variant: str | None = None, *, category: str | None = None, actual: str,
                     comparisons: tuple[str, ...] = (), save: bool = True) -> int:
        """Add an Intelligence visual and fill its wells: `category` (when the visual has one), `actual` and up to three `comparisons`
        (Comparison 1..3 vs Actuals). Saves afterwards by default: an unsaved Intelligence draft is lost when the page reloads or the browser closes."""
        visual_id = self.add_visual(gallery_button, variant)
        if category:
            self.drag_to_named_well(category, "Category")
        self.drag_to_named_well(actual, "Actual(s)")
        for index, measure in enumerate(comparisons, start=1):
            self.drag_to_named_well(measure, f"Comparison {index} (vs Actuals)")
        if save:
            self.dismiss_error_dialog()
            self.save()
        return visual_id

    def set_layout(self, x: float, y: float, width: float, height: float) -> None:
        """Set Dimension & Position (percent of the page) of the selected Intelligence visual. The Layout pane (ribbon > Layout) must be open.

        Size is set before position so the visual is never momentarily larger than the room left to the page edge."""
        values = {"Width": width, "Height": height, "X Position": x, "Y Position": y}
        for label, value in values.items():
            box = self.fr.get_by_role("textbox", name=re.compile(rf"^{label} Value is")).first
            box.click(timeout=8000)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.type(f"{value:g}", delay=30)
            self.page.keyboard.press("Enter")
            self.wait(700)

    def layout_values(self) -> dict[str, str]:
        """Current Dimension & Position values of the selected visual ({'Width': '30', ...}), for logging and for checking which element is selected."""
        out = {}
        for label in ("Width", "Height", "X Position", "Y Position"):
            box = self.fr.get_by_role("textbox", name=re.compile(rf"^{label} Value is")).first
            out[label] = box.input_value() if box.count() else "?"
        return out

    def layout_box(self) -> tuple[int | None, int | None, int | None, int | None]:
        """(x, y, width, height) in whole percent of the selected visual, as shown in the open Layout pane (the app rounds to whole percent)."""
        values = self.layout_values()

        def whole(label: str) -> int | None:
            try:
                return int(float(values[label]))
            except (KeyError, ValueError):
                return None

        return whole("X Position"), whole("Y Position"), whole("Width"), whole("Height")

    def select_visual(self, visual_id: int | None = None, *, super_filter: bool = False, at: tuple[float, float] | None = None,
                      blockers: tuple[dict, ...] = (), expect: tuple[int, int, int, int] | None = None) -> None:
        """Select an Intelligence visual by clicking inside its frame (the top-most element at that point is selected).

        Analytics+ visuals live in an iframe each, so their frame box is the click target. Visuals created later are stacked above earlier ones, so unless
        `at` (a relative point inside the frame) is given, the click points are the points of a 5 x 5 grid inside the frame, those not covered by a later
        visual or by one of `blockers` (screen rectangles such as the Super Filter, which is drawn in the report frame instead of an iframe) first. The Super
        Filter itself is selected with `super_filter=True`: it stacks on the default tile at the page origin and a point on that tile's empty right part selects it.

        A click selects whatever is top-most at that point, which is not always the wanted visual (a moved visual leaves its old frame behind). With `expect`
        = the (x, y, width, height) the wanted visual currently has, shown in the open Layout pane, every candidate point is tried until the pane shows that
        box; a RuntimeError is raised when none does, so a layout edit never lands on the wrong visual."""
        boxes = {}
        for frame in self.page.frames:
            if "analytics-plus-visual" in frame.url and "visualId=" in frame.url:
                try:
                    boxes[int(frame.url.split("visualId=")[1].split("&")[0])] = frame.frame_element().bounding_box()
                except Exception:  # noqa: BLE001 - detached while re-rendering
                    continue
        if super_filter:
            tile = boxes[min(boxes)]
            self.page.mouse.click(tile["x"] + tile["width"] * 0.85, tile["y"] + tile["height"] * 0.5)
            self.wait(1500)
            return
        box = boxes[visual_id]
        if at is not None:
            points = [(box["x"] + box["width"] * at[0], box["y"] + box["height"] * at[1])]
        else:
            covers = [b for vid, b in boxes.items() if vid > visual_id] + list(blockers)

            def covered(px: float, py: float) -> bool:
                return any(c["x"] <= px <= c["x"] + c["width"] and c["y"] <= py <= c["y"] + c["height"] for c in covers)

            grid = [(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy) for fy in (0.5, 0.3, 0.7, 0.15, 0.85) for fx in (0.5, 0.3, 0.7, 0.15, 0.85)]
            points = [p for p in grid if not covered(*p)] + ([p for p in grid if covered(*p)] if expect else [])
            if not points:
                raise RuntimeError(f"Every point of visual {visual_id} is covered by another visual; move the visuals above it first")
        for point in points:
            self.page.mouse.click(*point)
            self.wait(1500)
            if expect is None or self.layout_box() == tuple(expect):
                return
        raise RuntimeError(f"Visual {visual_id} could not be selected: no click point showed the expected layout {expect}")

    def open_layout_pane(self) -> None:
        """Intelligence ribbon > Layout (shown while the visual gallery is displayed); the pane then follows whichever visual is selected."""
        self.tab("Intelligence")
        item = self.fr.get_by_role("menuitem", name="Manage common properties for all elements, including background, dimensions, and other layout settings.")
        if not item.count():
            self.deselect_visual()
            self.tab("Intelligence")
        item.first.click(timeout=8000, force=True)
        self.wait(2000)

    def deselect_visual(self) -> None:
        """Click an empty point so the Visualizations pane shows the gallery instead of the selected visual's wells.

        The point is computed, not fixed: the browser window size differs between sessions. It lies just below the lowest visual (the page background
        below the canvas also deselects), at the left edge of the canvas, and is moved right if a visual happens to be there."""
        width, height = self.page.evaluate("[window.innerWidth, window.innerHeight]")
        boxes = []
        for frame in self.page.frames:
            if "analytics-plus-visual" in frame.url:
                try:
                    box = frame.frame_element().bounding_box()
                except Exception:  # noqa: BLE001 - detached while re-rendering
                    continue
                if box:
                    boxes.append(box)
        left = min((b["x"] for b in boxes), default=width * 0.25)
        bottom = max((b["y"] + b["height"] for b in boxes), default=height * 0.3)
        y = min(bottom + 40, height - 110)
        x = left + 30
        for _ in range(12):
            if not any(b["x"] - 12 <= x <= b["x"] + b["width"] + 12 and b["y"] - 12 <= y <= b["y"] + b["height"] + 12 for b in boxes):
                break
            x += 80
        self.page.mouse.click(x, y)
        self.wait(1200)

    CHART_GROUPS = ("Bar/ Column", "Line", "Area", "Combo", "Waterfall")

    def _new_visual_id(self, before: set[int], timeout: float) -> int | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            new = {v["id"] for v in self.visuals()} - before
            if new:
                self.wait(1500)
                return max(new)
            self.wait(1000)
        return None

    def add_visual(self, gallery_button: str, variant: str | None = None) -> int:
        """Add a visual from the Intelligence gallery and return its visual id (the new visual stays selected so its wells can be filled).

        `gallery_button` is a top-level gallery entry ('KPIs', 'Super Filter', 'Charts 100+', ...); for charts `variant` names the chart type inside the group
        opened by the button (for example 'Overlapped column'). The gallery is only shown when nothing is selected."""
        before = {v["id"] for v in self.visuals()}
        self.deselect_visual()
        button = self.fr.get_by_role("button", name=gallery_button)
        if not button.count():  # the pane is still inside a gallery group from an earlier attempt: go back to the top-level gallery
            back = self.fr.get_by_role("button", name="Back to all visuals")
            if back.count():
                back.first.click(timeout=8000)
                self.wait(1500)
        button.first.click(timeout=8000)
        self.wait(2000)
        if not variant:
            created = self._new_visual_id(before, 40)
        else:
            variants = self.fr.get_by_role("button", name=variant, exact=True)
            if not variants.count():  # the chart groups are collapsed lists: open the one that holds the variant
                for name in self.CHART_GROUPS:
                    opener = self.fr.get_by_text(name, exact=True)
                    if opener.count():
                        opener.first.click(timeout=5000)
                        self.wait(1200)
                        if variants.count():
                            break
            created = None
            for _ in range(2):
                # A group header and its first variant can share a name ('Line'): the first click then only expands the group, the second adds the chart.
                # The variant is the later of the two buttons in the page.
                variants.last.click(timeout=8000)
                created = self._new_visual_id(before, 12)
                if created is not None:
                    break
        if created is None:
            raise RuntimeError(f"No new visual appeared after choosing {gallery_button!r} / {variant!r}")
        return created

    def kpi_text(self) -> str:
        """Text rendered by the first KPI visual on the canvas."""
        frame = next((f for f in self.page.frames if "analytics-plus-visual" in f.url), None)
        return frame.locator("body").inner_text().replace("\n", " | ") if frame else ""

    # --- Infobridge -----------------------------------------------------------------------------
    def add_planning_query(self, source_sheet: str, measure: str) -> None:
        """Data > Queries > Add > Planning Sheet: query one measure of another planning sheet, wait for Infobridge to load, close it.

        The sheets must have identical row and column dimensions (documented requirement for sharing a measure)."""
        fr = self.fr
        expand = fr.get_by_role("button", name="Expand Queries")
        if expand.count():
            expand.first.click(timeout=8000)
            self.wait(1500)
        fr.get_by_role("button", name="Add", exact=True).last.click(timeout=8000)
        self.wait(1500)
        fr.get_by_role("menuitem", name="Planning Sheet", exact=True).first.click(timeout=8000)
        self.wait(3000)
        dialog = fr.get_by_role("dialog").first
        dialog.get_by_role("combobox").first.click(timeout=8000)
        self.wait(1200)
        fr.get_by_role("option", name=source_sheet).first.click(timeout=8000)
        self.wait(3000)
        dialog.get_by_text("All", exact=True).first.click(timeout=8000)  # clear every measure, then tick the wanted one
        self.wait(600)
        dialog.get_by_text(measure, exact=True).first.click(timeout=8000)
        self.wait(600)
        dialog.get_by_role("button", name="Add", exact=True).click(timeout=8000)
        deadline = time.time() + 90
        while time.time() < deadline and not fr.get_by_text("Query Details").count():
            self.wait(1000)
        self.wait(12000)  # the query preview loads asynchronously
        fr.get_by_text("Close", exact=True).first.click(timeout=8000)
        self.wait(5000)

    def drag_query_measure(self, name: str) -> None:
        """Drag a measure listed under Queries into Values once (retrying a drag that already worked adds a duplicate well)."""
        fr = self.fr
        box = fr.get_by_role("textbox", name="Search").first
        box.click(timeout=8000)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Delete")
        self.wait(1200)
        source = fr.locator(FIELD_ITEM).filter(has=fr.get_by_text(name, exact=True)).first
        source.drag_to(fr.locator(DROP_ZONE).nth(ZONES["values"]), timeout=20000)
        self.wait(6000)

    def remove_field(self, name: str) -> None:
        self.fr.get_by_role("button", name=f"Remove {name} Field").first.click(timeout=10000)
        self.wait(2000)
