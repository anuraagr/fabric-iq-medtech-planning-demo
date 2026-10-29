from __future__ import annotations

import json
import subprocess

import pytest

from scripts.lib.auth import AccessToken, AuthError, AzCliTokenProvider, decode_claims
from tests.conftest import make_jwt

TENANT = "11111111-1111-4111-8111-111111111111"


def runner_returning(claims: dict, calls: list):
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps({"accessToken": make_jwt(claims)}), "")

    return run


def test_decode_claims_roundtrip():
    claims = {"tid": TENANT, "upn": "a@b.c", "exp": 2_000_000_000}
    assert decode_claims(make_jwt(claims))["upn"] == "a@b.c"


def test_decode_rejects_garbage():
    with pytest.raises(AuthError):
        decode_claims("not-a-jwt")


def test_token_is_cached_and_passes_tenant_resource_and_profile():
    calls: list = []
    claims = {"tid": TENANT, "exp": 2_000_000_000, "upn": "admin@t.onmicrosoft.com", "oid": "o1"}
    provider = AzCliTokenProvider(TENANT, az_config_dir="C:/profile", az_path="az", runner=runner_returning(claims, calls), clock=lambda: 1_000.0)
    first = provider.get("fabric")
    second = provider.get("fabric")
    assert first is second and len(calls) == 1
    command, kwargs = calls[0]
    assert command[:3] == ["az", "account", "get-access-token"]
    assert "--tenant" in command and TENANT in command
    assert "https://api.fabric.microsoft.com" in command
    assert kwargs["env"]["AZURE_CONFIG_DIR"] == "C:/profile"


def test_expired_token_is_refreshed():
    calls: list = []
    now = {"t": 1_000.0}
    claims = {"tid": TENANT, "exp": 1_200}
    provider = AzCliTokenProvider(TENANT, az_path="az", runner=runner_returning(claims, calls), clock=lambda: now["t"])
    provider.get("sql")
    now["t"] = 1_150.0  # inside the 300s skew window
    provider.get("sql")
    assert len(calls) == 2


def test_wrong_tenant_is_rejected():
    provider = AzCliTokenProvider(TENANT, az_path="az", runner=runner_returning({"tid": "other", "exp": 9_999_999_999}, []))
    with pytest.raises(AuthError, match="does not match"):
        provider.get("fabric")


def test_timeout_gives_actionable_login_hint():
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    provider = AzCliTokenProvider(TENANT, az_config_dir="C:/p", az_path="az", runner=run)
    with pytest.raises(AuthError, match="az login --tenant"):
        provider.get("fabric")


def test_nonzero_exit_reports_last_error_line():
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "line1\nAADSTS70043: refresh token expired")

    provider = AzCliTokenProvider(TENANT, az_path="az", runner=run)
    with pytest.raises(AuthError, match="AADSTS70043"):
        provider.get("powerbi")


def test_missing_az_is_reported():
    provider = AzCliTokenProvider(TENANT, az_path=None)
    provider.az_path = None
    with pytest.raises(AuthError, match="Azure CLI"):
        provider.get("fabric")


def test_token_repr_does_not_leak_value():
    token = AccessToken(value="SECRET-VALUE", expires_on=1.0, resource="r", tenant_id="t")
    assert "SECRET-VALUE" not in repr(token)


def test_identity_returns_non_secret_claims():
    claims = {"tid": TENANT, "exp": 2_000_000_000, "upn": "admin@t.onmicrosoft.com", "oid": "oid-1", "amr": ["pwd", "mfa"]}
    provider = AzCliTokenProvider(TENANT, az_path="az", runner=runner_returning(claims, []), clock=lambda: 1.0)
    identity = provider.identity()
    assert identity == {"tenantId": TENANT, "upn": "admin@t.onmicrosoft.com", "objectId": "oid-1", "amr": "pwd,mfa"}
