"""Capacity health probe: classification and waiting logic around one trivial DAX query (no tenant access)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import capacity_check as cc
from scripts.lib.auth import AuthError
from scripts.lib.http import ApiError

REJECTION = ApiError(400, "DatasetExecuteQueriesError", "The operation failed because the compute capacity has exceeded its limits")


class Clock:
    """A fake clock whose `sleep` and whose fake queries advance the same time."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def query_taking(clock: Clock, seconds: float, error: BaseException | None = None):
    def run() -> None:
        clock.now += seconds
        if error is not None:
            raise error

    return run


def test_an_answer_within_eight_seconds_is_healthy_and_a_slower_one_is_delayed():
    assert cc.classify(3.6) == "healthy" and cc.classify(8.0) == "healthy"
    assert cc.classify(8.1) == "delayed" and cc.classify(21.6) == "delayed"


def test_the_capacity_rejection_is_told_apart_from_other_errors():
    assert cc.classify(2.0, REJECTION) == "rejected"
    assert cc.classify(2.0, ApiError(400, "DatasetExecuteQueriesError", "syntax error in the query")) == "error"  # a bad query is not a capacity problem
    assert cc.classify(2.0, ApiError(401, "Unauthorized", "token expired")) == "error"
    assert cc.classify(2.0, TimeoutError("read timed out")) == "error"


def test_a_probe_times_the_query_and_reports_failures_instead_of_raising():
    clock = Clock()
    slow = cc.probe(query_taking(clock, 21.5), clock)
    assert (slow.state, round(slow.seconds, 1), slow.healthy) == ("delayed", 21.5, False)
    rejected = cc.probe(query_taking(clock, 1.2, REJECTION), clock)
    assert rejected.state == "rejected" and "exceeded its limits" in rejected.detail and str(rejected).startswith("rejected 1.2 s - ")
    assert str(cc.probe(query_taking(clock, 3.4), clock)) == "healthy 3.4 s"


def test_check_runs_the_requested_number_of_probes_with_a_pause_between_them_only():
    clock, lines = Clock(), []
    results = cc.check(query_taking(clock, 3.0), probes=3, interval=5, sleep=clock.sleep, report=lines.append, clock=clock)
    assert [p.state for p in results] == ["healthy"] * 3 and len(lines) == 3
    assert clock.now == 3 * 3.0 + 2 * 5  # three answers and two pauses, none before the first


def test_wait_returns_after_three_healthy_answers_in_a_row_and_a_slow_one_resets_the_streak():
    clock, lines = Clock(), []
    answers = iter([21.0, 3.0, 25.0, 3.0, 3.0, 3.0])

    def run() -> None:
        clock.now += next(answers)

    assert cc.wait_until_healthy(run, minutes=60, interval=120, sleep=clock.sleep, report=lines.append, clock=clock) is True
    assert len(lines) == 6 and lines[-1].endswith("(healthy in a row: 3/3)") and "(healthy in a row: 0/3)" in lines[2]


def test_wait_gives_up_at_the_deadline_without_sleeping_past_it():
    clock, lines = Clock(), []
    assert cc.wait_until_healthy(query_taking(clock, 21.5), minutes=10, interval=120, sleep=clock.sleep, report=lines.append, clock=clock) is False
    assert len(lines) == 5 and clock.now <= 10 * 60


def fake_context(run_query, events=None, token_error=None):
    events = [] if events is None else events

    def get_token(resource):
        events.append(f"token:{resource}")
        if token_error:
            raise token_error

    def execute(wid, mid, dax):
        events.append("query")
        return run_query(dax)

    return SimpleNamespace(
        cfg=SimpleNamespace(names=SimpleNamespace(medtech_model="MedTech")),
        workspace_id=lambda: "ws-1",
        manifest=SimpleNamespace(find=lambda kind, name, workspace_id=None: SimpleNamespace(id="model-1")),
        clients=SimpleNamespace(tokens=SimpleNamespace(get=get_token), powerbi=SimpleNamespace(execute_queries=execute)),
    )


def test_main_exits_zero_only_when_every_probe_is_healthy(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cc, "Context", lambda: fake_context(lambda dax: seen.append(dax) or []))
    assert cc.main(["--probes", "2", "--interval", "0"]) == 0
    assert seen == [cc.QUERY, cc.QUERY] and "HEALTHY" in capsys.readouterr().out

    def reject(dax):
        raise REJECTION

    monkeypatch.setattr(cc, "Context", lambda: fake_context(reject))
    assert cc.main(["--probes", "1"]) == 1
    out = capsys.readouterr().out
    assert "rejected" in out and "NOT HEALTHY" in out


def test_the_token_is_fetched_before_the_first_timed_probe_so_that_az_startup_is_not_counted_as_a_slow_answer(monkeypatch):
    events = []
    monkeypatch.setattr(cc, "Context", lambda: fake_context(lambda dax: [], events))
    assert cc.main(["--probes", "2", "--interval", "0"]) == 0
    assert events == ["token:powerbi", "query", "query"]


def test_main_stops_without_probing_when_the_sign_in_fails(monkeypatch, capsys):
    events = []
    monkeypatch.setattr(cc, "Context", lambda: fake_context(lambda dax: [], events, token_error=AuthError("az timed out")))
    assert cc.main([]) == 2
    assert "Cannot sign in" in capsys.readouterr().out and "query" not in events


@pytest.mark.parametrize("argv", [["--probes", "0"], ["--probes", "-2"], ["--wait", "0"], ["--wait", "-5"], ["--interval", "-1"]])
def test_arguments_that_would_probe_nothing_or_sleep_backwards_are_refused_before_anything_runs(monkeypatch, argv):
    monkeypatch.setattr(cc, "Context", lambda: pytest.fail("the tenant must not be touched for an invalid argument"))
    with pytest.raises(SystemExit) as excinfo:
        cc.main(argv)
    assert excinfo.value.code == 2  # a zero-probe run must never report HEALTHY


def test_main_stops_when_the_model_is_not_deployed(monkeypatch, capsys):
    ctx = fake_context(lambda dax: [])
    ctx.manifest = SimpleNamespace(find=lambda kind, name, workspace_id=None: None)
    monkeypatch.setattr(cc, "Context", lambda: ctx)
    assert cc.main([]) == 2 and "deploy.py" in capsys.readouterr().out
