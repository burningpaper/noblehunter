"""The digest: tonight's best reachable curators, one each, with a brief to pitch from.

A playlist is considered only if four things are true: it's qualified, it belongs to an active
profile, its curator is eligible (not excluded, not in a digest in the last 90 days), and its
curator is reachable (an A or B contact). Candidates are ranked across all profiles with the
spec's weighted sum: fit first, then contact confidence, how recently the playlist moved, and
size. Fit is measured against the night's best candidate, so the ranking doesn't depend on
the scale of the fit score. A curator appears once, under the profile they fit best, and each
profile takes at most its digest target.

Each entry gets a brief from Claude. If Claude can't write one, a plain template brief is used
instead, so a flaky API never costs Jarred his morning list. Entries are committed one at a
time and their playlists marked `digested`, with the outreach table's 90-day constraint as
the backstop. Running again the same day only tops the digest up.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.exclusion import curator_is_eligible
from core.models import (
    Confidence,
    Contact,
    Outreach,
    OutreachStatus,
    Playlist,
    PlaylistProfileFit,
    PlaylistStatus,
    Profile,
    Run,
)
from pipeline.runs import record_stage

logger = logging.getLogger("noble_hunter.digest")

PLAYLIST_URL = "https://open.spotify.com/playlist/{}"
WEIGHT_FIT = 0.5
WEIGHT_CONTACT = 0.25
WEIGHT_RECENCY = 0.15
WEIGHT_SIZE = 0.10
CONTACT_SCORES = {Confidence.A: 1.0, Confidence.B: 0.6}
# Mid-sized playlists are the sweet spot: big enough to matter, small enough to read their inbox.
SIZE_SCORES = {"under-500": 0.4, "500-2k": 0.8, "2k-10k": 1.0, "10k-plus": 0.7}
UNKNOWN_SIZE_SCORE = 0.5
RECENCY_WINDOW_DAYS = 60
ROUTE_PREFERENCE = ("email", "submission-form", "instagram", "x", "bluesky", "other")
ROUTE_LABELS = {
    "email": "email",
    "submission-form": "their submission form",
    "instagram": "Instagram DM",
    "x": "X DM",
    "bluesky": "Bluesky DM",
    "other": "their stated route",
}
MAX_QUOTE_LENGTH = 200
MAX_ERROR_LENGTH = 300


@dataclass(frozen=True)
class BriefRequest:
    profile_name: str
    playlist_name: str
    playlist_url: str
    description: str
    curator_name: str
    followers: int | None
    size_band: str | None
    days_since_last_add: int | None
    reference_artists: tuple[str, ...]
    tracks: tuple[tuple[str, str], ...]  # (title, one line on how it sounds)
    contact_route_type: str
    contact_value: str
    contact_note: str


@dataclass(frozen=True)
class Brief:
    text: str
    angle: str
    spend_usd: Decimal = Decimal(0)


class BriefWriter(Protocol):
    def write(self, request: BriefRequest) -> Brief: ...


@dataclass
class DigestSummary:
    entries: int = 0
    template_briefs: int = 0
    spend_usd: Decimal = Decimal(0)
    per_profile: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Candidate:
    playlist: Playlist
    profile: Profile
    fit: PlaylistProfileFit
    contact: Contact
    score: float


def build_digest(
    session: Session, *, writer: BriefWriter | None, run: Run, today: date, now: datetime
) -> DigestSummary:
    summary = DigestSummary()
    candidates = _ranked_candidates(session, today, now)
    taken = _entries_already_today(session, today)
    used_curators: set[int] = set()

    for candidate in candidates:
        playlist, profile = candidate.playlist, candidate.profile
        if playlist.curator_id in used_curators or taken.get(profile.id, 0) >= profile.digest_target:
            continue
        brief = _write_brief(writer, _brief_request(candidate, now), summary)
        if not _add_entry(session, candidate, brief, today):
            continue
        playlist.status = PlaylistStatus.DIGESTED
        used_curators.add(playlist.curator_id)
        taken[profile.id] = taken.get(profile.id, 0) + 1
        summary.entries += 1
        summary.per_profile[profile.name] = summary.per_profile.get(profile.name, 0) + 1
        session.commit()

    run.llm_spend_usd = (run.llm_spend_usd or Decimal(0)) + summary.spend_usd
    record_stage(session, run, "digest", count_in=len(candidates), count_out=summary.entries)
    session.commit()
    return summary


def template_brief(request: BriefRequest) -> Brief:
    """A plain brief from the facts alone, for when Claude can't write one."""
    artists = _join(request.reference_artists) or "artists close to this music"
    size = f"{request.followers:,} followers" if request.followers is not None else "followers unknown"
    parts = [
        f"{request.curator_name or 'The curator'} runs “{request.playlist_name}” "
        f"({size}, {_activity(request)}).",
        f"It already features {artists}.",
    ]
    if request.description:
        parts.append(f"In their words: “{_clip(request.description)}”")
    route = ROUTE_LABELS.get(request.contact_route_type, request.contact_route_type)
    parts.append(f"Reach them by {route}: {request.contact_value}.")

    if request.tracks and request.reference_artists:
        angle = f"Lead with {request.tracks[0][0]} and how it sits next to {request.reference_artists[0]}."
    else:
        angle = "Lead with the track that sits closest to the artists already on the playlist."
    return Brief(text=" ".join(parts), angle=angle)


# --- Choosing -----------------------------------------------------------------------------


def _ranked_candidates(session: Session, today: date, now: datetime) -> list[_Candidate]:
    rows = session.execute(
        select(Playlist, Profile, PlaylistProfileFit)
        .join(PlaylistProfileFit, PlaylistProfileFit.playlist_id == Playlist.spotify_id)
        .join(Profile, Profile.id == PlaylistProfileFit.profile_id)
        .where(
            Playlist.status == PlaylistStatus.QUALIFIED,
            Playlist.curator_id.is_not(None),
            PlaylistProfileFit.qualified.is_(True),
            Profile.is_active.is_(True),
        )
    )
    eligible: dict[int, bool] = {}
    reachable = []
    for playlist, profile, fit in rows:
        curator_id = playlist.curator_id
        if curator_id not in eligible:
            eligible[curator_id] = curator_is_eligible(session, curator_id, today)
        contact = _best_contact(session, curator_id) if eligible[curator_id] else None
        if contact is not None:
            reachable.append((playlist, profile, fit, contact))

    best_fit = max((fit.fit_score for _, _, fit, _ in reachable), default=0) or 1
    ranked = [
        _Candidate(playlist, profile, fit, contact, _score(playlist, fit, contact, best_fit, now))
        for playlist, profile, fit, contact in reachable
    ]
    ranked.sort(key=lambda candidate: (-candidate.score, candidate.playlist.spotify_id))
    return ranked


def _best_contact(session: Session, curator_id: int) -> Contact | None:
    contacts = session.scalars(
        select(Contact).where(Contact.curator_id == curator_id, Contact.confidence.in_(CONTACT_SCORES))
    ).all()
    return min(contacts, key=_contact_preference, default=None)


def _contact_preference(contact: Contact) -> tuple:
    route_rank = ROUTE_PREFERENCE.index(contact.route_type) if contact.route_type in ROUTE_PREFERENCE else 99
    return (-CONTACT_SCORES[contact.confidence], route_rank, contact.id)


def _score(
    playlist: Playlist, fit: PlaylistProfileFit, contact: Contact, best_fit: float, now: datetime
) -> float:
    return (
        WEIGHT_FIT * (fit.fit_score / best_fit)
        + WEIGHT_CONTACT * CONTACT_SCORES[contact.confidence]
        + WEIGHT_RECENCY * _recency(playlist.last_add_at, now)
        + WEIGHT_SIZE * SIZE_SCORES.get(playlist.size_band or "", UNKNOWN_SIZE_SCORE)
    )


def _recency(last_add_at: datetime | None, now: datetime) -> float:
    if last_add_at is None:
        return 0.0
    days = (now - last_add_at).total_seconds() / 86_400
    return max(0.0, 1 - days / RECENCY_WINDOW_DAYS)


def _entries_already_today(session: Session, today: date) -> dict[int, int]:
    rows = session.execute(
        select(Outreach.profile_id, func.count())
        .where(Outreach.digest_date == today)
        .group_by(Outreach.profile_id)
    )
    return {profile_id: entries for profile_id, entries in rows}


# --- Writing ------------------------------------------------------------------------------


def _brief_request(candidate: _Candidate, now: datetime) -> BriefRequest:
    playlist, profile, fit, contact = candidate.playlist, candidate.profile, candidate.fit, candidate.contact
    days = None if playlist.last_add_at is None else max(0, (now - playlist.last_add_at).days)
    curator_name = playlist.curator.display_name if playlist.curator else (playlist.owner_name or "")
    return BriefRequest(
        profile_name=profile.name,
        playlist_name=playlist.name,
        playlist_url=PLAYLIST_URL.format(playlist.spotify_id),
        description=playlist.description or "",
        curator_name=curator_name,
        followers=playlist.followers,
        size_band=playlist.size_band,
        days_since_last_add=days,
        reference_artists=tuple(fit.reference_artists_present or ()),
        tracks=tuple(
            (track.title, track.description or "") for track in sorted(profile.tracks, key=_by_title)
        ),
        contact_route_type=contact.route_type,
        contact_value=contact.value,
        contact_note=contact.notes or "",
    )


def _write_brief(writer: BriefWriter | None, request: BriefRequest, summary: DigestSummary) -> Brief:
    if writer is not None:
        try:
            brief = writer.write(request)
        except Exception as error:
            logger.warning(
                "Couldn't get a brief for %s; using a plain one", request.playlist_url, exc_info=True
            )
            summary.errors.append(
                f"{request.playlist_name}: {type(error).__name__}: {error}"[:MAX_ERROR_LENGTH]
            )
        else:
            summary.spend_usd += brief.spend_usd
            return brief
    summary.template_briefs += 1
    return template_brief(request)


def _add_entry(session: Session, candidate: _Candidate, brief: Brief, today: date) -> bool:
    entry = Outreach(
        curator_id=candidate.playlist.curator_id,
        playlist_id=candidate.playlist.spotify_id,
        profile_id=candidate.profile.id,
        digest_date=today,
        brief_text=brief.text,
        suggested_angle=brief.angle or None,
        status=OutreachStatus.NEW,
    )
    try:
        with session.begin_nested():
            session.add(entry)
    except IntegrityError:
        # The 90-day constraint said no; the choosing above should have prevented this.
        logger.warning("Outreach refused for curator %s; skipping", candidate.playlist.curator_id)
        return False
    return True


def _activity(request: BriefRequest) -> str:
    days = request.days_since_last_add
    if days is None:
        return "activity unknown"
    if days == 0:
        return "updated today"
    return f"updated {days} {'day' if days == 1 else 'days'} ago"


def _join(names: Sequence[str]) -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _clip(text: str) -> str:
    return text if len(text) <= MAX_QUOTE_LENGTH else text[: MAX_QUOTE_LENGTH - 1].rstrip() + "…"


def _by_title(track) -> str:
    return track.title.casefold()
