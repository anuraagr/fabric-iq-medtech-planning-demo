"""Live tests need a real tenant: skip them unless FPD_LIVE=1 (set it only after `az login` with the isolated profile)."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("FPD_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live tenant checks need FPD_LIVE=1 and an Azure CLI sign-in (see tests/live/test_live_acceptance.py)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
