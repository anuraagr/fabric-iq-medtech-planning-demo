"""The JavaScript behind scripts.ui.session.drop_cached_graph_token, run in Node against a fake sessionStorage (skipped when Node is not installed)."""

import json
import shutil
import subprocess

import pytest

from scripts.ui import session

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

GRAPH = "msal.2|home.tenant|login.windows.net|accesstoken|client-1|tenant-1|email openid profile https://graph.microsoft.com/_d.aa https://graph.microsoft.com/user.read|"
POWERBI = "msal.2|home.tenant|login.windows.net|accesstoken|client-1|tenant-1|https://analysis.windows.net/powerbi/api/user_impersonation|"
ARM = "msal.2|home.tenant|login.windows.net|accesstoken|client-1|tenant-1|https://management.azure.com/user_impersonation|"
GRAPH_ID_TOKEN = "msal.2|home.tenant|login.windows.net|idtoken|client-1|tenant-1|https://graph.microsoft.com/user.read|"  # not an access token
INDEX = "msal.2.token.keys.client-1"
DRIVER = """
class Store {
  getItem(k) { return Object.prototype.hasOwnProperty.call(this, k) ? this[k] : null; }
  setItem(k, v) { this[k] = String(v); }
  removeItem(k) { delete this[k]; }
}
const store = new Store();
const seed = JSON.parse(process.argv[1]);
for (const [k, v] of Object.entries(seed)) store.setItem(k, v);
global.sessionStorage = store;
const dropped = (%s)();
console.log(JSON.stringify({dropped, keys: Object.keys(store), index: JSON.parse(store.getItem('%s') || '{}')}));
"""


def run(seed: dict) -> dict:
    script = DRIVER % (session._DROP_GRAPH_TOKEN_JS, INDEX)
    done = subprocess.run(["node", "-e", script, json.dumps(seed)], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout)


def seed(*access_keys: str) -> dict:
    data = {key: json.dumps({"secret": "not-a-real-token"}) for key in access_keys}
    data[GRAPH_ID_TOKEN] = json.dumps({"secret": "id"})
    data["unrelated"] = "keep"
    data[INDEX] = json.dumps({"idToken": [GRAPH_ID_TOKEN], "accessToken": list(access_keys), "refreshToken": ["rt"]})
    return data


def test_only_the_graph_access_token_and_its_index_entry_are_removed():
    out = run(seed(GRAPH, POWERBI, ARM))
    assert out["dropped"] == 1
    assert GRAPH not in out["keys"] and {POWERBI, ARM, GRAPH_ID_TOKEN, "unrelated", INDEX} <= set(out["keys"])
    assert out["index"]["accessToken"] == [POWERBI, ARM]
    assert out["index"]["idToken"] == [GRAPH_ID_TOKEN] and out["index"]["refreshToken"] == ["rt"]


def test_nothing_changes_when_no_graph_token_is_cached():
    out = run(seed(POWERBI, ARM))
    assert out["dropped"] == 0 and out["index"]["accessToken"] == [POWERBI, ARM]
    assert {POWERBI, ARM, GRAPH_ID_TOKEN, "unrelated"} <= set(out["keys"])


def test_the_helper_inspects_key_names_only_so_it_never_parses_a_cached_value():
    assert "JSON.parse(sessionStorage.getItem(key" not in session._DROP_GRAPH_TOKEN_JS
    broken = {GRAPH: "this is not json", INDEX: json.dumps({"accessToken": [GRAPH]})}
    assert run(broken)["dropped"] == 1
