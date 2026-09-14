"""A run cut short because the runner itself is stopping: a restart, a reinstall, the Mac shutting down.

The browser closes under the run, so the error that surfaces is a technical one about a closed
page. That isn't the run going wrong, so the run, its request and the result all record the
plain reason instead. A genuine failure keeps its real error.
"""

from sqlalchemy import select

from core.models import Run, RunRequestStatus, RunStatus, RunTrigger
from pipeline.worker import STOPPED_BY_RESTART, tick
from tests.test_worker import SCHEDULE, add_request, add_run, at

BROWSER_CLOSED = "TargetClosedError: Browser.new_context: Target page, context or browser has been closed"


class BrowserClosedRunner:
    """Like the real pipeline: records its own failed run, then raises the error again."""

    def __init__(self, now, *, records_run: bool = True):
        self.now = now
        self.records_run = records_run

    def __call__(self, session, *, trigger, profile_id):
        if self.records_run:
            add_run(session, trigger=trigger, started=self.now, status=RunStatus.FAILED, error=BROWSER_CLOSED)
            session.commit()
        raise RuntimeError("Target page, context or browser has been closed")


def run_tick(session, runner, now, *, stopping: bool):
    return tick(
        session,
        runner=runner,
        clock=lambda: now,
        hostname="mac-mini",
        schedule=SCHEDULE,
        stopping=lambda: stopping,
    )


def test_a_run_cut_short_by_a_restart_says_so(session):
    request = add_request(session, requested_at=at(9, 0))

    result = run_tick(session, BrowserClosedRunner(at(9, 1)), at(9, 1), stopping=True)

    run = session.scalars(select(Run).where(Run.trigger == RunTrigger.MANUAL)).one()
    assert run.error == STOPPED_BY_RESTART
    assert request.status == RunRequestStatus.FAILED
    assert request.error == STOPPED_BY_RESTART
    assert result.error == STOPPED_BY_RESTART


def test_a_run_stopped_before_it_got_going_says_so_too(session):
    run_tick(session, BrowserClosedRunner(at(2, 1), records_run=False), at(2, 1), stopping=True)

    run = session.scalars(select(Run).where(Run.trigger == RunTrigger.SCHEDULE)).one()
    assert run.status == RunStatus.FAILED
    assert run.error == STOPPED_BY_RESTART


def test_a_genuine_failure_keeps_its_real_reason(session):
    add_request(session, requested_at=at(9, 0))

    result = run_tick(session, BrowserClosedRunner(at(9, 1)), at(9, 1), stopping=False)

    run = session.scalars(select(Run).where(Run.trigger == RunTrigger.MANUAL)).one()
    assert "TargetClosedError" in run.error
    assert "closed" in result.error
