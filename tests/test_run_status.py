"""What the web app says about the runner: is it there, what ran, and what needs attention."""

from datetime import UTC, datetime, timedelta

from core.models import Run, RunRequestStatus, RunStatus, RunTrigger, WorkerState, WorkerStatus
from core.profiles import create_profile
from core.run_status import NO_RUN_WARNING_AFTER, RUNNER_OFFLINE_AFTER, request_run, run_status
from tests.factories import default_artist_id

NOW = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)


def checked_in(
    session, at: datetime, state: str = WorkerState.IDLE, next_run: datetime | None = None
) -> None:
    session.add(
        WorkerStatus(
            id=1, hostname="mac-mini", state=state, started_at=at, heartbeat_at=at, next_scheduled_at=next_run
        )
    )
    session.flush()


def finished_run(session, *, finished: datetime, status: str = RunStatus.SUCCEEDED, error: str | None = None):
    run = Run(
        trigger=RunTrigger.SCHEDULE,
        status=status,
        error=error,
        started_at=finished - timedelta(minutes=20),
        heartbeat_at=finished,
        finished_at=finished,
    )
    session.add(run)
    session.flush()
    return run


class TestRequestingARun:
    def test_run_now_queues_a_request_for_everything(self, session):
        request, created = request_run(session, requested_by="owner@example.com")

        assert created
        assert request.status == RunRequestStatus.PENDING
        assert request.profile_id is None
        assert request.requested_by == "owner@example.com"

    def test_clicking_twice_does_not_queue_twice(self, session):
        first, _ = request_run(session, requested_by="owner@example.com")
        second, created = request_run(session, requested_by="owner@example.com")

        assert not created
        assert second.id == first.id

    def test_a_single_profile_run_is_its_own_scope(self, session):
        profile = create_profile(session, default_artist_id(session), "Synman")
        everything, _ = request_run(session, requested_by="owner@example.com")

        just_one, created = request_run(session, requested_by="owner@example.com", profile_id=profile.id)

        assert created
        assert just_one.id != everything.id

    def test_once_a_request_is_done_a_new_one_can_be_queued(self, session):
        first, _ = request_run(session, requested_by="owner@example.com")
        first.status = RunRequestStatus.DONE
        session.flush()

        second, created = request_run(session, requested_by="owner@example.com")

        assert created
        assert second.id != first.id


class TestRunStatus:
    def test_a_runner_that_never_checked_in(self, session):
        status = run_status(session, NOW)

        assert not status.runner_online
        assert any("never checked in" in warning for warning in status.warnings)

    def test_a_quiet_runner_is_offline(self, session):
        checked_in(session, NOW - RUNNER_OFFLINE_AFTER - timedelta(seconds=1))

        status = run_status(session, NOW)

        assert not status.runner_online
        assert any("hasn't checked in" in warning for warning in status.warnings)

    def test_a_healthy_runner_after_a_good_night(self, session):
        checked_in(session, NOW - timedelta(seconds=30), next_run=NOW + timedelta(hours=17))
        run = finished_run(session, finished=NOW - timedelta(hours=6))

        status = run_status(session, NOW)

        assert status.runner_online
        assert status.warnings == ()
        assert status.last_run.id == run.id
        assert status.next_scheduled_at == NOW + timedelta(hours=17)

    def test_a_failed_last_run_is_flagged_with_its_reason(self, session):
        checked_in(session, NOW)
        finished_run(
            session,
            finished=NOW - timedelta(hours=6),
            status=RunStatus.FAILED,
            error="Spotify blocked the fetcher",
        )

        assert any("Spotify blocked the fetcher" in warning for warning in run_status(session, NOW).warnings)

    def test_nothing_finished_for_more_than_a_day(self, session):
        checked_in(session, NOW)
        finished_run(session, finished=NOW - NO_RUN_WARNING_AFTER - timedelta(minutes=1))

        assert any("26 hours" in warning for warning in run_status(session, NOW).warnings)

    def test_a_run_in_progress_is_shown_and_not_nagged_about(self, session):
        checked_in(session, NOW, state=WorkerState.RUNNING)
        finished_run(session, finished=NOW - timedelta(hours=30))
        current = Run(
            trigger=RunTrigger.MANUAL,
            status=RunStatus.RUNNING,
            started_at=NOW - timedelta(minutes=5),
            heartbeat_at=NOW,
        )
        session.add(current)
        session.flush()

        status = run_status(session, NOW)

        assert status.current_run.id == current.id
        assert not any("26 hours" in warning for warning in status.warnings)

    def test_an_open_request_is_reported(self, session):
        request, _ = request_run(session, requested_by="owner@example.com")

        assert run_status(session, NOW).open_request.id == request.id

    def test_a_failure_is_not_nagged_about_once_a_newer_run_is_under_way(self, session):
        checked_in(session, NOW, state=WorkerState.RUNNING)
        finished_run(
            session, finished=NOW - timedelta(minutes=10), status=RunStatus.FAILED, error="TargetClosedError"
        )
        session.add(
            Run(
                trigger=RunTrigger.MANUAL,
                status=RunStatus.RUNNING,
                started_at=NOW - timedelta(minutes=5),
                heartbeat_at=NOW,
            )
        )
        session.flush()

        assert not any("failed" in warning for warning in run_status(session, NOW).warnings)
