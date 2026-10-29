"""Refresh command for the MedTech semantic model: credential binding, waiting and reporting (no tenant access)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts import refresh_model as rm
from scripts.lib.auth import AuthError
from scripts.lib.http import ApiError

TOKEN = "eyJ-fake-sql-token-value-that-must-never-be-printed"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakePowerBi:
    """Records the order of the calls and plays back refresh histories."""

    def __init__(self, *, sources: int = 1, histories=None, final=None, request_id: str = "c46ccb6c-0000-0000-0000-000000000000") -> None:
        self.calls: list[str] = []
        self.tokens: list[str] = []
        self.sources = sources
        self.request_id = request_id
        self.histories = list(histories if histories is not None else [[]])
        self.final = final if final is not None else {"status": "Completed", "endTime": "2026-10-27T12:58:50Z"}

    def set_oauth_credentials(self, workspace_id, dataset_id, access_token, *, datasource_type="Sql"):
        self.calls.append("bind")
        self.tokens.append(access_token)
        return self.sources

    def refresh_history(self, workspace_id, dataset_id, top=5):
        self.calls.append("history")
        return self.histories.pop(0) if len(self.histories) > 1 else self.histories[0]

    def refresh(self, workspace_id, dataset_id, *, body=None):
        self.calls.append("refresh")
        return self.request_id

    def wait_for_refresh(self, workspace_id, dataset_id, request_id, *, timeout=900.0, interval=10.0):
        self.calls.append("wait")
        if self.final.get("status") != "Completed":
            raise ApiError(500, "RefreshFailed", f"Refresh {request_id} ended as {self.final['status']}: {self.final.get('serviceExceptionJson') or ''}")
        return self.final


def run(powerbi, clock=None, lines=None, **kwargs):
    clock = clock or Clock()
    lines = [] if lines is None else lines
    outcome = rm.refresh(powerbi, "ws-1", "model-1", TOKEN, sleep=clock.sleep, clock=clock, report=lines.append, **kwargs)
    return outcome, lines


def test_the_credential_is_bound_before_the_refresh_starts_and_the_outcome_is_reported():
    powerbi = FakePowerBi()
    outcome, lines = run(powerbi)
    assert powerbi.calls == ["bind", "history", "refresh", "wait"]
    assert powerbi.tokens == [TOKEN]
    assert (outcome.request_id[:8], outcome.sources, outcome.ended) == ("c46ccb6c", 1, "2026-10-27T12:58:50Z")
    assert lines[0].startswith("Bound a fresh OAuth2 credential to 1 SQL data source")


def test_the_token_is_never_written_to_the_report_lines():
    _, lines = run(FakePowerBi())
    assert not any(TOKEN in line or "eyJ" in line for line in lines)


def test_a_model_without_a_sql_source_is_an_error_and_nothing_is_refreshed():
    powerbi = FakePowerBi(sources=0)
    with pytest.raises(RuntimeError, match="no SQL data source"):
        run(powerbi)
    assert powerbi.calls == ["bind"]


def test_a_refresh_that_is_still_running_is_waited_for_before_a_new_one_is_started():
    running = [{"status": "Unknown", "startTime": "2026-10-27T12:43:01Z"}]
    done = [{"status": "Failed", "startTime": "2026-10-27T12:43:01Z"}]
    powerbi = FakePowerBi(histories=[running, running, done])
    clock = Clock()
    _, lines = run(powerbi, clock)
    assert powerbi.calls == ["bind", "history", "history", "history", "refresh", "wait"]
    assert clock.now == 20.0  # two pauses of ten seconds
    assert sum("still running" in line for line in lines) == 1


def test_a_refresh_that_never_ends_stops_the_wait_with_a_conflict_instead_of_starting_a_second_one():
    running = [{"status": "Unknown", "startTime": "2026-10-27T12:43:01Z"}]
    powerbi = FakePowerBi(histories=[running])
    clock = Clock()
    with pytest.raises(ApiError) as excinfo:
        run(powerbi, clock, idle_timeout=30)
    assert excinfo.value.status == 409 and "still running after 30 s" in str(excinfo.value)
    assert "refresh" not in powerbi.calls and clock.now == 30.0


def test_a_refresh_without_a_request_id_is_an_error_and_not_a_false_success_from_an_older_history_entry():
    powerbi = FakePowerBi(request_id="")
    with pytest.raises(ApiError, match="no request id"):
        run(powerbi)
    assert "wait" not in powerbi.calls


def test_an_unknown_status_with_an_end_time_is_not_a_running_refresh():
    finished = [{"status": "Unknown", "startTime": "2026-10-27T12:43:01Z", "endTime": "2026-10-27T12:44:00Z"}]
    powerbi = FakePowerBi(histories=[finished])
    clock = Clock()
    run(powerbi, clock)
    assert powerbi.calls == ["bind", "history", "refresh", "wait"] and clock.now == 0.0  # no pause: it did not wait for the old entry
    assert rm.is_running({"status": "Unknown"}) and not rm.is_running(finished[0]) and not rm.is_running({"status": "Failed"})
    assert "unknown" in rm.describe(finished)[0] and "running" not in rm.describe(finished)[0]


def test_a_failed_refresh_surfaces_the_services_own_message():
    final = {"status": "Failed", "serviceExceptionJson": json.dumps({"errorCode": "ModelRefresh_ShortMessage_ProcessingError", "errorDescription": "The credentials provided for the SQL source are invalid."})}
    with pytest.raises(ApiError, match="credentials provided for the SQL source are invalid"):
        run(FakePowerBi(final=final))


def test_the_duration_is_measured_from_the_start_of_the_refresh_not_from_the_binding():
    class Slow(FakePowerBi):
        def __init__(self, clock):
            super().__init__()
            self.clock = clock

        def set_oauth_credentials(self, *args, **kwargs):
            self.clock.now += 100
            return super().set_oauth_credentials(*args, **kwargs)

        def wait_for_refresh(self, *args, **kwargs):
            self.clock.now += 47
            return super().wait_for_refresh(*args, **kwargs)

    clock = Clock()
    outcome, _ = run(Slow(clock), clock)
    assert outcome.seconds == 47.0


def test_a_failure_reason_is_read_from_the_service_json_and_kept_to_one_line():
    entry = {"serviceExceptionJson": json.dumps({"errorCode": "X", "errorDescription": "The credentials\nprovided   are invalid. " + "x" * 400})}
    reason = rm.failure_reason(entry)
    assert reason.startswith("The credentials provided are invalid.") and "\n" not in reason and len(reason) == 300
    assert rm.failure_reason({}) == "" and rm.failure_reason({"serviceExceptionJson": "not json"}) == "not json"
    assert rm.failure_reason({"serviceExceptionJson": json.dumps({"errorCode": "OnlyCode"})}) == "OnlyCode"


def test_describe_lists_running_failed_and_completed_refreshes_newest_first():
    lines = rm.describe([
        {"status": "Unknown", "startTime": "t3"},
        {"status": "Failed", "startTime": "t2", "endTime": "t2e", "serviceExceptionJson": json.dumps({"errorDescription": "credentials invalid"})},
        {"status": "Completed", "startTime": "t1", "endTime": "t1e"},
    ])
    assert lines[0].startswith("t3") and "running" in lines[0]
    assert "failed" in lines[1] and lines[1].endswith("credentials invalid") and "until t2e" in lines[1]
    assert "completed" in lines[2] and "until t1e" in lines[2]


def fake_context(powerbi, token_error=None, model=True):
    def get_token(resource):
        assert resource == "sql"
        if token_error:
            raise token_error
        return SimpleNamespace(value=TOKEN)

    return SimpleNamespace(
        cfg=SimpleNamespace(names=SimpleNamespace(medtech_model="MedTech")),
        workspace_id=lambda: "ws-1",
        manifest=SimpleNamespace(find=lambda kind, name, workspace_id=None: SimpleNamespace(id="model-1") if model else None),
        clients=SimpleNamespace(tokens=SimpleNamespace(get=get_token), powerbi=powerbi),
    )


def test_main_refreshes_with_the_signed_in_identitys_sql_token_and_never_prints_it(monkeypatch, capsys):
    powerbi = FakePowerBi()
    monkeypatch.setattr(rm, "Context", lambda: fake_context(powerbi))
    assert rm.main([]) == 0
    out = capsys.readouterr().out
    assert powerbi.tokens == [TOKEN] and TOKEN not in out and "REFRESHED: c46ccb6c" in out and "Refresh Data" in out


def test_main_reports_a_failed_refresh_with_exit_code_one(monkeypatch, capsys):
    final = {"status": "Failed", "serviceExceptionJson": json.dumps({"errorDescription": "The credentials provided for the SQL source are invalid."})}
    monkeypatch.setattr(rm, "Context", lambda: fake_context(FakePowerBi(final=final)))
    assert rm.main([]) == 1
    assert "REFRESH FAILED" in capsys.readouterr().out


def test_main_stops_when_the_sign_in_fails_or_the_model_is_not_deployed(monkeypatch, capsys):
    powerbi = FakePowerBi()
    monkeypatch.setattr(rm, "Context", lambda: fake_context(powerbi, token_error=AuthError("az timed out")))
    assert rm.main([]) == 2 and "Cannot sign in" in capsys.readouterr().out and powerbi.calls == []
    monkeypatch.setattr(rm, "Context", lambda: fake_context(powerbi, model=False))
    assert rm.main([]) == 2 and "deploy.py" in capsys.readouterr().out and powerbi.calls == []


def test_status_is_read_only_and_points_at_the_command_when_the_latest_refresh_failed(monkeypatch, capsys):
    history = [{"status": "Failed", "startTime": "t2", "endTime": "t2e", "serviceExceptionJson": json.dumps({"errorDescription": "credentials invalid"})}, {"status": "Completed", "startTime": "t1", "endTime": "t1e"}]
    powerbi = FakePowerBi(histories=[history])
    monkeypatch.setattr(rm, "Context", lambda: fake_context(powerbi))
    assert rm.main(["--status"]) == 0
    out = capsys.readouterr().out
    assert powerbi.calls == ["history"] and "credentials invalid" in out and "scripts/refresh_model.py" in out
    powerbi = FakePowerBi(histories=[[]])
    monkeypatch.setattr(rm, "Context", lambda: fake_context(powerbi))
    assert rm.main(["--status"]) == 0 and "No refresh has been recorded" in capsys.readouterr().out


def test_verify_runs_the_t10_reconciliation_after_the_refresh_and_fails_the_command_when_it_fails(monkeypatch, capsys):
    import scripts.validate as validate

    powerbi = FakePowerBi()
    ctx = fake_context(powerbi)
    monkeypatch.setattr(rm, "Context", lambda: ctx)
    monkeypatch.setattr(validate, "t10", lambda c: ("passed", "model equals SQL") if c is ctx else pytest.fail("T-10 must get the same context"))
    assert rm.main(["--verify"]) == 0 and "T-10 PASSED: model equals SQL" in capsys.readouterr().out
    monkeypatch.setattr(validate, "t10", lambda c: ("failed", "total differs"))
    assert rm.main(["--verify"]) == 1 and "T-10 FAILED: total differs" in capsys.readouterr().out

    def rejected(c):
        raise ApiError(400, "DatasetExecuteQueriesError", "compute capacity has exceeded its limits")

    monkeypatch.setattr(validate, "t10", rejected)
    assert rm.main(["--verify"]) == 1 and "VERIFY FAILED" in capsys.readouterr().out


def test_without_verify_the_reconciliation_is_not_run(monkeypatch):
    import scripts.validate as validate

    monkeypatch.setattr(rm, "Context", lambda: fake_context(FakePowerBi()))
    monkeypatch.setattr(validate, "t10", lambda c: pytest.fail("T-10 must only run with --verify"))
    assert rm.main([]) == 0


def test_a_non_positive_timeout_is_refused_before_the_tenant_is_touched(monkeypatch):
    monkeypatch.setattr(rm, "Context", lambda: pytest.fail("the tenant must not be touched for an invalid argument"))
    with pytest.raises(SystemExit) as excinfo:
        rm.main(["--timeout", "0"])
    assert excinfo.value.code == 2
