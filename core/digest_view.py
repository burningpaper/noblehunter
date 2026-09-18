"""The digest page's view of a night: entries grouped by profile, ready to pitch, and the night's counts.

The web app runs on Vercel and never imports pipeline code, so everything the page needs is
read here, straight from the tables the pipeline wrote. A digest is identified by its date. The
page shows the latest by default and can step back through earlier nights. Entries keep the
order the pipeline ranked them in, grouped under their profile, and groups are keyed on the
artist's id, not its name, because names are unique only case-sensitively.

Every query that lists digest entries and nights is scoped to the viewer with `visible_to`: a
member sees only their own artists' entries and nights, and never learns whether another artist
even has a digest. Admins see everything, and `show_artists` tells the template to label each
group so it's clear whose profile it is.

The night's counts are different: they total every artist's pipeline activity, not just the
viewer's own, so `digest_view` computes them for admins only (the template's `is_admin` check
is a second guard, not the only one). They come from the run that produced that night's digest:
the latest run that started within that date (with a margin for the Mac's time zone).
"""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import ColumnElement, func, select, true
from sqlalchemy.orm import Session

from core.access import Viewer, visible_to
from core.contact_routes import ROUTE_LABELS, best_contact, contact_href
from core.conversations import loads_for
from core.models import (
    Artist,
    Curator,
    EmailMessage,
    MailDirection,
    Outreach,
    Playlist,
    PlaylistProfileFit,
    Profile,
    Run,
    RunStageCount,
)

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
class EntryMail:
    """What has passed between this entry and its curator. Derived, never a status column."""

    sent: int = 0
    received: int = 0
    last_direction: str | None = None
    last_at: datetime | None = None

    @property
    def waiting_on_you(self) -> bool:
        return self.last_direction == MailDirection.IN

    @property
    def started(self) -> bool:
        return bool(self.sent or self.received)


NO_MAIL = EntryMail()


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
    mail: EntryMail = NO_MAIL


@dataclass(frozen=True)
class ProfileDigest:
    artist_name: str
    profile_name: str
    entries: tuple[EntryView, ...]
    # How full the profile's inbox is tonight, which is what decided the size of this group.
    open_conversations: int = 0
    conversation_limit: int = 0


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
    # (id, name) of every profile that could be filtered to, and the one that was.
    filter_profiles: tuple[tuple[int, str], ...] = ()
    chosen_profile_id: int | None = None

    @property
    def total(self) -> int:
        return sum(len(group.entries) for group in self.profiles)

    @property
    def short(self) -> bool:
        """Fewer than 10 entries, which the page says plainly (spec §3.5)."""
        return 0 < self.total < SHORT_DIGEST

    @property
    def path(self) -> str:
        """This page's own path: the dated one, or /digest when it's showing the latest night."""
        return f"/digest/{self.digest_date.isoformat()}" if self.digest_date else "/digest"

    @property
    def filter_query(self) -> str:
        """The filter as a query string, for every link that stays on the digest.

        Without it, stepping back a night would quietly clear the filter: the reader narrows to
        one profile, clicks to yesterday, and everything they filtered out is back with no
        explanation. A link that leaves this page carries the filter or it loses their place.
        """
        return f"?profile={self.chosen_profile_id}" if self.chosen_profile_id else ""


def digest_view(
    session: Session,
    digest_date: date | None,
    *,
    today: date,
    viewer: Viewer,
    now: datetime | None = None,
    profile_id: int | None = None,
) -> DigestView:
    """One night's entries for one viewer, and the nights either side of it.

    `profile_id` narrows the page to a single profile: its entries, and its own nights. The
    dates follow the filter deliberately -- offering a night this profile never ran would step
    the reader onto an empty page with nothing to explain it. "Latest" becomes that profile's
    own latest night for the same reason. The caller checks the id with `require_profile`
    first, so another artist's id is a 404 long before it reaches these queries.
    """
    # `now` decides which conversations still count as open. It defaults rather than being
    # required because every caller already passes `today` and none of them has a clock to hand;
    # a test that needs a fixed one supplies it.
    dates = list(
        session.scalars(
            select(Outreach.digest_date)
            .join(Profile, Profile.id == Outreach.profile_id)
            .where(visible_to(viewer, Profile.artist_id), _on_profile(profile_id))
            .distinct()
            .order_by(Outreach.digest_date.desc())
        )
    )
    show_artists = viewer.is_admin or len(viewer.artist_ids) > 1
    filter_profiles = _filter_profiles(session, viewer)
    chosen = digest_date if digest_date is not None else (dates[0] if dates else None)
    if chosen is None:
        return DigestView(
            digest_date=None,
            profiles=(),
            counts=(),
            earlier_date=None,
            later_date=None,
            show_artists=show_artists,
            filter_profiles=filter_profiles,
            chosen_profile_id=profile_id,
        )

    return DigestView(
        digest_date=chosen,
        profiles=_profiles(session, chosen, today, viewer, now or datetime.now(UTC), profile_id),
        counts=_counts(session, chosen) if viewer.is_admin else (),
        earlier_date=next((day for day in dates if day < chosen), None),
        later_date=next((day for day in reversed(dates) if day > chosen), None),
        show_artists=show_artists,
        filter_profiles=filter_profiles,
        chosen_profile_id=profile_id,
    )


def _on_profile(profile_id: int | None) -> ColumnElement[bool]:
    """A WHERE clause narrowing to one profile, or one that narrows nothing when there's no filter."""
    return true() if profile_id is None else Outreach.profile_id == profile_id


def _filter_profiles(session: Session, viewer: Viewer) -> tuple[tuple[int, str], ...]:
    """Every profile the viewer could filter to: one with entries on some night, not just tonight.

    Deliberately narrowed by neither the chosen night nor the current filter. The row has to
    hold still as the reader steps between nights, and it has to offer the way back from a
    night the chosen profile happened to miss.
    """
    rows = session.execute(
        select(Profile.id, Profile.name)
        .join(Outreach, Outreach.profile_id == Profile.id)
        .where(visible_to(viewer, Profile.artist_id))
        .distinct()
    )
    profiles = [(row_id, name) for row_id, name in rows]
    return tuple(sorted(profiles, key=lambda profile: profile[1].casefold()))


def entry_view(session: Session, outreach_id: int, *, today: date) -> EntryView:
    row = session.execute(_entry_rows().where(Outreach.id == outreach_id)).first()
    if row is None:
        raise LookupError(f"Outreach {outreach_id} not found")
    mail = _mail_by_entry(session, [outreach_id])
    return _entry(session, *row, today=today, mail=mail.get(outreach_id, NO_MAIL))


def _profiles(
    session: Session, chosen: date, today: date, viewer: Viewer, now: datetime, profile_id: int | None
) -> tuple[ProfileDigest, ...]:
    # Keyed on ids, not names: artist names are unique only case-sensitively, and two artists
    # could otherwise collide here even though `visible_to` and the profiles page never confuse
    # them. Rows arrive in pipeline ranking order (Outreach.id), and dict insertion order plus a
    # stable sort keep that order within one artist once groups are sorted by artist name.
    groups: dict[tuple[int, str, int], tuple[str, list[EntryView]]] = {}
    profiles: dict[int, Profile] = {}
    rows = list(
        session.execute(
            _entry_rows()
            .join(Artist, Artist.id == Profile.artist_id)
            .add_columns(Artist.id, Artist.name)
            .where(
                Outreach.digest_date == chosen,
                visible_to(viewer, Profile.artist_id),
                _on_profile(profile_id),
            )
            .order_by(Outreach.id)
        )
    )
    # Read once for the whole night: a query per entry would cost twenty statements on a page load.
    mail = _mail_by_entry(session, [outreach.id for outreach, *_rest in rows])
    for outreach, profile, playlist, curator, artist_id, artist_name in rows:
        key = (artist_id, artist_name, profile.id)
        profiles[profile.id] = profile
        groups.setdefault(key, (profile.name, []))[1].append(
            _entry(
                session,
                outreach,
                profile,
                playlist,
                curator,
                today=today,
                mail=mail.get(outreach.id, NO_MAIL),
            )
        )
    # Artists in name order (case-insensitive, ties broken by id); within one artist, profiles
    # keep the pipeline's ranking order because sorted() is stable and shares that group's key.
    ordered = sorted(groups.items(), key=lambda item: (item[0][1].casefold(), item[0][0]))
    # Read once for the whole night, like the mail above: the same figures the pipeline worked to.
    loads = loads_for(session, list(profiles.values()), now=now)
    return tuple(
        ProfileDigest(
            artist_name,
            profile_name,
            tuple(entries),
            open_conversations=loads[profile_id].open_now,
            conversation_limit=loads[profile_id].limit,
        )
        for (_artist_id, artist_name, profile_id), (profile_name, entries) in ordered
    )


def _mail_by_entry(session: Session, outreach_ids: list[int]) -> dict[int, EntryMail]:
    """Every entry's mail state for the whole night. Entries with no mail aren't in the result.

    Two queries, never one per entry: the counts, then who spoke last. DISTINCT ON and a window
    function could fold them into one, and deliberately don't -- two plain queries read better
    than one clever one, and the thing worth preventing is a query per row either way.
    """
    if not outreach_ids:
        return {}
    rows = session.execute(
        select(
            EmailMessage.outreach_id,
            func.count().filter(EmailMessage.direction == MailDirection.OUT),
            func.count().filter(EmailMessage.direction == MailDirection.IN),
            func.max(EmailMessage.sent_at),
        )
        .where(EmailMessage.outreach_id.in_(outreach_ids))
        .group_by(EmailMessage.outreach_id)
    )
    state = {
        outreach_id: EntryMail(sent=sent, received=received, last_at=last_at)
        for outreach_id, sent, received, last_at in rows
    }
    for outreach_id, direction in session.execute(
        select(EmailMessage.outreach_id, EmailMessage.direction)
        .where(EmailMessage.outreach_id.in_(outreach_ids))
        .order_by(EmailMessage.outreach_id, EmailMessage.sent_at.desc(), EmailMessage.id.desc())
        .distinct(EmailMessage.outreach_id)
    ):
        state[outreach_id] = replace(state[outreach_id], last_direction=direction)
    return state


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
    mail: EntryMail = NO_MAIL,
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
        mail=mail,
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
