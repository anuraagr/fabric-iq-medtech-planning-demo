"""Interactive Microsoft Entra authentication through the Azure CLI. Tokens are held in memory only."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable

RESOURCES = {
    "fabric": "https://api.fabric.microsoft.com",
    "powerbi": "https://analysis.windows.net/powerbi/api",
    "sql": "https://database.windows.net/",
}


class AuthError(RuntimeError):
    """Raised when a token cannot be acquired non-interactively."""


@dataclass(frozen=True)
class AccessToken:
    value: str = field(repr=False)
    expires_on: float
    resource: str
    tenant_id: str

    def is_fresh(self, now: float | None = None, skew: float = 300.0) -> bool:
        return (now if now is not None else time.time()) < self.expires_on - skew


def decode_claims(jwt: str) -> dict:
    """Decode (without verifying) the payload of a JWT, for display of tid/upn/oid/exp only."""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except (IndexError, ValueError) as exc:
        raise AuthError("Access token is not a decodable JWT") from exc


class AzCliTokenProvider:
    """Wraps `az account get-access-token`; honours AZURE_CONFIG_DIR so a dedicated profile can be used."""

    def __init__(
        self,
        tenant_id: str,
        *,
        az_config_dir: str | None = None,
        az_path: str | None = None,
        timeout: float = 90.0,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.tenant_id = tenant_id
        self.az_config_dir = az_config_dir
        self.az_path = az_path or shutil.which("az")
        self.timeout = timeout
        self._runner = runner
        self._clock = clock
        self._cache: dict[str, AccessToken] = {}

    def _login_hint(self) -> str:
        profile = f"$env:AZURE_CONFIG_DIR='{self.az_config_dir}'; " if self.az_config_dir else ""
        return f"Run: {profile}az login --tenant {self.tenant_id} --use-device-code --allow-no-subscriptions"

    def get(self, resource: str) -> AccessToken:
        resource_url = RESOURCES.get(resource, resource)
        cached = self._cache.get(resource_url)
        if cached and cached.is_fresh(self._clock()):
            return cached
        if not self.az_path:
            raise AuthError("Azure CLI (az) was not found on PATH. Install it from https://aka.ms/installazurecliwindows")
        env = dict(os.environ)
        if self.az_config_dir:
            env["AZURE_CONFIG_DIR"] = self.az_config_dir
        command = [self.az_path, "account", "get-access-token", "--tenant", self.tenant_id, "--resource", resource_url, "--output", "json"]
        try:
            completed = self._runner(command, capture_output=True, text=True, timeout=self.timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            raise AuthError(f"az timed out; interactive sign-in is probably required. {self._login_hint()}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else "unknown error"
            raise AuthError(f"az could not issue a token for {resource_url}: {tail}. {self._login_hint()}")
        try:
            payload = json.loads(completed.stdout)
            value = payload["accessToken"]
        except (ValueError, KeyError) as exc:
            raise AuthError("Unexpected output from az account get-access-token") from exc
        claims = decode_claims(value)
        token_tenant = str(claims.get("tid", ""))
        if token_tenant.lower() != self.tenant_id.lower():
            raise AuthError(f"Token tenant {token_tenant} does not match the configured tenant {self.tenant_id}")
        token = AccessToken(value=value, expires_on=float(claims.get("exp", self._clock() + 3000)), resource=resource_url, tenant_id=token_tenant)
        self._cache[resource_url] = token
        return token

    def bearer(self, resource: str) -> Callable[[], str]:
        return lambda: self.get(resource).value

    def identity(self) -> dict[str, str]:
        """Non-secret identity facts from the Fabric token claims."""
        claims = decode_claims(self.get("fabric").value)
        return {
            "tenantId": str(claims.get("tid", "")),
            "upn": str(claims.get("upn") or claims.get("unique_name") or claims.get("preferred_username") or ""),
            "objectId": str(claims.get("oid", "")),
            "amr": ",".join(claims.get("amr", [])),
        }
