"""HTTP client with retries, Retry-After handling, pagination and Fabric long-running-operation polling."""

from __future__ import annotations

import time
from typing import Any, Callable, Iterator

import requests

RETRYABLE_STATUS = {429, 502, 503, 504}
USER_AGENT = "FabricPlanningDemo/1.0"


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, *, url: str = "", request_id: str = "", body: Any = None) -> None:
        super().__init__(f"{status} {code}: {message} ({url})" if url else f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.url = url
        self.request_id = request_id
        self.body = body


def is_capacity_rejection(error: BaseException) -> bool:
    """HTTP 400 whose message names the capacity: the throttling stage that rejects interactive queries (`DatasetExecuteQueriesError`,
    'compute capacity has exceeded its limits'). It says nothing about the query or the model, so it is an infrastructure problem, not a failed check."""
    return isinstance(error, ApiError) and error.status == 400 and "capacity" in str(error).lower()


def _error_from(response: requests.Response) -> ApiError:
    code, message, body = "HttpError", (response.text or "")[:500], None
    try:
        body = response.json()
    except ValueError:
        pass
    if isinstance(body, dict):
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        code = str(error.get("errorCode") or error.get("code") or code)
        message = str(error.get("message") or message)
    request_id = response.headers.get("RequestId") or response.headers.get("x-ms-request-id") or response.headers.get("request-id") or ""
    return ApiError(response.status_code, code, message, url=response.url or "", request_id=request_id, body=body)


def _retry_delay(response: requests.Response | None, attempt: int, cap: float = 60.0) -> float:
    if response is not None:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), cap)
            except ValueError:
                pass
    return min(2.0 ** attempt, cap)


class ApiClient:
    def __init__(
        self,
        base_url: str,
        bearer: Callable[[], str],
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 5,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._bearer = bearer
        self._session = session or requests.Session()
        self._sleep = sleep
        self.max_retries = max_retries
        self.timeout = timeout

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return f"{self.base_url}/{path_or_url.lstrip('/')}"

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        data: Any = None,
        files: Any = None,
        headers: dict | None = None,
        ok: tuple[int, ...] = (200, 201, 202, 204),
        retry_server_errors: bool | None = None,
    ) -> requests.Response:
        """Send a request. 429 is always retried; 5xx and connection errors only for idempotent methods unless overridden."""
        url = self._url(path_or_url)
        idempotent = method.upper() in {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}
        retry_5xx = idempotent if retry_server_errors is None else retry_server_errors
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            request_headers = {"Authorization": f"Bearer {self._bearer()}", "User-Agent": USER_AGENT}
            if headers:
                request_headers.update(headers)
            try:
                response = self._session.request(
                    method, url, params=params, json=json_body, data=data, files=files, headers=request_headers, timeout=self.timeout
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                if not retry_5xx or attempt >= self.max_retries:
                    raise
                self._sleep(_retry_delay(None, attempt))
                continue
            if response.status_code in ok:
                return response
            retryable = response.status_code == 429 or (response.status_code in RETRYABLE_STATUS and retry_5xx)
            if retryable and attempt < self.max_retries:
                self._sleep(_retry_delay(response, attempt))
                continue
            raise _error_from(response)
        raise ApiError(0, "RetriesExhausted", str(last_error or "retries exhausted"), url=url)

    def get_json(self, path_or_url: str, **kwargs: Any) -> Any:
        return self.request("GET", path_or_url, **kwargs).json()

    def paginate(self, path: str, *, key: str = "value", params: dict | None = None) -> Iterator[dict]:
        """Iterate a Fabric list endpoint that returns `continuationToken`."""
        query = dict(params or {})
        while True:
            body = self.get_json(path, params=query)
            for item in body.get(key, []):
                yield item
            token = body.get("continuationToken")
            if not token:
                return
            query["continuationToken"] = token

    def wait_for_operation(self, response: requests.Response, *, timeout: float = 900.0, default_interval: float = 5.0, clock: Callable[[], float] = time.monotonic) -> dict:
        """Follow a Fabric long-running operation (202 + Location) to completion and return its result (or final status)."""
        if response.status_code != 202:
            return response.json() if response.content else {}
        location = response.headers.get("Location")
        if not location:
            raise ApiError(202, "MissingLocation", "202 Accepted without a Location header", url=response.url or "")
        interval = _parse_interval(response.headers.get("Retry-After"), default_interval)
        deadline = clock() + timeout
        while True:
            self._sleep(interval)
            poll = self.request("GET", location)
            status_body = poll.json() if poll.content else {}
            state = str(status_body.get("status", ""))
            if state == "Succeeded":
                return self._fetch_result(location, poll, status_body)
            if state == "Failed":
                error = status_body.get("error") or {}
                raise ApiError(500, str(error.get("errorCode", "OperationFailed")), str(error.get("message", "Long-running operation failed")), url=location, body=status_body)
            if clock() > deadline:
                raise ApiError(408, "OperationTimeout", f"Operation did not finish within {timeout:.0f}s (last status: {state or 'unknown'})", url=location)
            interval = _parse_interval(poll.headers.get("Retry-After"), default_interval)

    def _fetch_result(self, location: str, poll: requests.Response, status_body: dict) -> dict:
        result_url = poll.headers.get("Location") or location.rstrip("/") + "/result"
        try:
            result = self.request("GET", result_url)
        except ApiError as exc:
            if exc.status in (400, 404):
                return status_body
            raise
        return result.json() if result.content else status_body


def _parse_interval(value: str | None, default: float) -> float:
    try:
        return max(float(value), 0.0) if value is not None else default
    except ValueError:
        return default
