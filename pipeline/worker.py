"""The nightly runner on the Mac Mini.

A small loop, kept alive by launchd, wakes once a minute and does the first thing that applies:

1. Checks in, so the web app can show the runner is there.
2. Picks up a "Run now" request the web app left, oldest first.
3. Starts the nightly run once 02:00 has passed and no nightly run has started today. The same
   rule catches up when the Mac was asleep at 02:00: the run starts when it wakes.

A run can take an hour, so a background thread keeps checking in while it works. A Postgres
advisory lock means two runs never overlap, even if a second runner is started by mistake.
If the runner is killed mid-run, its next start closes the abandoned run and request as
failed, so nothing is left "running" forever.

A nightly run that fails isn't retried until the next night. A run that dies before it even
starts (no browser, no network) is recorded as failed too, so the web app can say so and the
loop doesn't try again every minute.
"""

import logging
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from core.models import Run, RunRequest, RunRequestStatus, RunStatus, RunTrigger, WorkerState, WorkerStatus
from pipeline.nightly import describe_run, run_pipeline
from pipeline.runs import finish_run

logger = logging.getLogger("noble_hunter.worker")

WORKER_ROW_ID = 1
NIGHTLY_AT = time(2, 0)
POLL_SECONDS = 60
BEAT_SECONDS = 30
STALE_RUN_AFTER = timedelta(minutes=10)
START_TOLERANCE = timedelta(minutes=1)  # the Mac's clock and the database's can differ a little
RUN_LOCK_KEY = 7_406_311  # any fixed number, as long as every runner uses the same one
MAX_ERROR_LENGTH = 500
LOG_FILE_BYTES = 5_000_000
LOG_FILE_COUNT = 5


class RunInProgress(RuntimeError):
    """Another run holds the lock."""


@dataclass(frozen=True)
class Schedule:
    at: time
    tz: tzinfo


@dataclass(frozen=True)
class TickResult:
    action: str  # "idle", "request" or "schedule"
    run_id: int | None = None
    error: str | None = None


class Runner(Protocol):
    def __call__(self, session: Session, *, trigger: str, profile_id: int | None) -> int: ...


def local_schedule() -> Schedule:
    """02:00 in the Mac's own time zone, read fresh each time so a clock change is picked up."""
    return Schedule(at=NIGHTLY_AT, tz=datetime.now().astimezone().tzinfo)


# --- Deciding what to do ------------------------------------------------------------------


def scheduled_run_due(session: Session, now: datetime, schedule: Schedule) -> bool:
    slot = _todays_slot(now, schedule)
    if now < slot:
        return False
    started_tonight = session.scalar(
        select(Run.id).where(Run.trigger == RunTrigger.SCHEDULE, Run.started_at >= slot).limit(1)
    )
    return started_tonight is None


def next_scheduled_at(now: datetime, schedule: Schedule) -> datetime:
    slot = _todays_slot(now, schedule)
    upcoming = slot if now < slot else slot + timedelta(days=1)
    return upcoming.astimezone(UTC)


def claim_run_request(session: Session, now: datetime) -> RunRequest | None:
    request = session.scalar(
        select(RunRequest)
        .where(RunRequest.status == RunRequestStatus.PENDING)
        .order_by(RunRequest.requested_at, RunRequest.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if request is None:
        return None
    request.status = RunRequestStatus.CLAIMED
    request.claimed_at = now
    session.flush()
    return request


def tick(
    session: Session,
    *,
    runner: Runner,
    clock: Callable[[], datetime],
    hostname: str,
    schedule: Schedule,
) -> TickResult:
    """One pass of the loop: check in, then run a requested or scheduled run if there is one."""
    now = clock()
    write_heartbeat(
        session, now, hostname=hostname, state=WorkerState.IDLE, next_run_at=next_scheduled_at(now, schedule)
    )
    session.commit()

    request = claim_run_request(session, now)
    if request is not None:
        session.commit()
        return _run(session, runner, clock, hostname, schedule, trigger=RunTrigger.MANUAL, request=request)
    if scheduled_run_due(session, now, schedule):
        return _run(session, runner, clock, hostname, schedule, trigger=RunTrigger.SCHEDULE, request=None)
    return TickResult("idle")


def _run(
    session: Session,
    runner: Runner,
    clock: Callable[[], datetime],
    hostname: str,
    schedule: Schedule,
    *,
    trigger: str,
    request: RunRequest | None,
) -> TickResult:
    action = "request" if request is not None else "schedule"
    profile_id = request.profile_id if request is not None else None
    started = clock()
    write_heartbeat(
        session,
        started,
        hostname=hostname,
        state=WorkerState.RUNNING,
        next_run_at=next_scheduled_at(started, schedule),
    )
    session.commit()

    logger.info("Starting a %s run%s", trigger, f" for profile {profile_id}" if profile_id else "")
    try:
        run_id = runner(session, trigger=trigger, profile_id=profile_id)
    except Exception as error:
        session.rollback()
        message = f"{type(error).__name__}: {error}"[:MAX_ERROR_LENGTH]
        logger.exception("The %s run failed", trigger)
        _record_failure_if_no_run(session, trigger, started, clock(), message)
        result = TickResult(action, error=message)
    else:
        logger.info("Run %s finished", run_id)
        result = TickResult(action, run_id=run_id)

    finished = clock()
    if request is not None:
        _close_request(request, finished, error=result.error)
    write_heartbeat(
        session,
        finished,
        hostname=hostname,
        state=WorkerState.IDLE,
        next_run_at=next_scheduled_at(finished, schedule),
    )
    session.commit()
    return result


def _record_failure_if_no_run(session: Session, trigger: str, started: datetime, now: datetime, message: str):
    """The pipeline records its own failures; this covers a run that died before it started."""
    recorded = session.scalar(
        select(Run.id).where(Run.trigger == trigger, Run.started_at >= started - START_TOLERANCE).limit(1)
    )
    if recorded is not None:
        return
    run = Run(trigger=trigger, status=RunStatus.RUNNING, started_at=started, heartbeat_at=started)
    session.add(run)
    session.flush()
    finish_run(session, run, now=now, error=message)


def _close_request(request: RunRequest, now: datetime, *, error: str | None) -> None:
    request.status = RunRequestStatus.FAILED if error else RunRequestStatus.DONE
    request.finished_at = now
    request.error = error


def _todays_slot(now: datetime, schedule: Schedule) -> datetime:
    local_now = now.astimezone(schedule.tz)
    return datetime.combine(local_now.date(), schedule.at, tzinfo=schedule.tz)


# --- Staying visible ----------------------------------------------------------------------


def write_heartbeat(
    session: Session,
    now: datetime,
    *,
    hostname: str,
    state: str,
    current_run_id: int | None = None,
    next_run_at: datetime | None = None,
) -> WorkerStatus:
    status = session.get(WorkerStatus, WORKER_ROW_ID)
    if status is None:
        status = WorkerStatus(id=WORKER_ROW_ID, started_at=now)
        session.add(status)
    status.hostname = hostname
    status.state = state
    status.heartbeat_at = now
    status.current_run_id = current_run_id
    status.next_scheduled_at = next_run_at
    session.flush()
    return status


def beat(session: Session, now: datetime) -> None:
    """Keep a long run looking alive: the runner's check-in and every running run's heartbeat."""
    status = session.get(WorkerStatus, WORKER_ROW_ID)
    if status is not None:
        status.heartbeat_at = now
    for run in session.scalars(select(Run).where(Run.status == RunStatus.RUNNING)).all():
        run.heartbeat_at = now
    session.flush()


@contextmanager
def keep_checking_in(engine: Engine, every: float = BEAT_SECONDS) -> Iterator[None]:
    """Beat from a background thread, on its own connection, for as long as a run works."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(every):
            try:
                with Session(engine) as session:
                    beat(session, datetime.now(UTC))
                    session.commit()
            except Exception:
                logger.warning("Couldn't refresh the heartbeat; will try again", exc_info=True)

    thread = threading.Thread(target=loop, name="noble-hunter-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=every)


def recover_abandoned(session: Session, now: datetime, *, stale_after: timedelta = STALE_RUN_AFTER) -> int:
    """Close out work a crashed or killed runner left open. Returns how many rows were closed.

    Anything that hasn't beaten for `stale_after` counts as abandoned.
    """
    cutoff = now - stale_after
    stale_runs = session.scalars(
        select(Run).where(Run.status == RunStatus.RUNNING, Run.heartbeat_at <= cutoff)
    ).all()
    for run in stale_runs:
        finish_run(
            session,
            run,
            now=now,
            error="The runner stopped mid-run (the Mac slept, restarted or lost power).",
        )
    stale_requests = session.scalars(
        select(RunRequest).where(
            RunRequest.status == RunRequestStatus.CLAIMED, RunRequest.claimed_at <= cutoff
        )
    ).all()
    for request in stale_requests:
        _close_request(request, now, error="The runner stopped before this run finished.")
    session.flush()
    return len(stale_runs) + len(stale_requests)


def recover_at_startup(session: Session, engine: Engine, now: datetime) -> int:
    """Close what an earlier runner left open when it was killed.

    A killed run's heartbeat can be seconds old. But if no runner holds the run lock, nothing can
    really be running, so everything still marked as running is closed now, not ten minutes later.
    """
    with run_lock(engine) as nobody_running:
        stale_after = timedelta(0) if nobody_running else STALE_RUN_AFTER
        return recover_abandoned(session, now, stale_after=stale_after)


@contextmanager
def run_lock(engine: Engine) -> Iterator[bool]:
    """A Postgres advisory lock, held on its own connection for as long as the run lasts.

    If the Mac loses its connection the lock goes with it, so a dead runner can't block the next one.
    """
    with engine.connect() as connection:
        acquired = bool(connection.scalar(text("select pg_try_advisory_lock(:key)"), {"key": RUN_LOCK_KEY}))
        connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("select pg_advisory_unlock(:key)"), {"key": RUN_LOCK_KEY})
                connection.commit()


# --- The real thing -----------------------------------------------------------------------


class PipelineRunner:
    """Take the lock, keep checking in, open search and Spotify, and run the pipeline with its stages."""

    def __init__(
        self,
        engine: Engine,
        *,
        open_http: Callable[[], AbstractContextManager],
        open_spotify: Callable[[], AbstractContextManager],
        providers_for: Callable[[object], list],
        stages_for: Callable[[object], list],
    ):
        self.engine = engine
        self.open_http = open_http
        self.open_spotify = open_spotify
        self.providers_for = providers_for
        self.stages_for = stages_for

    def __call__(self, session: Session, *, trigger: str, profile_id: int | None) -> int:
        with run_lock(self.engine) as acquired:
            if not acquired:
                raise RunInProgress("Another run is already in progress")
            with keep_checking_in(self.engine), self.open_http() as http:
                stages = self.stages_for(http)  # before Chromium starts, so a missing key fails fast
                with self.open_spotify() as spotify:
                    now = datetime.now(UTC)
                    report = run_pipeline(
                        session,
                        providers=self.providers_for(http),
                        fetcher=spotify,
                        trigger=trigger,
                        today=now.date(),
                        now=now,
                        profile_id=profile_id,
                        stages=stages,
                    )
        logger.info("%s", describe_run(report))
        return report.run_id


def run_forever(
    engine: Engine,
    runner: Runner,
    *,
    stop: threading.Event,
    hostname: str | None = None,
    poll_seconds: float = POLL_SECONDS,
) -> None:
    hostname = hostname or socket.gethostname()
    with Session(engine) as session:
        closed = recover_at_startup(session, engine, datetime.now(UTC))
        session.commit()
    if closed:
        logger.warning("Closed %s run(s) or request(s) left open by an earlier runner", closed)
    logger.info("Runner started on %s; the nightly run starts at %s", hostname, NIGHTLY_AT.strftime("%H:%M"))

    while not stop.is_set():
        try:
            with Session(engine) as session:
                tick(
                    session,
                    runner=runner,
                    clock=lambda: datetime.now(UTC),
                    hostname=hostname,
                    schedule=local_schedule(),
                )
        except Exception:
            # Usually the network or the database is briefly away (e.g. just after the Mac wakes).
            logger.exception("This check failed; trying again in %s seconds", poll_seconds)
        stop.wait(poll_seconds)
    logger.info("Runner stopped")


def configure_logging(directory: Path) -> Path:
    """Log to a rotating file, so months of nights never fill the disk."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "worker.log"
    handler = RotatingFileHandler(path, maxBytes=LOG_FILE_BYTES, backupCount=LOG_FILE_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return path
