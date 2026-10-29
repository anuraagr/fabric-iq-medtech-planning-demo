"""Attach Playwright to a dedicated Microsoft Edge window (optional native-UI automation helper).

Why: Fabric Planning renders inside a cross-origin iframe (pbifpw.powerbi.com), which simple page
automation cannot reach; Playwright can drive such frames. This helper never reads, stores or logs
authentication token values: the human signs in once in a normal Edge window that uses a dedicated profile
stored OUTSIDE the repository (%LOCALAPPDATA%\\FabricPlanningDemo\\edge-profile). The only token-related
helper, `drop_cached_graph_token`, deletes one stale cache entry in the browser by its key name.

    python -m scripts.ui.session launch      # start Edge with a local-only debug port (127.0.0.1)
    python -m scripts.ui.session status      # list pages open in that Edge window

The debug port is reachable only from this machine; close the Edge window when finished.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PORT = int(os.environ.get("FPD_EDGE_PORT", "9333"))
CDP_URL = f"http://127.0.0.1:{PORT}"
PROFILE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FabricPlanningDemo" / "edge-profile"
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
FABRIC_HOME = "https://app.fabric.microsoft.com/"


def debug_port_open() -> bool:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2):
            return True
    except OSError:
        return False


def launch_edge(url: str = FABRIC_HOME) -> None:
    if debug_port_open():
        print(f"Edge debug port {PORT} is already open")
        return
    edge = next((p for p in EDGE_CANDIDATES if Path(p).exists()), None)
    if not edge:
        raise SystemExit("Microsoft Edge was not found")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [edge, f"--remote-debugging-port={PORT}", "--remote-debugging-address=127.0.0.1", f"--user-data-dir={PROFILE_DIR}",
         "--no-first-run", "--no-default-browser-check", "--window-size=1680,1050", url],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    for _ in range(40):
        if debug_port_open():
            print(f"Edge started with debug port {PORT}; profile {PROFILE_DIR}")
            return
        time.sleep(0.5)
    raise SystemExit("Edge did not open its debug port in time")


@contextmanager
def attached() -> Iterator[tuple]:
    """Yield (browser, context) attached over CDP. Closing only disconnects; Edge keeps running."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_URL)
        try:
            yield browser, browser.contexts[0]
        finally:
            browser.close()


def find_page(context, url_part: str):
    for page in context.pages:
        if url_part in page.url:
            return page
    return None


# The Fabric portal tab caches its MSAL tokens in sessionStorage under keys
# "msal.2|<account>|<authority>|accesstoken|<client>|<tenant>|<scopes>|<claims>|<type>" (plus an index per client).
_DROP_GRAPH_TOKEN_JS = """() => {
  const isGraphToken = (key) => { const p = key.split('|'); return p[0] === 'msal.2' && p[3] === 'accesstoken' && (p[6] || '').includes('graph.microsoft.com'); };
  const dropped = Object.keys(sessionStorage).filter(isGraphToken);
  dropped.forEach((key) => sessionStorage.removeItem(key));
  for (const indexKey of Object.keys(sessionStorage).filter((key) => key.startsWith('msal.2.token.keys.'))) {
    const index = JSON.parse(sessionStorage.getItem(indexKey) || '{}');
    index.accessToken = (index.accessToken || []).filter((key) => !isGraphToken(key));
    sessionStorage.setItem(indexKey, JSON.stringify(index));
  }
  return dropped.length;
}"""


def drop_cached_graph_token(page) -> int:
    """Delete the Microsoft Graph access token the Fabric portal tab has cached; returns how many entries were removed (0 or 1).

    Why: the portal reuses a cached Graph token for up to 24 h. When the tenant's Conditional Access policies change in the meantime, Graph answers
    that token with 401 `TokenCreatedWithOutdatedPolicies` (continuous access evaluation) and the Planning app never learns the current user, so it
    throws `Cannot read properties of undefined (reading 'id')` as soon as it renders in Editing mode and the plan area turns white. After the entry
    is gone the next page load gets a fresh token silently (a new sign-in is not needed). Reload the plan afterwards. Only a key name is inspected."""
    return int(page.evaluate(_DROP_GRAPH_TOKEN_JS))


def planning_frame(page, timeout: float = 10.0):
    """The Planning app frame: pbifpw.powerbi.com/<plan id>/... (worker frames carry __iframeType and are skipped).

    Frames attach asynchronously after a CDP connect, so poll briefly before giving up."""
    deadline = time.time() + timeout
    while True:
        for frame in page.frames:
            if frame.url.startswith("https://pbifpw.powerbi.com/") and "__iframeType" not in frame.url:
                return frame
        if time.time() >= deadline:
            return None
        page.wait_for_timeout(300)


def outline(frame, limit: int = 8000) -> str:
    text = frame.locator("body").aria_snapshot()
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more chars]"


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "status"
    if command == "launch":
        launch_edge(argv[1] if len(argv) > 1 else FABRIC_HOME)
        return 0
    if command == "status":
        if not debug_port_open():
            print("Edge debug port is closed. Run: python -m scripts.ui.session launch")
            return 1
        with attached() as (_, context):
            for i, page in enumerate(context.pages):
                print(f"[{i}] {page.title()[:60]!r} {page.url[:140]}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
