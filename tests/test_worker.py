"""The nightly runner: when to run, what to pick up, and never two runs at once."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.models import Run, RunRequest, RunRequestStatus, RunStatus, RunTrigger, WorkerState, WorkerStatus
from core.profiles import create_profile
from pipeline.worker import (
    STALE_RUN_AFTER,
    Schedule,
    beat,
    claim_run_request,
    next_scheduled_at,
    recover_abandoned,
    run_lock,
    scheduled_run_due,
    tick,
    write_heartbeat,
)

JOHANNESBURG = ZoneInfo("Africa/Johannesburg")
SCHEDULE = Schedule(at=time(2, 0), tz=JOHANNESBURG)


def at(hour: int, minute: int = 0, day: int = 15) -> datetime:
    """A moment on the Mac Mini's clock, as the UTC time the database stores."""
    return datetime(2026, 9, day, hour, minute, tzinfo=JOHANNESBURG).astimezone(UTC)


def add_run(
    session, *, trigger: str, started: datetime, status: str = RunStatus.SUCCEEDED, error=None
) -> Run:
    finished = None if status == RunStatus.RUNNING else started + timedelta(minutes=20)
    run = Run(
        trigger=trigger,
        status=status,
        error=error,
        started_at=started,
        heartbeat_at=started,
        finished_at=finished,
    )
    session.add(run)
    session.flush()
    return run


def add_request(session, *, requested_at: datetime, profile_id=None, status=RunRequestStatus.PENDING):
    request = RunRequest(
        requested_by="owner@example.com", requested_at=requested_at, profile_id=profile_id, status=status
    )
    session.add(request)
    session.flush()
    return request


class FakeRunner:
    """Records what it was asked to run and leaves a finished run behind, as the real one does."""

    def __init__(self, now: datetime, error: Exception | None = None):
        self.now = now
        self.error = error
        self.calls: list[tuple[str, int | None]] = []

    def __call__(self, session, *, trigger: str, profile_id: int | None) -> int:
        self.calls.append((trigger, profile_id))
        if self.error:
            raise self.error
        run = add_run(session, trigger=trigger, started=self.now)
        session.commit()
        return run.id


def run_tick(session, runner, now: datetime):
    return tick(session, runner=runner, clock=lambda: now, hostname="mac-mini", schedule=SCHEDULE)


class TestSchedule:
    def test_not_due_before_two_in_the_morning(self, session):
        assert not scheduled_run_due(session, at(1, 59), SCHEDULE)

    def test_due_after_two_when_nothing_has_run_tonight(self, session):
        add_run(session, trigger=RunTrigger.SCHEDULE, started=at(2, 5, day=14))

        assert scheduled_run_due(session, at(2, 0), SCHEDULE)

    def test_not_due_once_tonights_run_has_started_even_if_it_failed(self, session):
        add_run(session, trigger=RunTrigger.SCHEDULE, started=at(2, 1), status=RunStatus.FAILED, error="boom")

        assert not scheduled_run_due(session, at(9, 0), SCHEDULE)

    def test_a_run_now_does_not_replace_the_nightly_run(self, session):
        add_run(session, trigger=RunTrigger.MANUAL, started=at(3, 0))

        assert scheduled_run_due(session, at(4, 0), SCHEDULE)

    def test_catches_up_later_in_the_day_if_the_mac_was_asleep(self, session):
        assert scheduled_run_due(session, at(14, 30), SCHEDULE)

    def test_next_scheduled_time(self):
        assert next_scheduled_at(at(1, 0), SCHEDULE) == at(2, 0)
        assert next_scheduled_at(at(3, 0), SCHEDULE) == at(2, 0, day=16)


class TestClaimingRequests:
    def test_claims_the_oldest_pending_request(self, session):
        profile = create_profile(session, "Synman")
        older = add_request(session, requested_at=at(9, 0), profile_id=profile.id)
        add_request(session, requested_at=at(9, 5))

        claimed = claim_run_request(session, at(9, 6))

        assert claimed.id == older.id
        assert claimed.status == RunRequestStatus.CLAIMED
        assert claimed.claimed_at == at(9, 6)

    def test_nothing_pending_means_nothing_claimed(self, session):
        add_request(session, requested_at=at(9, 0), status=RunRequestStatus.DONE)

        assert claim_run_request(session, at(9, 1)) is None

    def test_only_one_open_request_per_scope(self, session):
        add_request(session, requested_at=at(9, 0))

        with pytest.raises(IntegrityError):
            add_request(session, requested_at=at(9, 1))


class TestTick:
    def test_a_run_now_request_is_run_and_marked_done(self, session):
        profile = create_profile(session, "Synman")
        request = add_request(session, requested_at=at(9, 0), profile_id=profile.id)
        runner = FakeRunner(now=at(9, 1))

        result = run_tick(session, runner, at(9, 1))

        assert runner.calls == [(RunTrigger.MANUAL, profile.id)]
        assert result.action == "request"
        assert request.status == RunRequestStatus.DONE
        assert request.finished_at == at(9, 1)

    def test_a_failed_request_is_marked_failed_with_the_reason(self, session):
        request = add_request(session, requested_at=at(9, 0))
        runner = FakeRunner(now=at(9, 1), error=ValueError("Profile “Synman” is not active"))

        result = run_tick(session, runner, at(9, 1))

        assert request.status == RunRequestStatus.FAILED
        assert "not active" in request.error
        assert "not active" in result.error

    def test_the_nightly_run_starts_when_due_and_only_once(self, session):
        runner = FakeRunner(now=at(2, 1))

        first = run_tick(session, runner, at(2, 1))
        second = run_tick(session, runner, at(2, 2))

        assert first.action == "schedule"
        assert second.action == "idle"
        assert runner.calls == [(RunTrigger.SCHEDULE, None)]

    def test_a_nightly_run_that_cannot_start_is_recorded_and_not_retried(self, session):
        runner = FakeRunner(now=at(2, 1), error=RuntimeError("Chromium failed to launch"))

        run_tick(session, runner, at(2, 1))
        again = run_tick(session, runner, at(2, 2))

        failed = session.scalars(select(Run).where(Run.trigger == RunTrigger.SCHEDULE)).one()
        assert failed.status == RunStatus.FAILED
        assert "Chromium" in failed.error
        assert again.action == "idle"
        assert len(runner.calls) == 1

    def test_nothing_to_do_still_checks_in(self, session):
        runner = FakeRunner(now=at(1, 0))

        result = run_tick(session, runner, at(1, 0))

        assert result.action == "idle"
        assert runner.calls == []
        status = session.get(WorkerStatus, 1)
        assert status.hostname == "mac-mini"
        assert status.state == WorkerState.IDLE
        assert status.heartbeat_at == at(1, 0)
        assert status.next_scheduled_at == at(2, 0)

    def test_shows_as_running_during_a_run_and_idle_after(self, session):
        seen: list[str] = []

        class PeekingRunner(FakeRunner):
            def __call__(self, session, **kwargs):
                seen.append(session.get(WorkerStatus, 1).state)
                return super().__call__(session, **kwargs)

        run_tick(session, PeekingRunner(now=at(2, 1)), at(2, 1))

        assert seen == [WorkerState.RUNNING]
        assert session.get(WorkerStatus, 1).state == WorkerState.IDLE


class TestHeartbeat:
    def test_there_is_only_ever_one_status_row(self, session):
        write_heartbeat(session, at(1, 0), hostname="mac-mini", state=WorkerState.IDLE)
        write_heartbeat(session, at(1, 1), hostname="mac-mini", state=WorkerState.IDLE)

        rows = session.scalars(select(WorkerStatus)).all()
        assert len(rows) == 1
        assert rows[0].heartbeat_at == at(1, 1)

    def test_the_database_refuses_a_second_row(self, session):
        session.add(WorkerStatus(id=2, hostname="x", state="idle", started_at=at(1), heartbeat_at=at(1)))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_beat_keeps_a_long_run_looking_alive(self, session):
        write_heartbeat(session, at(2, 0), hostname="mac-mini", state=WorkerState.RUNNING)
        run = add_run(session, trigger=RunTrigger.SCHEDULE, started=at(2, 0), status=RunStatus.RUNNING)

        beat(session, at(3, 30))

        assert session.get(WorkerStatus, 1).heartbeat_at == at(3, 30)
        assert run.heartbeat_at == at(3, 30)


class TestRecovery:
    def test_work_abandoned_by_a_crashed_runner_is_marked_failed(self, session):
        stale = add_run(session, trigger=RunTrigger.SCHEDULE, started=at(2, 0), status=RunStatus.RUNNING)
        request = add_request(session, requested_at=at(1, 0), status=RunRequestStatus.CLAIMED)
        request.claimed_at = at(1, 0)

        recover_abandoned(session, at(2, 0) + STALE_RUN_AFTER + timedelta(minutes=1))

        assert stale.status == RunStatus.FAILED
        assert "stopped" in stale.error
        assert request.status == RunRequestStatus.FAILED

    def test_a_run_that_is_still_beating_is_left_alone(self, session):
        live = add_run(session, trigger=RunTrigger.SCHEDULE, started=at(2, 0), status=RunStatus.RUNNING)

        recover_abandoned(session, at(2, 5))

        assert live.status == RunStatus.RUNNING


class TestRunLock:
    def test_a_second_run_cannot_start_while_one_holds_the_lock(self, engine):
        with run_lock(engine) as first:
            assert first
            with run_lock(engine) as second:
                assert not second

        with run_lock(engine) as again:
            assert again
