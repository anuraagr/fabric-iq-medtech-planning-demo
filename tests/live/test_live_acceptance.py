"""Live integration checks: the read-only acceptance runners against the real tenant, as pytest tests.

Skipped unless FPD_LIVE=1 (it also needs an Azure CLI sign-in; see README). Nothing here writes to the tenant:

    $env:FPD_LIVE = "1"; $env:AZURE_CONFIG_DIR = "$HOME\\.azure-fabricplanningdemo"
    python -m pytest tests/live -q

A runner that cannot reach the tenant (capacity throttled, login expired) raises, so the test errors instead of reporting a pass.
"""

from __future__ import annotations

import pytest

from scripts import validate
from scripts.lib.context import Context

pytestmark = pytest.mark.live

# Checks that only read: tenant and workspace, models, T-SQL, plan definition, scenario and reporting reconciliation, reset dry run
READ_ONLY_CHECKS = ["T-01", "T-02", "T-03", "T-04", "T-05", "T-06", "T-08", "T-10", "T-12", "T-15", "T-16"]


@pytest.fixture(scope="module")
def ctx() -> Context:
    return Context()


@pytest.mark.parametrize("check_id", READ_ONLY_CHECKS)
def test_live_acceptance_check_passes(ctx: Context, check_id: str):
    status, observed = validate.RUNNERS[check_id](ctx)
    assert status == "passed", f"{check_id} is {status}: {observed}"


def test_the_role_probe_reports_but_never_passes_the_multi_user_check(ctx: Context):
    status, observed = validate.RUNNERS["T-11"](ctx)
    assert status == "not_executed" and "probe for" in observed
