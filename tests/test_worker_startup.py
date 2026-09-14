"""The runner starting up after being killed mid-run (a restart, a reinstall, a power cut).

A killed run's heartbeat can be only seconds old, so "stale for 10 minutes" alone would leave it
showing as running forever. But if no other runner holds the run lock, nothing can really be
running, so whatever is still open was left behind and is closed straight away.
"""

from datetime import UTC, datetime

from core.models import Run, RunRequest, RunRequestStatus, RunStatus, RunTrigger
from pipeline.worker import recover_at_startup, run_lock

JUST_KILLED = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
RESTARTED = datetime(2026, 9, 14, 10, 1, tzinfo=UTC)


def running_run(session) -> Run:
    run = Run(
        trigger=RunTrigger.SCHEDULE,
        status=RunStatus.RUNNING,
        started_at=JUST_KILLED,
        heartbeat_at=JUST_KILLED,
    )
    session.add(run)
    session.flush()
    return run


def claimed_request(session) -> RunRequest:
    request = RunRequest(
        requested_by="owner@example.com",
        requested_at=JUST_KILLED,
        status=RunRequestStatus.CLAIMED,
        claimed_at=JUST_KILLED,
    )
    session.add(request)
    session.flush()
    return request


def test_with_no_other_runner_a_just_killed_run_is_closed(session, engine):
    killed = running_run(session)
    request = claimed_request(session)

    closed = recover_at_startup(session, engine, RESTARTED)

    assert closed == 2
    assert killed.status == RunStatus.FAILED
    assert "stopped" in killed.error
    assert request.status == RunRequestStatus.FAILED


def test_a_run_another_runner_holds_is_left_alone(session, engine):
    live = running_run(session)

    with run_lock(engine) as held:
        assert held
        closed = recover_at_startup(session, engine, RESTARTED)

    assert closed == 0
    assert live.status == RunStatus.RUNNING
