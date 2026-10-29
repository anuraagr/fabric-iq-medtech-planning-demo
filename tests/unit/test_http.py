from __future__ import annotations

import pytest
import requests

from scripts.lib.http import ApiClient, ApiError, is_capacity_rejection
from tests.conftest import FakeResponse, FakeSession


def client(responses, sleeps, **kwargs):
    session = FakeSession(responses)
    api = ApiClient("https://api.example.test/v1", lambda: "TOKEN", session=session, sleep=sleeps.append, **kwargs)
    return api, session


def test_a_capacity_rejection_is_a_400_that_names_the_capacity_and_nothing_else():
    assert is_capacity_rejection(ApiError(400, "DatasetExecuteQueriesError", "Your organization's Fabric compute capacity has exceeded its limits"))
    assert not is_capacity_rejection(ApiError(400, "DatasetExecuteQueriesError", "Failed to resolve the name 'Plan Budget Revenue'"))  # a real model error
    assert not is_capacity_rejection(ApiError(429, "TooManyRequests", "capacity busy"))  # retryable throttling is another path
    assert not is_capacity_rejection(TimeoutError("capacity"))


def test_get_json_builds_url_and_sends_bearer(sleeps):
    api, session = client([FakeResponse(200, {"ok": True})], sleeps)
    assert api.get_json("workspaces") == {"ok": True}
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", "https://api.example.test/v1/workspaces")
    assert kwargs["headers"]["Authorization"] == "Bearer TOKEN"


def test_429_is_retried_honouring_retry_after_even_for_post(sleeps):
    api, session = client([FakeResponse(429, {"error": "slow"}, {"Retry-After": "7"}), FakeResponse(201, {"id": "1"})], sleeps)
    response = api.request("POST", "items", json_body={"a": 1})
    assert response.status_code == 201
    assert sleeps == [7.0]
    assert len(session.calls) == 2


def test_5xx_retried_for_get_but_not_for_post(sleeps):
    api, _ = client([FakeResponse(503, {}), FakeResponse(200, {"v": 1})], sleeps)
    assert api.get_json("x") == {"v": 1}
    assert len(sleeps) == 1

    api2, session2 = client([FakeResponse(503, {"error": {"code": "Busy", "message": "later"}})], [])
    with pytest.raises(ApiError) as excinfo:
        api2.request("POST", "x", json_body={})
    assert excinfo.value.status == 503 and len(session2.calls) == 1


def test_error_body_is_parsed_into_api_error(sleeps):
    api, _ = client([FakeResponse(403, {"errorCode": "InsufficientScopes", "message": "nope"}, {"RequestId": "abc"})], sleeps)
    with pytest.raises(ApiError) as excinfo:
        api.get_json("admin/tenantsettings")
    assert excinfo.value.code == "InsufficientScopes"
    assert excinfo.value.request_id == "abc"
    assert "nope" in str(excinfo.value)


def test_retries_are_bounded(sleeps):
    api, session = client([FakeResponse(429, {})] * 4, sleeps, max_retries=2)
    with pytest.raises(ApiError):
        api.get_json("x")
    assert len(session.calls) == 3


def test_connection_errors_retry_for_get_only(sleeps):
    api, _ = client([requests.ConnectionError("boom"), FakeResponse(200, {"ok": 1})], sleeps)
    assert api.get_json("x") == {"ok": 1}

    api2, _ = client([requests.ConnectionError("boom")], [])
    with pytest.raises(requests.ConnectionError):
        api2.request("POST", "x", json_body={})


def test_paginate_follows_continuation_token(sleeps):
    api, session = client(
        [FakeResponse(200, {"value": [{"id": 1}], "continuationToken": "t1"}), FakeResponse(200, {"value": [{"id": 2}]})],
        sleeps,
    )
    assert [i["id"] for i in api.paginate("workspaces")] == [1, 2]
    assert session.calls[1][2]["params"]["continuationToken"] == "t1"


def test_long_running_operation_polls_then_fetches_result(sleeps):
    accepted = FakeResponse(202, None, {"Location": "https://api.example.test/v1/operations/op1", "Retry-After": "3"})
    api, session = client(
        [
            FakeResponse(200, {"status": "Running"}, {"Retry-After": "2"}),
            FakeResponse(200, {"status": "Succeeded"}),
            FakeResponse(200, {"id": "item-1", "displayName": "db"}),
        ],
        sleeps,
    )
    result = api.wait_for_operation(accepted)
    assert result == {"id": "item-1", "displayName": "db"}
    assert sleeps == [3.0, 2.0]
    assert session.calls[-1][1].endswith("/operations/op1/result")


def test_long_running_operation_without_result_returns_status(sleeps):
    accepted = FakeResponse(202, None, {"Location": "https://api.example.test/v1/operations/op2"})
    api, _ = client([FakeResponse(200, {"status": "Succeeded"}), FakeResponse(404, {"errorCode": "OperationHasNoResult", "message": "none"})], sleeps)
    assert api.wait_for_operation(accepted) == {"status": "Succeeded"}


def test_long_running_operation_failure_raises(sleeps):
    accepted = FakeResponse(202, None, {"Location": "https://api.example.test/v1/operations/op3"})
    api, _ = client([FakeResponse(200, {"status": "Failed", "error": {"errorCode": "Boom", "message": "bad"}})], sleeps)
    with pytest.raises(ApiError) as excinfo:
        api.wait_for_operation(accepted)
    assert excinfo.value.code == "Boom"


def test_long_running_operation_times_out(sleeps):
    ticks = iter([0.0, 5.0, 100.0, 200.0, 300.0])
    accepted = FakeResponse(202, None, {"Location": "https://api.example.test/v1/operations/op4"})
    api, _ = client([FakeResponse(200, {"status": "Running"})] * 3, sleeps)
    with pytest.raises(ApiError) as excinfo:
        api.wait_for_operation(accepted, timeout=60, clock=lambda: next(ticks))
    assert excinfo.value.code == "OperationTimeout"


def test_non_202_returns_body_directly(sleeps):
    api, _ = client([], sleeps)
    assert api.wait_for_operation(FakeResponse(200, {"id": "x"})) == {"id": "x"}
