"""Database models shared by the web app (Vercel) and the pipeline (Mac Mini).

Allowed values live in StrEnums and are enforced by CHECK constraints, so a typo in
either half of the system fails loudly at the database. Two guarantees are written
directly in the migration because they need Postgres features SQLAlchemy models don't
express cleanly:

- outreach: an EXCLUDE constraint so the same curator can't be digested twice within
  90 days, across all profiles.
- run_requests: a partial unique index so only one "Run now" can be open per scope.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

from core.text import normalize_text

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# --- Allowed values -----------------------------------------------------------------


class PlaylistStatus(StrEnum):
    CANDIDATE = "candidate"
    QUALIFIED = "qualified"
    REJECTED = "rejected"
    NO_CONTACT = "no-contact"
    DIGESTED = "digested"
    FETCH_FAILED = "fetch-failed"


class RejectionReason(StrEnum):
    NOT_ALIVE = "not-alive"
    NOT_REAL = "not-real"
    NO_FIT = "no-fit"
    SPOTIFY_OWNED = "spotify-owned"  # Spotify's own playlists: never pitchable (migration 0002)
    TOO_SMALL = "too-small"  # fits, but under the profile's follower floor (migration 0003)


class SizeBand(StrEnum):
    UNDER_500 = "under-500"
    FROM_500_TO_2K = "500-2k"
    FROM_2K_TO_10K = "2k-10k"
    OVER_10K = "10k-plus"


class Reachability(StrEnum):
    CURATOR = "curator"
    SPOTIFY = "spotify"
    LABEL = "label"
    DISTRIBUTOR = "distributor"


class OutreachStatus(StrEnum):
    NEW = "new"
    PITCHED = "pitched"
    SKIP = "skip"
    BAD_FIT = "bad-fit"
    DEAD = "dead"
    REPLIED = "replied"
    PLACED = "placed"


class ExclusionReason(StrEnum):
    BAD_FIT = "bad-fit"
    DEAD = "dead"


class RouteType(StrEnum):
    EMAIL = "email"
    SUBMISSION_FORM = "submission-form"
    INSTAGRAM = "instagram"
    X = "x"
    BLUESKY = "bluesky"
    OTHER = "other"


class Confidence(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class AntiSignalKind(StrEnum):
    ARTIST = "artist"
    TERM = "term"


class SearchTermOrigin(StrEnum):
    MANUAL = "manual"
    SUGGESTED = "suggested"


class SearchTermStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    PAUSED = "paused"
    REJECTED = "rejected"


class SourceProvider(StrEnum):
    SERPER = "serper"
    BRAVE = "brave"
    NEIGHBOUR = "neighbour"


class RunTrigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunRequestStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DONE = "done"
    FAILED = "failed"


def one_of(column: str, allowed: type[StrEnum], name: str | None = None) -> CheckConstraint:
    values = ", ".join(f"'{member.value}'" for member in allowed)
    return CheckConstraint(f"{column} in ({values})", name=name or column)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Profiles -----------------------------------------------------------------------


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("digest_target between 1 and 50", name="digest_target"),
        CheckConstraint("min_followers >= 0", name="min_followers"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    digest_target: Mapped[int] = mapped_column(Integer, default=20, server_default="20")
    # Playlists with fewer followers aren't worth pitching (migration 0003); 0 means no floor.
    min_followers: Mapped[int] = mapped_column(Integer, default=50, server_default="50")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    genres: Mapped[list["ProfileGenre"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    reference_artists: Mapped[list["ReferenceArtist"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    anti_signals: Mapped[list["AntiSignal"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    tracks: Mapped[list["ProfileTrack"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    search_terms: Mapped[list["SearchTerm"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class ProfileGenre(Base):
    __tablename__ = "profile_genres"
    __table_args__ = (UniqueConstraint("profile_id", "normalized_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    tag: Mapped[str] = mapped_column(String(100))
    normalized_tag: Mapped[str] = mapped_column(String(100))
    priority: Mapped[int] = mapped_column(Integer)

    profile: Mapped[Profile] = relationship(back_populates="genres")

    @validates("tag")
    def _normalize(self, _key: str, value: str) -> str:
        self.normalized_tag = normalize_text(value)
        return value


class ReferenceArtist(Base):
    __tablename__ = "reference_artists"
    __table_args__ = (UniqueConstraint("profile_id", "normalized_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    display_name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    spotify_artist_id: Mapped[str | None] = mapped_column(String(22))

    profile: Mapped[Profile] = relationship(back_populates="reference_artists")

    @validates("display_name")
    def _normalize(self, _key: str, value: str) -> str:
        self.normalized_name = normalize_text(value)
        return value


class AntiSignal(Base):
    __tablename__ = "anti_signals"
    __table_args__ = (
        UniqueConstraint("profile_id", "kind", "normalized_value"),
        one_of("kind", AntiSignalKind),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    value: Mapped[str] = mapped_column(String(200))
    normalized_value: Mapped[str] = mapped_column(String(200))

    profile: Mapped[Profile] = relationship(back_populates="anti_signals")

    @validates("value")
    def _normalize(self, _key: str, value: str) -> str:
        self.normalized_value = normalize_text(value)
        return value


class ProfileTrack(Base):
    __tablename__ = "profile_tracks"
    __table_args__ = (UniqueConstraint("profile_id", "spotify_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    spotify_url: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300), default="", server_default="")

    profile: Mapped[Profile] = relationship(back_populates="tracks")


class SearchTerm(Base):
    __tablename__ = "search_terms"
    __table_args__ = (
        UniqueConstraint("profile_id", "normalized_term"),
        one_of("origin", SearchTermOrigin),
        one_of("status", SearchTermStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    term: Mapped[str] = mapped_column(String(200))
    normalized_term: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[Profile] = relationship(back_populates="search_terms")

    @validates("term")
    def _normalize(self, _key: str, value: str) -> str:
        self.normalized_term = normalize_text(value)
        return value


# --- Curators, playlists and contacts ---------------------------------------------------


class Curator(Base):
    __tablename__ = "curators"
    __table_args__ = (
        one_of("exclusion_reason", ExclusionReason),
        CheckConstraint("(excluded_at is null) = (exclusion_reason is null)", name="exclusion_complete"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    spotify_user_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exclusion_reason: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = created_at_column()

    playlists: Mapped[list["Playlist"]] = relationship(back_populates="curator")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="curator")
    outreach: Mapped[list["Outreach"]] = relationship(back_populates="curator")


class Playlist(Base):
    __tablename__ = "playlists"
    __table_args__ = (
        one_of("status", PlaylistStatus),
        one_of("rejection_reason", RejectionReason),
        one_of("size_band", SizeBand),
        one_of("reachability", Reachability),
        CheckConstraint(
            "(status = 'rejected') = (rejection_reason is not null)", name="rejection_has_reason"
        ),
    )

    spotify_id: Mapped[str] = mapped_column(String(22), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    owner_spotify_id: Mapped[str | None] = mapped_column(String(100))
    owner_name: Mapped[str | None] = mapped_column(String(200))
    curator_id: Mapped[int | None] = mapped_column(ForeignKey("curators.id", ondelete="SET NULL"))
    followers: Mapped[int | None] = mapped_column(Integer)
    track_count: Mapped[int | None] = mapped_column(Integer)
    last_add_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    size_band: Mapped[str | None] = mapped_column(String(20))
    is_alive: Mapped[bool | None] = mapped_column(Boolean)
    is_real: Mapped[bool | None] = mapped_column(Boolean)
    reachability: Mapped[str | None] = mapped_column(String(20))
    cover_image_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default=PlaylistStatus.CANDIDATE, server_default="candidate"
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(20))
    first_seen_at: Mapped[datetime] = created_at_column()
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    curator: Mapped[Curator | None] = relationship(back_populates="playlists")


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (one_of("route_type", RouteType), one_of("confidence", Confidence))

    id: Mapped[int] = mapped_column(primary_key=True)
    curator_id: Mapped[int] = mapped_column(ForeignKey("curators.id", ondelete="CASCADE"))
    playlist_id: Mapped[str | None] = mapped_column(ForeignKey("playlists.spotify_id", ondelete="SET NULL"))
    route_type: Mapped[str] = mapped_column(String(20))
    value: Mapped[str] = mapped_column(Text)
    contact_key: Mapped[str] = mapped_column(String(400), unique=True)
    domain_key: Mapped[str | None] = mapped_column(String(300), index=True)
    confidence: Mapped[str] = mapped_column(String(1))
    source_url: Mapped[str] = mapped_column(Text)
    resolved_at: Mapped[datetime] = created_at_column()
    notes: Mapped[str | None] = mapped_column(Text)

    curator: Mapped[Curator] = relationship(back_populates="contacts")


class PlaylistProfileFit(Base):
    __tablename__ = "playlist_profile_fit"

    playlist_id: Mapped[str] = mapped_column(
        ForeignKey("playlists.spotify_id", ondelete="CASCADE"), primary_key=True
    )
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    fit_score: Mapped[float] = mapped_column(Float)
    reference_artists_present: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}")
    genre_tags: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}")
    qualified: Mapped[bool] = mapped_column(Boolean)
    scored_at: Mapped[datetime] = created_at_column()


class Outreach(Base):
    """One digest entry. The 90-day per-curator EXCLUDE constraint lives in the migration."""

    __tablename__ = "outreach"
    __table_args__ = (one_of("status", OutreachStatus),)

    id: Mapped[int] = mapped_column(primary_key=True)
    curator_id: Mapped[int] = mapped_column(ForeignKey("curators.id", ondelete="RESTRICT"))
    playlist_id: Mapped[str] = mapped_column(ForeignKey("playlists.spotify_id", ondelete="RESTRICT"))
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="RESTRICT"))
    digest_date: Mapped[date] = mapped_column(Date)
    brief_text: Mapped[str] = mapped_column(Text)
    suggested_angle: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=OutreachStatus.NEW, server_default="new")
    pitched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()

    curator: Mapped[Curator] = relationship(back_populates="outreach")
    playlist: Mapped[Playlist] = relationship()
    profile: Mapped[Profile] = relationship()


# --- Runs ---------------------------------------------------------------------------


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (one_of("trigger", RunTrigger), one_of("status", RunStatus))

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.RUNNING, server_default="running")
    started_at: Mapped[datetime] = created_at_column()
    heartbeat_at: Mapped[datetime] = created_at_column()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    search_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_spend_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal(0), server_default="0")


class PlaylistSource(Base):
    """Where a playlist was found. Uniqueness (NULLS NOT DISTINCT) is created in the migration."""

    __tablename__ = "playlist_sources"
    __table_args__ = (one_of("provider", SourceProvider),)

    id: Mapped[int] = mapped_column(primary_key=True)
    playlist_id: Mapped[str] = mapped_column(ForeignKey("playlists.spotify_id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(20))
    search_term_id: Mapped[int | None] = mapped_column(ForeignKey("search_terms.id", ondelete="SET NULL"))
    rank: Mapped[int | None] = mapped_column(Integer)
    found_at: Mapped[datetime] = created_at_column()


class RunStageCount(Base):
    """Per-run, per-profile stage counts. Uniqueness (NULLS NOT DISTINCT) is created in the migration."""

    __tablename__ = "run_stage_counts"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(40))
    count_in: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    count_out: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class RunRequest(Base):
    """A "Run now" click. Only one open request per scope: a partial unique index in the migration."""

    __tablename__ = "run_requests"
    __table_args__ = (one_of("status", RunRequestStatus),)

    id: Mapped[int] = mapped_column(primary_key=True)
    requested_at: Mapped[datetime] = created_at_column()
    requested_by: Mapped[str] = mapped_column(String(320))
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(
        String(20), default=RunRequestStatus.PENDING, server_default="pending"
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
