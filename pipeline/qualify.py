"""Qualify: is this playlist alive, real, and the right room for a profile's music?

These are pure judgements on what Spotify told us, so they're easy to test and to tune:

- Alive: a track added in the last 60 days, and at least 3 adds in the last 180. Playlists that
  haven't moved in months are dead, whatever their follower count.
- Real: no pay-to-play wording ("submission fee", "guaranteed placement"), and no huge
  following on a handful of tracks. Merely mentioning SubmitHub is fine; plenty of honest
  curators use it.
- Fit, per profile: reference artists on the playlist (3+ is top tier, 1-2 acceptable), and
  any anti-signal artist or whole-word term is an outright rejection.

The order matters for the recorded reason: Spotify's own playlists first (never pitchable),
then pay-to-play (permanent), then dead (re-checked later), then no fit. A playlist with no
reference artists at all counts as no fit for now; the LLM genre match that could rescue it
arrives later, behind its own switch. Thresholds are deliberately plain constants, because
a week of looking at real verdicts will move them.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from core.models import AntiSignalKind, PlaylistStatus, Profile, RejectionReason, SizeBand
from core.text import normalize_text
from pipeline.spotify import PlaylistData, Track

SPOTIFY_OWNERS = frozenset({"spotify", "thesoundsofspotify"})

ALIVE_MAX_DAYS_SINCE_ADD = 60
ALIVE_WINDOW_DAYS = 180
ALIVE_MIN_ADDS_IN_WINDOW = 3

SUSPICIOUS_MIN_FOLLOWERS = 50_000
SUSPICIOUS_MAX_TRACKS = 20

TOP_TIER_REFERENCE_ARTISTS = 3
TIER_TOP, TIER_ACCEPTABLE, TIER_WEAK, TIER_REJECTED = "top", "acceptable", "weak", "rejected"
QUALIFYING_TIERS = frozenset({TIER_TOP, TIER_ACCEPTABLE})

PAY_TO_PLAY_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"submission\s+fees?",
        r"guarantee[ds]?\s+(?:placements?|streams|plays|adds?)",
        r"paid\s+placements?",
        r"promo(?:tion)?\s+packages?",
        r"pay\s+to\s+(?:get|be)\s+(?:added|placed|featured)",
    )
)
SIZE_BANDS = ((500, SizeBand.UNDER_500), (2_000, SizeBand.FROM_500_TO_2K), (10_000, SizeBand.FROM_2K_TO_10K))


@dataclass(frozen=True)
class ProfileRules:
    profile_id: int
    name: str
    reference_artists: tuple[str, ...] = ()
    anti_artists: tuple[str, ...] = ()
    anti_terms: tuple[str, ...] = ()

    @classmethod
    def from_profile(cls, profile: Profile) -> "ProfileRules":
        return cls(
            profile_id=profile.id,
            name=profile.name,
            reference_artists=tuple(artist.display_name for artist in profile.reference_artists),
            anti_artists=tuple(s.value for s in profile.anti_signals if s.kind == AntiSignalKind.ARTIST),
            anti_terms=tuple(s.value for s in profile.anti_signals if s.kind == AntiSignalKind.TERM),
        )


@dataclass(frozen=True)
class Liveness:
    alive: bool
    last_add_at: datetime | None
    adds_last_180_days: int


@dataclass(frozen=True)
class RealityCheck:
    real: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class FitCheck:
    tier: str
    score: float
    reference_artists_present: tuple[str, ...]
    anti_signals_found: tuple[str, ...]

    @property
    def qualifies(self) -> bool:
        return self.tier in QUALIFYING_TIERS


@dataclass(frozen=True)
class Verdict:
    status: str
    rejection_reason: str | None
    liveness: Liveness
    reality: RealityCheck
    fits: dict[int, FitCheck]
    size_band: str | None

    @property
    def qualified_profile_ids(self) -> tuple[int, ...]:
        if self.status != PlaylistStatus.QUALIFIED:
            return ()
        return tuple(sorted(profile_id for profile_id, fit in self.fits.items() if fit.qualifies))


def assess_liveness(tracks: Iterable[Track], today: date) -> Liveness:
    dates = [track.added_at for track in tracks if track.added_at is not None]
    if not dates:
        return Liveness(alive=False, last_add_at=None, adds_last_180_days=0)

    last_add_at = max(dates)
    window_start = today - timedelta(days=ALIVE_WINDOW_DAYS)
    recent_adds = sum(1 for added in dates if added.astimezone(UTC).date() >= window_start)
    days_since_add = (today - last_add_at.astimezone(UTC).date()).days
    alive = days_since_add <= ALIVE_MAX_DAYS_SINCE_ADD and recent_adds >= ALIVE_MIN_ADDS_IN_WINDOW
    return Liveness(alive=alive, last_add_at=last_add_at, adds_last_180_days=recent_adds)


def assess_reality(data: PlaylistData) -> RealityCheck:
    reasons = []
    for pattern in PAY_TO_PLAY_PATTERNS:
        match = pattern.search(data.description_text or "")
        if match:
            reasons.append(f"pay-to-play wording in the description: “{match.group(0)}”")
            break
    followers = data.followers or 0
    if followers >= SUSPICIOUS_MIN_FOLLOWERS and data.total_tracks <= SUSPICIOUS_MAX_TRACKS:
        reasons.append(f"followers out of proportion: {followers:,} followers on {data.total_tracks} tracks")
    return RealityCheck(real=not reasons, reasons=tuple(reasons))


def assess_fit(data: PlaylistData, rules: ProfileRules) -> FitCheck:
    playlist_artists = {normalize_text(artist) for track in data.tracks for artist in track.artists}
    present = tuple(
        sorted(
            (name for name in rules.reference_artists if normalize_text(name) in playlist_artists),
            key=normalize_text,
        )
    )
    anti_found = tuple(name for name in rules.anti_artists if normalize_text(name) in playlist_artists)
    anti_found += tuple(term for term in rules.anti_terms if _contains_phrase(data, term))

    if anti_found:
        return FitCheck(TIER_REJECTED, 0.0, present, anti_found)
    if len(present) >= TOP_TIER_REFERENCE_ARTISTS:
        tier = TIER_TOP
    elif present:
        tier = TIER_ACCEPTABLE
    else:
        tier = TIER_WEAK
    score = min(1.0, len(present) / TOP_TIER_REFERENCE_ARTISTS)
    return FitCheck(tier, score, present, ())


def size_band(followers: int | None) -> str | None:
    if followers is None:
        return None
    for upper_bound, band in SIZE_BANDS:
        if followers < upper_bound:
            return band
    return SizeBand.OVER_10K


def judge(data: PlaylistData, rules: Sequence[ProfileRules], today: date) -> Verdict:
    liveness = assess_liveness(data.tracks, today)
    reality = assess_reality(data)
    fits = {profile_rules.profile_id: assess_fit(data, profile_rules) for profile_rules in rules}
    reason = _rejection_reason(data, liveness, reality, fits)
    return Verdict(
        status=PlaylistStatus.REJECTED if reason else PlaylistStatus.QUALIFIED,
        rejection_reason=reason,
        liveness=liveness,
        reality=reality,
        fits=fits,
        size_band=size_band(data.followers),
    )


def _rejection_reason(
    data: PlaylistData, liveness: Liveness, reality: RealityCheck, fits: dict[int, FitCheck]
) -> str | None:
    if data.owner_id in SPOTIFY_OWNERS:
        return RejectionReason.SPOTIFY_OWNED
    if not reality.real:
        return RejectionReason.NOT_REAL
    if not liveness.alive:
        return RejectionReason.NOT_ALIVE
    if not any(fit.qualifies for fit in fits.values()):
        return RejectionReason.NO_FIT
    return None


def _contains_phrase(data: PlaylistData, phrase: str) -> bool:
    """Whole-word match in the playlist's name or description ("EDM" matches "EDM mix", not "Edmonton")."""
    wanted = normalize_text(phrase)
    if not wanted:
        return False
    haystack = normalize_text(f"{data.name} {data.description_text}")
    return re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", haystack) is not None
