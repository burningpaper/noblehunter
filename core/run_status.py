"""What the web app says about the Mac Mini runner, and how "Run now" asks it for a run.

The web app never runs the pipeline itself: a run takes far longer than a Vercel function is
allowed. "Run now" leaves a request in the database, and the runner on the Mac Mini picks it
up within a minute. The status panel reads the runner's check-in and the runs table, and says
plainly when something needs attention: a runner that has gone quiet (the Mac is asleep or
off), a run that failed, or a day with nothing finished.

Mailboxes get their own warnings, in `mail_warnings`, and not because the code reads better
that way: the warnings above are written for the admin and can name anyone's data, while a
mailbox warning has to name an address. So that one is scoped with `visible_to` and a member
never learns another artist's mailbox exists.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.access import Viewer, visible_to
from core.models import MailAccount, Run, RunRequest, RunRequestStatus, RunStatus, WorkerStatus

WORKER_ROW_ID = 1
RUNNER_OFFLINE_AFTER = timedelta(minutes=3)
NO_RUN_WARNING_AFTER = timedelta(hours=26)
MAIL_UNREAD_AFTER = timedelta(hours=6)
OPEN_REQUEST_STATUSES = (RunRequestStatus.PENDING, RunRequestStatus.CLAIMED)


@dataclass(frozen=True)
class RunStatusView:
    runner_online: bool
    runner_seen_at: datetime | None
    runner_state: str | None
    next_scheduled_at: datetime | None
    current_run: Run | None
    last_run: Run | None
    open_request: RunRequest | None
    warnings: tuple[str, ...]


def request_run(
    session: Session, *, requested_by: str, profile_id: int | None = None
) -> tuple[RunRequest, bool]:
    """Queue a run, or return the one already waiting for this scope. True if a new one was queued."""
    existing = _open_request(session, profile_id)
    if existing is not None:
        return existing, False

    request = RunRequest(requested_by=requested_by, profile_id=profile_id, status=RunRequestStatus.PENDING)
    try:
        with session.begin_nested():
            session.add(request)
    except IntegrityError:
        # Two clicks landed together; the partial unique index kept it to one request.
        return _open_request(session, profile_id), False
    return request, True


def run_status(session: Session, now: datetime) -> RunStatusView:
    worker = session.get(WorkerStatus, WORKER_ROW_ID)
    online = worker is not None and now - worker.heartbeat_at <= RUNNER_OFFLINE_AFTER
    current = session.scalar(
        select(Run).where(Run.status == RunStatus.RUNNING).order_by(Run.started_at.desc()).limit(1)
    )
    last = session.scalar(
        select(Run).where(Run.finished_at.is_not(None)).order_by(Run.finished_at.desc()).limit(1)
    )
    open_request = session.scalar(
        select(RunRequest)
        .where(RunRequest.status.in_(OPEN_REQUEST_STATUSES))
        .order_by(RunRequest.requested_at)
        .limit(1)
    )
    return RunStatusView(
        runner_online=online,
        runner_seen_at=worker.heartbeat_at if worker else None,
        runner_state=worker.state if worker else None,
        next_scheduled_at=worker.next_scheduled_at if worker else None,
        current_run=current,
        last_run=last,
        open_request=open_request,
        warnings=tuple(_warnings(worker, online, current, last, now)),
    )


MEMBER_WARNING = "Something is wrong with the nightly runs. An admin can see the details."


def member_warnings(warnings: tuple[str, ...]) -> tuple[str, ...]:
    """What a member may see of the admin's warnings.

    The real warnings are written for the admin and can name another artist's profile, a
    curator's email, or a search URL -- `last.error` is raw exception text, not something
    sanitised for sharing. A member gets a single generic line instead, or nothing at all
    when there's nothing wrong.
    """
    return (MEMBER_WARNING,) if warnings else ()


def mail_warnings(session: Session, viewer: Viewer, now: datetime) -> tuple[str, ...]:
    """What to say about this viewer's mailboxes: the two failures that are otherwise silent.

    Scoped with `visible_to`, so a member never learns another artist's mailbox address. These
    are deliberately separate from `run_status`'s warnings, which are written for the admin and
    can name anyone's data.
    """
    mailboxes = list(
        session.scalars(
            select(MailAccount).where(
                visible_to(viewer, MailAccount.artist_id),
                MailAccount.disconnected_at.is_(None),
                MailAccount.refresh_token_encrypted.is_not(None),
            )
        )
    )
    if not mailboxes:
        return ()

    warnings = [
        f"{mailbox.address} needs reconnecting before replies can be read or sent."
        for mailbox in mailboxes
        if mailbox.needs_reconnect
    ]
    latest = max((mailbox.last_checked_at for mailbox in mailboxes if mailbox.last_checked_at), default=None)
    if latest is None or now - latest > MAIL_UNREAD_AFTER:
        since = f"for {_span(now - latest)}" if latest else "yet"
        warnings.append(f"Replies haven't been read {since}. Is the Mac Mini on and awake?")
    return tuple(warnings)


def relative_time(moment: datetime | None, now: datetime) -> str:
    """ "12 minutes ago", "in 5 hours": readable anywhere, whatever time zone the server is in."""
    if moment is None:
        return "never"
    delta = now - moment
    if abs(delta) < timedelta(minutes=1):
        return "just now"
    span = _span(abs(delta))
    return f"in {span}" if delta < timedelta(0) else f"{span} ago"


def _warnings(worker, online: bool, current: Run | None, last: Run | None, now: datetime) -> list[str]:
    warnings = []
    if worker is None:
        warnings.append(
            "The runner on the Mac Mini has never checked in. Install it with scripts/install-worker.sh."
        )
    elif not online:
        warnings.append(
            f"The runner on the Mac Mini hasn't checked in for {_span(now - worker.heartbeat_at)}. "
            "Is the Mac on and awake?"
        )
    # Once a newer run is under way, an earlier failure is old news; the new run is what matters.
    newer_run_under_way = current is not None and last is not None and current.started_at >= last.started_at
    if last is not None and last.status == RunStatus.FAILED and not newer_run_under_way:
        warnings.append(f"The last run failed: {last.error or 'no reason was recorded'}")
    if current is None:
        if last is None:
            warnings.append("No run has finished yet.")
        elif now - last.finished_at > NO_RUN_WARNING_AFTER:
            hours = int(NO_RUN_WARNING_AFTER.total_seconds() // 3600)
            warnings.append(f"No run has finished in the last {hours} hours.")
    return warnings


def _open_request(session: Session, profile_id: int | None) -> RunRequest | None:
    same_scope = (
        RunRequest.profile_id.is_(None) if profile_id is None else RunRequest.profile_id == profile_id
    )
    return session.scalar(
        select(RunRequest)
        .where(same_scope, RunRequest.status.in_(OPEN_REQUEST_STATUSES))
        .order_by(RunRequest.id)
        .limit(1)
    )


def _span(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'' if hours == 1 else 's'}"
    return f"{hours // 24} days"
