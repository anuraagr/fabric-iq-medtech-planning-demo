"""Shared fakes for unit tests: no network, no Azure CLI, no ODBC."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest


class FakeResponse:
    def __init__(self, status: int = 200, json_body: Any = None, headers: dict | None = None, text: str | None = None, url: str = "https://example.test/x") -> None:
        self.status_code = status
        self._json = json_body
        self.headers = headers or {}
        self.url = url
        if text is not None:
            self.text = text
        elif json_body is not None:
            self.text = json.dumps(json_body)
        else:
            self.text = ""
        self.content = self.text.encode("utf-8")

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeSession:
    """Scripted requests.Session replacement; items in `responses` may be FakeResponse or Exception."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"Unexpected extra request: {method} {url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        item.url = url
        return item


def make_jwt(claims: dict) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none', 'typ': 'JWT'})}.{enc(claims)}.signature"


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def no_sleep(sleeps: list[float]):
    return sleeps.append
