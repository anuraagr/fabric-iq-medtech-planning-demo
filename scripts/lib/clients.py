"""Factory that wires configuration, Azure CLI token provider and the API clients together."""

from __future__ import annotations

from dataclasses import dataclass

from .auth import AzCliTokenProvider
from .config import DemoConfig
from .fabric import FABRIC_BASE, FabricClient
from .http import ApiClient
from .powerbi import POWERBI_BASE, PowerBiClient


@dataclass
class Clients:
    config: DemoConfig
    tokens: AzCliTokenProvider
    fabric: FabricClient
    powerbi: PowerBiClient


def build_clients(config: DemoConfig) -> Clients:
    tokens = AzCliTokenProvider(config.tenant_id, az_config_dir=config.az_config_dir)
    fabric_api = ApiClient(FABRIC_BASE, tokens.bearer("fabric"))
    powerbi_api = ApiClient(POWERBI_BASE, tokens.bearer("powerbi"), timeout=600.0)
    return Clients(config=config, tokens=tokens, fabric=FabricClient(fabric_api), powerbi=PowerBiClient(powerbi_api))
