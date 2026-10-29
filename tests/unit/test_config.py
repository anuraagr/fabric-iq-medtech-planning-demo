from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scripts.lib.config import ConfigError, REPO_ROOT, load_config, load_sample

TENANT = "11111111-1111-4111-8111-111111111111"


def test_manifest_environment_override_supports_isolated_deployment_state(tmp_path):
    path = tmp_path / "other-state" / "manifest.json"
    cfg = load_config(environ={"FPD_TENANT_ID": TENANT, "FPD_MANIFEST_PATH": str(path)}, env_file=tmp_path / "no.env")
    assert cfg.manifest_path == path
NO_ENV_FILE = Path("does-not-exist.env")


def test_defaults_come_from_example_yaml_and_env():
    cfg = load_config(environ={"FPD_TENANT_ID": TENANT}, env_file=NO_ENV_FILE)
    assert cfg.tenant_id == TENANT
    assert cfg.resource_prefix == "FabricPlanningDemo"
    assert cfg.names.workspace == "FabricPlanningDemo"
    assert cfg.names.writeback_db == "FabricPlanningDemo_Writeback"
    assert cfg.names.powertable_db == "FabricPlanningDemo_PowerTable"
    assert cfg.names.plan_item == "FabricPlanningDemo_Plan"
    assert cfg.names.semantic_model == "FabricPlanningDemo_Northwind_FMCG"
    assert cfg.workspace_id is None and cfg.capacity_id is None and cfg.live is False
    assert cfg.manifest_path == REPO_ROOT / "state" / "deployment-manifest.json"


def test_environment_overrides_and_live_flag():
    cfg = load_config(
        environ={
            "FPD_TENANT_ID": TENANT,
            "FPD_RESOURCE_PREFIX": "DemoX",
            "FPD_WORKSPACE_ID": "4dabd120-bae2-445d-8f6e-5dcd242ebe79",
            "FPD_CAPACITY_ID": "33333333-3333-4333-8333-333333333333",
            "FPD_LIVE": "1",
            "AZURE_CONFIG_DIR": "C:/p",
        },
        env_file=NO_ENV_FILE,
    )
    assert cfg.names.plan_item == "DemoX_Plan"
    assert cfg.workspace_id and cfg.capacity_id and cfg.live is True and cfg.az_config_dir == "C:/p"


def test_dotenv_file_is_read_and_environment_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"FPD_TENANT_ID={TENANT}  # comment\nFPD_RESOURCE_PREFIX=FromFile\n", encoding="utf-8")
    cfg = load_config(environ={"FPD_RESOURCE_PREFIX": "FromEnv"}, env_file=env_file)
    assert cfg.tenant_id == TENANT and cfg.resource_prefix == "FromEnv"


@pytest.mark.parametrize(
    "environ, message",
    [
        ({}, "FPD_TENANT_ID is not set"),
        ({"FPD_TENANT_ID": "00000000-0000-0000-0000-000000000000"}, "FPD_TENANT_ID is not set"),
        ({"FPD_TENANT_ID": "not-a-guid"}, "must be a GUID"),
        ({"FPD_TENANT_ID": TENANT, "FPD_WORKSPACE_ID": "x"}, "FPD_WORKSPACE_ID must be a GUID"),
        ({"FPD_TENANT_ID": TENANT, "FPD_RESOURCE_PREFIX": "1bad prefix"}, "FPD_RESOURCE_PREFIX"),
    ],
)
def test_invalid_configuration_is_rejected(environ, message):
    with pytest.raises(ConfigError, match=message):
        load_config(environ=environ, env_file=NO_ENV_FILE)


def test_secret_like_yaml_keys_are_rejected(tmp_path):
    bad = tmp_path / "c.yaml"
    bad.write_text(textwrap.dedent("""
        resource_prefix: X
        auth:
          client_secret: nope
    """), encoding="utf-8")
    with pytest.raises(ConfigError, match="secret-like"):
        load_config(yaml_path=bad, environ={"FPD_TENANT_ID": TENANT}, env_file=NO_ENV_FILE)


def test_sample_pins_are_complete_and_well_formed():
    sample = load_sample()
    assert len(sample.commit) == 40
    names = {f.name for f in sample.files}
    assert {"Northwind_FMCG.pbix", "Northwind-FMCG-assets-powertable-tutorial.xlsx"} <= names
    for item in sample.files:
        assert len(item.sha256) == 64 and item.bytes > 0
