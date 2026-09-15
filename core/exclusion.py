"""Who and what must not reach the digest again, and for how long.

The rules (Jarred, 2026-09-13):
- A curator appears at most once per 90 days, across every profile.
- `pitched` and `skip` just wait out those 90 days; `bad-fit` and `dead` exclude the
  curator permanently.
- `no-contact`, digested, not-alive and no-fit playlists are re-checked after 90 days.
  Pay-to-play and bot rejections (`not-real`) are permanent.

The 90-day curator rule is also enforced by an EXCLUDE constraint on `outreach`, so even
a bug here can't put the same curator in two digests inside the window.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from core.access import is_storable_id
from core.models import Contact, Curator, Outreach, OutreachStatus, Playlist, PlaylistStatus, RejectionReason

COOLDOWN_DAYS = 90

VERDICTS = {OutreachStatus.PITCHED, OutreachStatus.SKIP, OutreachStatus.BAD_FIT, OutreachStatus.DEAD}
PERMANENT_VERDICTS = {OutreachStatus.BAD_FIT, OutreachStatus.DEAD}

IN_PROGRESS_STATUSES = {PlaylistStatus.CANDIDATE, PlaylistStatus.QUALIFIED}
RECHECKABLE_STATUSES = {PlaylistStatus.NO_CONTACT, PlaylistStatus.DIGESTED}
RECHECKABLE_REJECTIONS = {RejectionReason.NOT_ALIVE, RejectionReason.NO_FIT, RejectionReason.TOO_SMALL}


def cooldown_start(today: date) -> date:
    """Digests on or before this date no longer block a curator."""
    return today - timedelta(days=COOLDOWN_DAYS)


def _curator_blocked(today: date) -> ColumnElement[bool]:
    recently_digested = (
        select(Outreach.id)
        .where(Outreach.curator_id == Curator.id, Outreach.digest_date > cooldown_start(today))
        .exists()
    )
    return or_(Curator.excluded_at.is_not(None), recently_digested)


def curator_is_eligible(session: Session, curator_id: int, today: date) -> bool:
    if session.get(Curator, curator_id) is None:
        raise LookupError(f"Curator {curator_id} not found")
    blocked = session.scalar(select(Curator.id).where(Curator.id == curator_id, _curator_blocked(today)))
    return blocked is None


def record_verdict(session: Session, outreach_id: int, verdict: str, now: datetime) -> Outreach:
    if verdict not in VERDICTS:
        allowed = ", ".join(sorted(VERDICTS))
        raise ValueError(f"Unknown verdict {verdict!r}; expected one of: {allowed}")

    if not is_storable_id(outreach_id):
        raise LookupError(f"Outreach {outreach_id} not found")
    outreach = session.get(Outreach, outreach_id)
    if outreach is None:
        raise LookupError(f"Outreach {outreach_id} not found")

    outreach.status = verdict
    outreach.status_changed_at = now
    if verdict == OutreachStatus.PITCHED:
        outreach.pitched_at = now
    if verdict in PERMANENT_VERDICTS and outreach.curator.excluded_at is None:
        outreach.curator.excluded_at = now
        outreach.curator.exclusion_reason = verdict

    session.flush()
    return outreach


def playlist_ids_to_skip(session: Session, spotify_ids: Iterable[str], today: date) -> set[str]:
    """Of these candidate IDs, the ones discovery must not spend a fetch on today."""
    ids = set(spotify_ids)
    if not ids:
        return set()

    rows = session.execute(
        select(
            Playlist.spotify_id, Playlist.status, Playlist.rejection_reason, Playlist.last_checked_at
        ).where(Playlist.spotify_id.in_(ids))
    )
    return {row.spotify_id for row in rows if _should_skip(row, today)}


def _should_skip(row, today: date) -> bool:
    if row.status in IN_PROGRESS_STATUSES:
        return True
    if row.status == PlaylistStatus.FETCH_FAILED:
        return False

    recheckable = row.status in RECHECKABLE_STATUSES or (
        row.status == PlaylistStatus.REJECTED and row.rejection_reason in RECHECKABLE_REJECTIONS
    )
    if not recheckable:
        return True  # permanent rejections, and anything unexpected, stay out
    if row.last_checked_at is None:
        return False
    return row.last_checked_at.astimezone(UTC).date() > cooldown_start(today)


def contact_is_excluded(session: Session, contact_key: str, domain_key: str | None, today: date) -> bool:
    """True if this contact (or, for company email, its domain) belongs to a blocked curator."""
    matches = Contact.contact_key == contact_key
    if domain_key is not None:
        matches = or_(matches, Contact.domain_key == domain_key)

    statement = (
        select(Contact.id)
        .join(Curator, Contact.curator_id == Curator.id)
        .where(matches, _curator_blocked(today))
        .limit(1)
    )
    return session.scalar(statement) is not None
