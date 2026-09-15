"""The digest page's view of a night: entries grouped by profile, ready to pitch, and the night's counts.

The web app runs on Vercel and never imports pipeline code, so everything the page needs is
read here, straight from the tables the pipeline wrote. A digest is identified by its date. The
page shows the latest by default and can step back through earlier nights. Entries keep the
order the pipeline ranked them in, grouped under their profile, and groups are keyed on the
artist's id, not its name, because names are unique only case-sensitively.

Every query here is scoped to the viewer with `visible_to`: a member sees only their own
artists' entries and nights, and never learns whether another artist even has a digest. Admins
see everything, and `show_artists` tells the template to label each group so it's clear whose
profile it is.

The counts come from the run that produced that night's digest: the latest run that started
within that date (with a margin for the Mac's time zone). They total every artist's pipeline
activity, so the page shows them to admins only.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.access import Viewer, visible_to
from core.contact_routes import ROUTE_LABELS, best_contact, contact_href
from core.models import Artist, Curator, Outreach, Playlist, PlaylistProfileFit, Profile, Run, RunStageCount

PLAYLIST_URL = "https://open.spotify.com/playlist/{}"
SHORT_DIGEST = 10
RUN_WINDOW_BEFORE_MIDNIGHT = timedelta(hours=12)
STAGE_LABELS = (
    ("discover", "Found by search"),
    ("fetch", "Fetched from Spotify"),
    ("qualify", "Qualified"),
    ("research", "Contact researched"),
    ("digest", "In the digest"),
)


@dataclass(frozen=True)
class EntryView:
    outreach_id: int
    status: str
    playlist_name: str
    playlist_url: str
    curator_name: str
    followers: int | None
    size_band: str | None
    days_since_last_add: int | None
    route_type: str | None
    route_label: str | None
    contact_value: str | None
    contact_href: str | None
    confidence: str | None
    brief: str
    angle: str | None
    reference_artists: tuple[str, ...]


@dataclass(frozen=True)
class ProfileDigest:
    artist_name: str
    profile_name: str
    entries: tuple[EntryView, ...]


@dataclass(frozen=True)
class NightCount:
    label: str
    count_in: int
    count_out: int


@dataclass(frozen=True)
class DigestView:
    digest_date: date | None
    profiles: tuple[ProfileDigest, ...]
    counts: tuple[NightCount, ...]
    earlier_date: date | None
    later_date: date | None
    show_artists: bool = False

    @property
    def total(self) -> int:
        return sum(len(group.entries) for group in self.profiles)

    @property
    def short(self) -> bool:
        """Fewer than 10 entries, which the page says plainly (spec §3.5)."""
        return 0 < self.total < SHORT_DIGEST


def digest_view(session: Session, digest_date: date | None, *, today: date, viewer: Viewer) -> DigestView:
    dates = list(
        session.scalars(
            select(Outreach.digest_date)
            .join(Profile, Profile.id == Outreach.profile_id)
            .where(visible_to(viewer, Profile.artist_id))
            .distinct()
            .order_by(Outreach.digest_date.desc())
        )
    )
    show_artists = viewer.is_admin or len(viewer.artist_ids) > 1
    chosen = digest_date if digest_date is not None else (dates[0] if dates else None)
    if chosen is None:
        return DigestView(
            digest_date=None,
            profiles=(),
            counts=(),
            earlier_date=None,
            later_date=None,
            show_artists=show_artists,
        )

    return DigestView(
        digest_date=chosen,
        profiles=_profiles(session, chosen, today, viewer),
        counts=_counts(session, chosen),
        earlier_date=next((day for day in dates if day < chosen), None),
        later_date=next((day for day in reversed(dates) if day > chosen), None),
        show_artists=show_artists,
    )


def entry_view(session: Session, outreach_id: int, *, today: date) -> EntryView:
    row = session.execute(_entry_rows().where(Outreach.id == outreach_id)).first()
    if row is None:
        raise LookupError(f"Outreach {outreach_id} not found")
    return _entry(session, *row, today=today)


def _profiles(session: Session, chosen: date, today: date, viewer: Viewer) -> tuple[ProfileDigest, ...]:
    # Keyed on ids, not names: artist names are unique only case-sensitively, and two artists
    # could otherwise collide here even though `visible_to` and the profiles page never confuse
    # them. Rows arrive in pipeline ranking order (Outreach.id), and dict insertion order plus a
    # stable sort keep that order within one artist once groups are sorted by artist name.
    groups: dict[tuple[int, str, int], tuple[str, list[EntryView]]] = {}
    rows = session.execute(
        _entry_rows()
        .join(Artist, Artist.id == Profile.artist_id)
        .add_columns(Artist.id, Artist.name)
        .where(Outreach.digest_date == chosen, visible_to(viewer, Profile.artist_id))
        .order_by(Outreach.id)
    )
    for outreach, profile, playlist, curator, artist_id, artist_name in rows:
        key = (artist_id, artist_name, profile.id)
        groups.setdefault(key, (profile.name, []))[1].append(
            _entry(session, outreach, profile, playlist, curator, today=today)
        )
    # Artists in name order (case-insensitive, ties broken by id); within one artist, profiles
    # keep the pipeline's ranking order because sorted() is stable and shares that group's key.
    ordered = sorted(groups.items(), key=lambda item: (item[0][1].casefold(), item[0][0]))
    return tuple(
        ProfileDigest(artist_name, profile_name, tuple(entries))
        for (_artist_id, artist_name, _profile_id), (profile_name, entries) in ordered
    )


def _entry_rows():
    return (
        select(Outreach, Profile, Playlist, Curator)
        .join(Profile, Profile.id == Outreach.profile_id)
        .join(Playlist, Playlist.spotify_id == Outreach.playlist_id)
        .join(Curator, Curator.id == Outreach.curator_id)
    )


def _entry(
    session: Session,
    outreach: Outreach,
    profile: Profile,
    playlist: Playlist,
    curator: Curator,
    *,
    today: date,
) -> EntryView:
    contact = best_contact(session, curator.id)
    fit = session.get(PlaylistProfileFit, (playlist.spotify_id, profile.id))
    return EntryView(
        outreach_id=outreach.id,
        status=outreach.status,
        playlist_name=playlist.name,
        playlist_url=PLAYLIST_URL.format(playlist.spotify_id),
        curator_name=curator.display_name,
        followers=playlist.followers,
        size_band=playlist.size_band,
        days_since_last_add=_days_since(playlist.last_add_at, today),
        route_type=contact.route_type if contact else None,
        route_label=ROUTE_LABELS.get(contact.route_type, contact.route_type) if contact else None,
        contact_value=contact.value if contact else None,
        contact_href=contact_href(contact.route_type, contact.value) if contact else None,
        confidence=contact.confidence if contact else None,
        brief=outreach.brief_text,
        angle=outreach.suggested_angle,
        reference_artists=tuple(fit.reference_artists_present or ()) if fit else (),
    )


def _days_since(moment: datetime | None, today: date) -> int | None:
    if moment is None:
        return None
    return max(0, (today - moment.astimezone(UTC).date()).days)


def _counts(session: Session, chosen: date) -> tuple[NightCount, ...]:
    day_start = datetime.combine(chosen, time.min, tzinfo=UTC)
    run_id = session.scalar(
        select(Run.id)
        .where(
            Run.started_at >= day_start - RUN_WINDOW_BEFORE_MIDNIGHT,
            Run.started_at < day_start + timedelta(days=1),
        )
        .order_by(Run.started_at.desc())
        .limit(1)
    )
    if run_id is None:
        return ()
    rows = session.execute(
        select(RunStageCount.stage, func.sum(RunStageCount.count_in), func.sum(RunStageCount.count_out))
        .where(RunStageCount.run_id == run_id)
        .group_by(RunStageCount.stage)
    )
    totals = {stage: (int(count_in), int(count_out)) for stage, count_in, count_out in rows}
    return tuple(NightCount(label, *totals[stage]) for stage, label in STAGE_LABELS if stage in totals)
