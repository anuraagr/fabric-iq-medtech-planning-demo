"""Permission probe (T-11 support): classification, SQL probe generation and the 'never passed without other identities' guarantee."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import permissions as cli
from scripts import validate
from scripts.lib import permissions as perm
from scripts.lib.http import ApiError
from scripts.lib.sqlclient import assert_read_only


class OdbcError(Exception):
    pass


def test_only_explicit_denials_are_classified_as_denied():
    assert perm.classify_error(ApiError(403, "Forbidden", "no")) == (perm.DENIED, "HTTP 403")
    assert perm.classify_error(ApiError(401, "Unauthorized", "no"))[0] == perm.DENIED
    assert perm.classify_error(OdbcError("28000", "Login failed for user"))[0] == perm.DENIED
    assert perm.classify_error(OdbcError("42000", "The SELECT permission was denied"))[0] == perm.DENIED
    for transient in (ApiError(500, "Internal", "boom"), OdbcError("08001", "login timeout"), RuntimeError("x")):
        assert perm.classify_error(transient)[0] == perm.UNKNOWN, transient


def test_probe_reports_a_failure_in_the_matrix_instead_of_raising():
    assert perm.probe("Plan item", "read", lambda: "plan-1") == perm.LayerResult("Plan item", "read", perm.ALLOWED, "plan-1")
    denied = perm.probe("Plan item", "read", lambda: (_ for _ in ()).throw(ApiError(403, "Forbidden", "no")))
    assert (denied.outcome, denied.detail) == (perm.DENIED, "HTTP 403")
    assert perm.probe("Workspace", "x", lambda: None).detail == ""


def test_sql_probe_query_is_a_single_read_only_select_even_though_it_names_write_verbs():
    sql = perm.sql_probe_query(["dbo.medtech_revenue_budget_fy2026", "dbo.assets"])
    assert_read_only(sql)  # 'INSERT' etc. appear only inside string literals
    assert sql.count("HAS_PERMS_BY_NAME(") == 1 + 2 * len(perm.SQL_VERBS)  # database CONTROL + each verb on each of the two tables
    assert "AS t1_delete" in sql and "IS_ROLEMEMBER('db_owner')" in sql


def test_sql_probe_query_refuses_anything_but_plain_schema_table_names():
    for bad in ("assets", "dbo.assets; DROP TABLE x", "dbo.a'b", "dbo.[a]"):
        with pytest.raises(ValueError):
            perm.sql_probe_query([bad])


def test_sql_row_is_interpreted_per_verb_and_unknown_objects_are_not_called_denied():
    row = {"db_user": "alice@contoso", "db_owner": 0, "db_control": 0, "t0_select": 1, "t0_insert": 0, "t0_update": 0, "t0_delete": None}
    results = perm.interpret_sql_row("SQL", row, ["dbo.t"])
    by_check = {r.check: r.outcome for r in results}
    assert by_check["SELECT on dbo.t"] == perm.ALLOWED and by_check["INSERT on dbo.t"] == perm.DENIED
    assert by_check["DELETE on dbo.t"] == perm.UNKNOWN  # NULL: the object is not visible, which is not the same as a denied grant
    assert by_check["database user"] == perm.ALLOWED and by_check["db_owner / CONTROL"] == perm.DENIED


def test_the_summary_never_claims_the_role_matrix_was_executed():
    results = [perm.LayerResult("Workspace", "role assignment", perm.ALLOWED, "Admin"), perm.LayerResult("SQL", "DELETE on dbo.t", perm.DENIED), perm.LayerResult("SQL", "x", perm.UNKNOWN),
               *perm.manual_layers()]
    text = perm.summarize_author(results, "admin@contoso")
    assert "1/3 layer checks allowed" in text and "denied: ['DELETE on dbo.t']" in text and "not readable: ['x']" in text
    assert "NOT executed" in text and "manual" in text


class FakeSession:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def query(self, sql, params=()):
        assert_read_only(sql)
        return [self.row]


def fake_context(upn="admin@contoso.com", role="Admin", plan_error=None, db_row=None):
    def get_plan(wid, plan_id):
        if plan_error:
            raise plan_error
        return {"displayName": "Plan"}

    record = SimpleNamespace(id="x")
    row = db_row or {"db_user": upn, "db_owner": 1, "db_control": 1, **{f"t{i}_{v.lower()}": 1 for i in range(4) for v in perm.SQL_VERBS}}
    return SimpleNamespace(
        cfg=SimpleNamespace(
            names=SimpleNamespace(medtech_plan="Plan", medtech_model="Model", writeback_db="WB", powertable_db="PT"),
            raw={"medtech": {"writeback_tables": [{"source_table": "bud"}], "scenario": {"table": "dbo.scen"}}},
        ),
        workspace_id=lambda: "ws",
        manifest=SimpleNamespace(find=lambda kind, name, workspace_id=None: record),
        clients=SimpleNamespace(
            tokens=SimpleNamespace(identity=lambda: {"upn": upn, "tenantId": "t", "objectId": "oid", "amr": "pwd,mfa"}),
            fabric=SimpleNamespace(get_workspace=lambda wid: {"capacityId": "cap"}, my_workspace_role=lambda wid, oid: role, get_plan=get_plan),
            powerbi=SimpleNamespace(execute_queries=lambda wid, mid, dax: [{"[v]": 1}]),
        ),
        sql_session=lambda name: FakeSession(row),
    )


def test_collect_covers_every_layer_and_includes_the_scenario_table():
    upn, results = cli.collect(fake_context())
    layers = {r.layer for r in results}
    assert upn == "admin@contoso.com"
    assert {"Microsoft Entra", "Workspace", "Plan item", "Semantic model", "SQL writeback database", "SQL PowerTable database", "Native Planning role", "PowerTable security"} <= layers
    checks = {r.check for r in results}
    assert "SELECT on dbo.bud" in checks and "INSERT on dbo.scen" in checks and "DELETE on dbo.assets" in checks


def test_a_guest_identity_and_a_forbidden_plan_item_show_up_as_denied():
    _, results = cli.collect(fake_context(upn="x_gmail.com#EXT#@contoso.onmicrosoft.com", plan_error=ApiError(403, "Forbidden", "no")))
    outcomes = {(r.layer, r.check): r.outcome for r in results}
    assert outcomes[("Microsoft Entra", "tenant member (not a B2B guest)")] == perm.DENIED
    assert outcomes[("Plan item", "read plan item")] == perm.DENIED


def test_a_sql_login_failure_becomes_a_single_denied_row_for_that_layer():
    ctx = fake_context()

    def refuse(name):
        raise OdbcError("28000", "Login failed")

    ctx.sql_session = refuse
    _, results = cli.collect(ctx)
    sql = [r for r in results if r.layer == "SQL writeback database"]
    assert len(sql) == 1 and sql[0].outcome == perm.DENIED


def test_t11_attaches_the_author_probe_but_can_never_be_passed():
    status, observed = validate.t11(fake_context())
    assert status == "not_executed"
    assert "probe for admin@contoso.com" in observed and "role matrix is NOT executed" in observed
