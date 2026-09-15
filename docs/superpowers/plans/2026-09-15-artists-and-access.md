# Artists and Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Several artists can use Noble Hunter. Each person sees only their own artists' profiles and digests, and artists stop competing for the same curators.

**Architecture:**
- **Data.** New `users`, `artists` and `artist_members` tables. Profiles, and later outreach, carry an `artist_id`.
- **Access.** One module, `core/access.py`, decides who can see what, and every web route asks it. A FastAPI dependency (`web/access.py`) rebuilds the viewer on each request, so removing someone takes effect on their next click.
- **Exclusion.** Rules become artist-aware: a 90-day window per artist, "bad fit" per artist, "dead" shared by everyone.

**Tech Stack:** Python 3.12, FastAPI, Jinja, htmx, SQLAlchemy 2, Alembic, Postgres 18 (Docker for tests, Neon in production), pytest, ruff, uv.

**Spec:** [docs/superpowers/specs/2026-09-15-artists-and-access-design.md](../specs/2026-09-15-artists-and-access-design.md)

---

## Before you start

- **Test database:** start it with `scripts/test-db.sh up`. Run tests with `uv run pytest -q`; the suite was 904 passed, 5 skipped before this work.
- **Lint:** `uv run ruff check . && uv run ruff format --check .`, with line length 110.
- **Shell:** zsh. Never name a shell loop variable `path`, because it clobbers `PATH`.
- **Tests:**
  - They run against real Postgres. The `session` fixture rolls back after each test.
  - Web tests override `get_db` with that session and sign in through `tests/web_helpers.FakeGoogle`.
  - `owner@example.com` is the only `ALLOWED_EMAILS` address in tests, so it is the admin.
- **Migrations:** the test database migrates to head once per pytest session (`tests/conftest.py`).
- **Production:**
  - Neon is migrated with `uv run alembic upgrade head`, then `uv run python -m pipeline.cli db grant`.
  - Migrate Neon before pushing any model change, because Vercel deploys every push.
  - Pushing needs Jarred's go-ahead.
- **Commit messages** explain why and end with the co-author line in use on this repo.

## File map

| File | Status | Responsibility |
|---|---|---|
| `core/models.py` | modify | `User`, `Artist`, `ArtistMember`, `ArtistCuratorExclusion`; `Profile.artist_id`; `Outreach.artist_id`; curator exclusion narrowed to `dead` |
| `migrations/versions/20260915_0006_artists_and_users.py` | create | People tables, `profiles.artist_id`, backfill "Synman" |
| `migrations/versions/20260915_0007_curators_per_artist.py` | create | `outreach.artist_id`, per-artist 90-day constraint, `artist_curator_exclusions`, bad-fit move |
| `core/profiles.py` | modify | Profiles belong to an artist; names unique within an artist; list filtered by viewer |
| `core/profile_import.py` | modify | Import into an artist; `artist_for_import` |
| `pipeline/cli.py` | modify | `profile import --artist`; ambiguous `--profile` names |
| `core/people.py` | create | Artists and memberships: create, rename, add and remove members, list |
| `core/access.py` | create | `Viewer`, `viewer_for`, `record_sign_in`, `require_profile`, `require_outreach`, `require_admin`, `visible_to` |
| `web/access.py` | create | Per-request viewer dependency, admin dependency, exception handlers |
| `web/auth.py` | modify | Sign-in by admin list or membership; record the user |
| `web/people.py` | create | People page and actions (admins only) |
| `web/profiles.py`, `web/profile_contents.py`, `web/suggestions.py`, `web/digest.py`, `web/runs.py`, `web/budget.py`, `web/app.py`, `web/templating.py` | modify | Every route takes the viewer |
| `web/templates/people/page.html`, `people/_artist.html` | create | People page |
| `web/templates/base.html`, `profiles/list.html`, `profiles/_create_form.html`, `runs/_panel.html`, `digest/page.html`, `errors/access_denied.html` | modify | Nav link, artist picker, admin-only controls, artist grouping, wording |
| `core/exclusion.py` | modify | Artist-aware eligibility, verdicts and contact exclusion |
| `pipeline/digest.py`, `pipeline/research.py`, `pipeline/report.py` | modify | Per-artist eligibility |
| `core/digest_view.py` | modify | Entries filtered by viewer, grouped by artist |
| `core/db_roles.py` | modify | Grants for the new tables |
| `tests/factories.py`, `tests/web_helpers.py`, `tests/migration_helpers.py` | modify / create | Artists, users, viewers, signing in as someone, migrating to a revision |

Stages match the spec:
- **Stage 1** (Tasks 1–8) ships people and access without changing what Jarred sees.
- **Stage 2** (Tasks 9–12) restricts every route.
- **Stage 3** (Tasks 13–17) makes curators per artist.

---

# Stage 1: People and access

### Task 1: Tables for users, artists and memberships (migration 0006)

**Files:**
- Modify: `core/models.py` (new classes above `Profile`, plus `Profile` changes)
- Create: `migrations/versions/20260915_0006_artists_and_users.py`
- Create: `tests/migration_helpers.py`
- Modify: `tests/factories.py`
- Test: `tests/test_schema_artists.py`, `tests/test_migration_0006.py`

- [ ] **Step 1: Write the failing schema tests**

Create `tests/test_schema_artists.py`:

```python
"""Artists own profiles and people belong to artists (Artists and access, stage 1)."""

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from core.models import ArtistMember, Profile, User
from tests.factories import default_artist_id, make_artist, make_profile, make_user


def test_the_people_tables_exist(engine):
    assert {"users", "artists", "artist_members"} <= set(inspect(engine).get_table_names())


def test_every_profile_belongs_to_an_artist(session):
    session.add(Profile(name="Orphan"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_profile_names_are_unique_within_an_artist(session):
    artist = make_artist(session)
    make_profile(session, "Synman", artist=artist)

    with pytest.raises(IntegrityError):
        make_profile(session, "Synman", artist=artist)


def test_two_artists_can_use_the_same_profile_name(session):
    make_profile(session, "Main", artist=make_artist(session))

    assert make_profile(session, "Main", artist=make_artist(session)).id is not None


def test_artist_names_are_unique(session):
    make_artist(session, "Synman")

    with pytest.raises(IntegrityError):
        make_artist(session, "Synman")


def test_user_emails_are_unique(session):
    make_user(session, "jarred@example.com")

    with pytest.raises(IntegrityError):
        make_user(session, "jarred@example.com")


def test_user_emails_must_be_stored_lowercase(session):
    session.add(User(email="Jarred@Example.com"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_person_joins_an_artist_once(session):
    artist, user = make_artist(session), make_user(session)
    session.add(ArtistMember(artist_id=artist.id, user_id=user.id))
    session.flush()

    session.add(ArtistMember(artist_id=artist.id, user_id=user.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_default_test_artist_is_reused(session):
    assert default_artist_id(session) == default_artist_id(session)
```

- [ ] **Step 2: Write the failing data-migration test**

Create `tests/migration_helpers.py`:

```python
"""Moving the test database to a given revision and back, for tests of data migrations.

These run outside the rolled-back `session` fixture, because DDL can't share its
transaction. Every test that uses them must put the database back at head in a `finally`.
"""

from alembic import command
from alembic.config import Config

from tests.conftest import REPO_ROOT, TEST_DATABASE_URL


def migrate_to(revision: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    if revision == "head":
        command.upgrade(config, "head")
    else:
        current = _current(config)
        if current is not None and current > revision:
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)


def _current(config: Config) -> str | None:
    from sqlalchemy import create_engine, text

    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.connect() as connection:
            return connection.scalar(text("select version_num from alembic_version"))
    finally:
        engine.dispose()
```

Create `tests/test_migration_0006.py`:

```python
"""Migration 0006 moves existing profiles under an artist called "Synman", and adds nothing to an empty database."""

from sqlalchemy import text

from tests.migration_helpers import migrate_to


def test_existing_profiles_move_under_synman(engine):
    try:
        migrate_to("0005")
        with engine.begin() as connection:
            connection.execute(text("insert into profiles (name) values ('Legacy IDM')"))

        migrate_to("0006")

        with engine.connect() as connection:
            artist = connection.execute(text("select id, name from artists")).one()
            owner = connection.scalar(text("select artist_id from profiles where name = 'Legacy IDM'"))
        assert artist.name == "Synman"
        assert owner == artist.id
    finally:
        with engine.begin() as connection:
            connection.execute(text("delete from profiles where name = 'Legacy IDM'"))
            connection.execute(text("delete from artists where name = 'Synman'"))
        migrate_to("head")


def test_an_empty_database_gets_no_artist(engine):
    try:
        migrate_to("0005")
        migrate_to("0006")

        with engine.connect() as connection:
            assert connection.scalar(text("select count(*) from artists")) == 0
    finally:
        migrate_to("head")
```

- [ ] **Step 3: Run both test files to verify they fail**

Run: `uv run pytest tests/test_schema_artists.py tests/test_migration_0006.py -q`

Expected: collection errors for the schema tests (`ImportError: cannot import name 'ArtistMember'`), and the migration test failing with `Can't locate revision identified by '0006'`.

- [ ] **Step 4: Add the models**

In `core/models.py`, add this section directly above `# --- Profiles ---`:

```python
# --- People and artists ---------------------------------------------------------------


class User(Base):
    """Someone who has signed in. Admins come from ALLOWED_EMAILS; everyone else needs a membership."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    picture_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    last_signed_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["ArtistMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Artist(Base):
    """A musician or act. Their profiles, digests and (later) mail belong together."""

    __tablename__ = "artists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = created_at_column()

    members: Mapped[list["ArtistMember"]] = relationship(
        back_populates="artist", cascade="all, delete-orphan"
    )
    profiles: Mapped[list["Profile"]] = relationship(back_populates="artist")


class ArtistMember(Base):
    __tablename__ = "artist_members"

    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    added_at: Mapped[datetime] = created_at_column()
    added_by: Mapped[str | None] = mapped_column(String(320))

    artist: Mapped[Artist] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")
```

Change `Profile` as follows:
- Add `UniqueConstraint("artist_id", "name"),` as the first entry of `__table_args__`.
- Replace the `name` column with `name: Mapped[str] = mapped_column(String(80))  # unique within its artist`.
- Add `artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="RESTRICT"))` below `id`.
- Add `artist: Mapped["Artist"] = relationship(back_populates="profiles")` above `genres`.

In the module docstring, add a bullet: `- profiles: unique by name within an artist (migration 0006).`

- [ ] **Step 5: Write the migration**

Create `migrations/versions/20260915_0006_artists_and_users.py`:

```python
"""artists own profiles; people belong to artists

Jarred (2026-09-15): other artists will use Noble Hunter for their own promotion, so each person
sees only their own artists' work. Existing profiles move under an artist called "Synman". No
users are created here: ALLOWED_EMAILS lives in Vercel, and a user row is written at sign-in.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("picture_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_signed_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "artists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artists")),
        sa.UniqueConstraint("name", name=op.f("uq_artists_name")),
    )
    op.create_table(
        "artist_members",
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("added_by", sa.String(length=320), nullable=True),
        sa.ForeignKeyConstraint(
            ["artist_id"], ["artists.id"], name=op.f("fk_artist_members_artist_id_artists"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_artist_members_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("artist_id", "user_id", name=op.f("pk_artist_members")),
    )
    op.create_index(op.f("ix_artist_members_user_id"), "artist_members", ["user_id"])

    # Existing profiles are Jarred's. An empty database (tests, a fresh install) gets no artist.
    op.add_column("profiles", sa.Column("artist_id", sa.Integer(), nullable=True))
    op.execute("INSERT INTO artists (name) SELECT 'Synman' WHERE EXISTS (SELECT 1 FROM profiles)")
    op.execute("UPDATE profiles SET artist_id = (SELECT id FROM artists WHERE name = 'Synman')")
    op.alter_column("profiles", "artist_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_profiles_artist_id_artists"), "profiles", "artists", ["artist_id"], ["id"], ondelete="RESTRICT"
    )
    op.drop_constraint(op.f("uq_profiles_name"), "profiles", type_="unique")
    op.create_unique_constraint(op.f("uq_profiles_artist_id_name"), "profiles", ["artist_id", "name"])


def downgrade() -> None:
    # Fails if two artists have a profile with the same name: rename one first.
    op.drop_constraint(op.f("uq_profiles_artist_id_name"), "profiles", type_="unique")
    op.create_unique_constraint(op.f("uq_profiles_name"), "profiles", ["name"])
    op.drop_constraint(op.f("fk_profiles_artist_id_artists"), "profiles", type_="foreignkey")
    op.drop_column("profiles", "artist_id")
    op.drop_index(op.f("ix_artist_members_user_id"), table_name="artist_members")
    op.drop_table("artist_members")
    op.drop_table("artists")
    op.drop_table("users")
```

- [ ] **Step 6: Add the factories**

In `tests/factories.py`:
- Change the models import to `from core.models import Artist, ArtistMember, Contact, Curator, Outreach, Playlist, PlaylistStatus, Profile, User`.
- Add `from sqlalchemy import select`.
- Replace `make_profile` with the code below, and add the other functions after `now()`:

```python
DEFAULT_ARTIST = "Test Artist"


def make_artist(session, name: str | None = None) -> Artist:
    artist = Artist(name=name or f"Artist {next(_sequence)}")
    session.add(artist)
    session.flush()
    return artist


def default_artist_id(session) -> int:
    """The artist a test's profiles belong to when the test doesn't care which."""
    existing = session.scalar(select(Artist.id).where(Artist.name == DEFAULT_ARTIST))
    return existing if existing is not None else make_artist(session, DEFAULT_ARTIST).id


def make_user(session, email: str | None = None) -> User:
    user = User(email=email or f"person{next(_sequence)}@example.com")
    session.add(user)
    session.flush()
    return user


def make_member(session, artist: Artist, user: User | None = None) -> User:
    user = user or make_user(session)
    session.add(ArtistMember(artist_id=artist.id, user_id=user.id))
    session.flush()
    return user


def make_profile(session, name: str | None = None, artist: Artist | None = None) -> Profile:
    artist_id = artist.id if artist is not None else default_artist_id(session)
    profile = Profile(name=name or f"Profile {next(_sequence)}", artist_id=artist_id)
    session.add(profile)
    session.flush()
    return profile
```

In `tests/test_min_followers.py` line 136, change `Profile(name="Broken", min_followers=-5)` to `Profile(name="Broken", min_followers=-5, artist_id=default_artist_id(session))` and import `default_artist_id` from `tests.factories`. That way the test still fails on the follower check, not on the missing artist.

In `tests/test_cli_pipeline.py`, change `wipe` so it also clears the new tables, because these tests commit:

```python
connection.execute(text("truncate profiles, playlists, curators, runs, artists, users restart identity cascade"))
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_schema_artists.py tests/test_migration_0006.py -q`

Expected: 11 passed.

- [ ] **Step 8: Don't commit yet**

`core.profiles.create_profile`, `core.profile_import` and the web create route still build profiles without an artist, so the full suite fails here. Task 2 fixes that and commits both tasks together.

---

### Task 2: A profile belongs to an artist

**Files:**
- Modify: `core/profiles.py`, `core/profile_import.py`, `pipeline/cli.py`, `web/profiles.py`, `web/templates/profiles/_create_form.html`, `web/templates/profiles/list.html`
- Modify (mechanical): every test calling `create_profile(session, ...)` or `import_profile(session, ...)`
- Test: `tests/test_profiles_artist.py`, `tests/test_profile_import.py`, `tests/test_web_profiles.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_profiles_artist.py`:

```python
"""Profiles belong to an artist, and their names only need to be unique within it."""

import pytest

from core.profile_import import artist_for_import
from core.profiles import ProfileValidationError, artist_choices, create_profile, update_profile_settings
from pipeline.cli import _profile_id_by_name
from tests.factories import make_artist


def test_a_new_profile_belongs_to_the_chosen_artist(session):
    artist = make_artist(session)

    assert create_profile(session, artist.id, "Synman").artist_id == artist.id


def test_a_name_is_refused_twice_within_an_artist(session):
    artist = make_artist(session)
    create_profile(session, artist.id, "Synman")

    with pytest.raises(ProfileValidationError) as error:
        create_profile(session, artist.id, "synman")

    assert "already exists" in error.value.errors["name"]


def test_another_artist_can_reuse_a_name(session):
    create_profile(session, make_artist(session).id, "Main")

    assert create_profile(session, make_artist(session).id, "Main").id is not None


def test_an_unknown_artist_is_refused(session):
    with pytest.raises(ProfileValidationError) as error:
        create_profile(session, 999_999, "Synman")

    assert error.value.errors["artist_id"] == "Choose which artist this profile is for"


def test_renaming_only_checks_names_within_the_artist(session):
    first, second = make_artist(session), make_artist(session)
    create_profile(session, first.id, "Taken")
    other = create_profile(session, second.id, "Other")

    update_profile_settings(session, other.id, "Taken", 20)

    assert other.name == "Taken"


def test_artist_choices_are_sorted_by_name(session):
    make_artist(session, "zeta")
    make_artist(session, "Alpha")

    names = [name for _, name in artist_choices(session)]

    assert names.index("Alpha") < names.index("zeta")


class TestArtistForImport:
    def test_a_named_artist_is_found_whatever_the_case(self, session):
        artist = make_artist(session, "Synman")

        assert artist_for_import(session, "SYNMAN") == artist.id

    def test_a_new_name_creates_the_artist(self, session):
        artist_id = artist_for_import(session, "  Brand   New  ")

        assert artist_for_import(session, "Brand New") == artist_id

    def test_with_no_name_the_only_artist_is_used(self, session):
        artist = make_artist(session)

        assert artist_for_import(session, None) == artist.id

    def test_with_no_name_and_no_artists_it_says_what_to_do(self, session):
        with pytest.raises(LookupError, match="--artist"):
            artist_for_import(session, None)

    def test_with_no_name_and_several_artists_it_asks_which(self, session):
        make_artist(session)
        make_artist(session)

        with pytest.raises(LookupError, match="more than one artist"):
            artist_for_import(session, None)


def test_a_profile_name_shared_by_two_artists_is_ambiguous_on_the_command_line(session):
    create_profile(session, make_artist(session).id, "Main")
    create_profile(session, make_artist(session).id, "Main")

    with pytest.raises(LookupError, match="More than one artist"):
        _profile_id_by_name(session, "main")
```

Add to `tests/test_profile_import.py`, inside `class TestImportProfile`:

```python
    def test_the_same_file_can_be_imported_for_two_artists(self, session, tmp_path):
        first = import_profile(session, make_artist(session).id, config_from(tmp_path))
        second = import_profile(session, make_artist(session).id, config_from(tmp_path))

        assert first.profile_id != second.profile_id
        assert first.created and second.created
```

Then add `from tests.factories import default_artist_id, make_artist` to its imports.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_profiles_artist.py -q`

Expected: collection error `ImportError: cannot import name 'artist_choices' from 'core.profiles'`.

- [ ] **Step 3: Update `core/profiles.py`**

Change the imports to `from core.models import Artist, Profile, SearchTermStatus`, then make these edits:

```python
def create_profile(
    session: Session, artist_id: int, name: str, digest_target: int | str = DEFAULT_DIGEST_TARGET
) -> Profile:
    if session.get(Artist, artist_id) is None:
        raise ProfileValidationError({"artist_id": "Choose which artist this profile is for"})
    clean_name, target, _ = _validated_settings(session, artist_id, name, digest_target, profile_id=None)
    profile = Profile(artist_id=artist_id, name=clean_name, digest_target=target)
    session.add(profile)
    session.flush()
    return profile


def update_profile_settings(
    session: Session,
    profile_id: int,
    name: str,
    digest_target: int | str,
    min_followers: int | str | None = None,
) -> Profile:
    """Rename and retune a profile. Leaving `min_followers` out keeps the current floor."""
    profile = get_profile(session, profile_id)
    clean_name, target, floor = _validated_settings(
        session, profile.artist_id, name, digest_target, profile_id, min_followers
    )
    profile.name, profile.digest_target = clean_name, target
    if floor is not None:
        profile.min_followers = floor
    session.flush()
    return profile


def artist_choices(session: Session) -> list[tuple[int, str]]:
    """Every artist as (id, name), for the new-profile form. Stage 2 narrows this to the viewer's."""
    rows = session.execute(select(Artist.id, Artist.name).order_by(func.lower(Artist.name), Artist.id))
    return [(artist_id, name) for artist_id, name in rows]
```

In `_validated_settings`:
- The signature becomes `(session, artist_id: int, name, digest_target, profile_id, min_followers=None)`.
- The name check calls `_name_taken(session, artist_id, clean_name, profile_id)`.
- The message becomes `f"A profile called “{clean_name}” already exists for this artist"`.

Replace `_name_taken`:

```python
def _name_taken(session: Session, artist_id: int, name: str, profile_id: int | None) -> bool:
    query = select(Profile.id).where(Profile.artist_id == artist_id, func.lower(Profile.name) == name.lower())
    if profile_id is not None:
        query = query.where(Profile.id != profile_id)
    return session.scalar(query.limit(1)) is not None
```

- [ ] **Step 4: Update `core/profile_import.py`**

Add `func` to the SQLAlchemy import, and `Artist` to the models import. Add `from core.profile_rules import MAX_NAME_LENGTH`.

Replace the start of `import_profile` and add `artist_for_import`:

```python
def import_profile(session: Session, artist_id: int, config: ProfileConfig) -> ImportResult:
    profile = session.scalar(select(Profile).where(Profile.artist_id == artist_id, Profile.name == config.name))
    created = profile is None
    if created:
        profile = Profile(artist_id=artist_id, name=config.name)
        session.add(profile)
    # ... the rest of the function is unchanged


def artist_for_import(session: Session, name: str | None) -> int:
    """The artist named (created if it's new), or the only artist there is."""
    if name is not None:
        clean = " ".join(name.split())
        if not clean or len(clean) > MAX_NAME_LENGTH:
            raise LookupError(f"Give --artist a name of 1 to {MAX_NAME_LENGTH} characters.")
        artist = session.scalar(select(Artist).where(func.lower(Artist.name) == clean.lower()))
        if artist is None:
            artist = Artist(name=clean)
            session.add(artist)
            session.flush()
        return artist.id

    ids = list(session.scalars(select(Artist.id).order_by(Artist.id).limit(2)))
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise LookupError("There are no artists yet. Pass --artist NAME to create one.")
    raise LookupError("There's more than one artist. Say which with --artist NAME.")
```

`MAX_NAME_LENGTH` is 80 in `core/profile_rules.py`, the same limit as artist names.

- [ ] **Step 5: Update `pipeline/cli.py`**

Import `artist_for_import` alongside `import_profile`. Change the import command:

```python
@profile_app.command("import")
def import_command(
    path: Annotated[Path, typer.Argument(help="Path to a profile YAML file.")],
    artist: Annotated[
        str | None,
        typer.Option(help="Artist the profile belongs to (created if new). Needed once there's more than one."),
    ] = None,
) -> None:
    """Create or update a profile from a YAML file."""
    try:
        config = load_profile_config(path)
        settings = load_settings()
    except (ProfileConfigError, MissingSettingError) as error:
        raise fail(str(error)) from None

    engine = create_engine(settings.sqlalchemy_url(pooled=False))
    try:
        with Session(engine) as session:
            result = import_profile(session, artist_for_import(session, artist), config)
            session.commit()
    except LookupError as error:
        raise fail(str(error)) from None
    except SQLAlchemyError as error:
        # Name the failure without echoing connection details.
        raise fail(f"Database error while importing {config.name!r}: {describe_db_error(error)}") from None
    finally:
        engine.dispose()

    action = "Created" if result.created else "Updated"
    typer.echo(
        f"{action} profile {config.name!r} (id {result.profile_id}); {result.terms_added} new search term(s)."
    )
```

Replace `_profile_id_by_name`:

```python
def _profile_id_by_name(session: Session, name: str) -> int:
    ids = list(
        session.scalars(select(Profile.id).where(func.lower(Profile.name) == name.strip().lower()).limit(2))
    )
    if not ids:
        raise LookupError(f"No profile called “{name}”.")
    if len(ids) > 1:
        raise LookupError(f"More than one artist has a profile called “{name}”. Rename one in the web app first.")
    return ids[0]
```

In `tests/test_profile_import.py`, change `TestProfileImportCommand.test_imports_example_profile`:
- The invoke args become `["profile", "import", str(example), "--artist", "CLI Import Artist"]`.
- The `finally` block also runs `session.execute(delete(Artist).where(Artist.name == "CLI Import Artist"))` after deleting the profile. Import `Artist` from `core.models`.

- [ ] **Step 6: Update the web create route and form**

In `web/profiles.py`, import `artist_choices` from `core.profiles`. Replace `profiles_page` and `create`:

```python
@router.get("")
def profiles_page(request: Request, db: DbSession) -> Response:
    context = {
        "profiles": list_profiles(db),
        "form": {"name": "", "digest_target": DEFAULT_DIGEST_TARGET, "artist_id": ""},
        "errors": {},
        "artist_choices": artist_choices(db),
        **panel_context(db),
        **budget_context(db),
    }
    return templates.TemplateResponse(request, "profiles/list.html", context)


@router.post("")
def create(
    request: Request,
    db: DbSession,
    name: FormText = "",
    digest_target: FormText = "",
    artist_id: FormText = "",
) -> Response:
    try:
        profile = create_profile(db, _form_id(artist_id), name, digest_target)
        db.commit()
    except ProfileValidationError as error:
        context = {
            "form": {"name": name, "digest_target": digest_target, "artist_id": artist_id},
            "errors": error.errors,
            "artist_choices": artist_choices(db),
        }
        return templates.TemplateResponse(request, "profiles/_create_form.html", context, status_code=422)
    return _redirect(request, f"/profiles/{profile.id}")


def _form_id(raw: str) -> int:
    """A posted id, or 0 (which matches nothing) when it isn't a whole number."""
    text = raw.strip()
    return int(text) if text.isdigit() else 0
```

In `web/templates/profiles/_create_form.html`, insert this as the first block inside `<form>`:

```html
  {% if artist_choices | length == 1 %}
  <input type="hidden" name="artist_id" value="{{ artist_choices[0][0] }}">
  {% elif artist_choices %}
  <div class="field">
    <label class="field__label" for="new-profile-artist">Artist</label>
    <select class="input" id="new-profile-artist" name="artist_id" required
            {% if errors.artist_id %}aria-invalid="true" aria-describedby="new-profile-artist-error"{% endif %}>
      <option value="">Choose an artist</option>
      {% for choice_id, choice_name in artist_choices %}
      <option value="{{ choice_id }}"{% if form.artist_id | string == choice_id | string %} selected{% endif %}>{{ choice_name }}</option>
      {% endfor %}
    </select>
    {% if errors.artist_id %}<p class="field__error" id="new-profile-artist-error">{{ errors.artist_id }}</p>{% endif %}
  </div>
  {% else %}
  <p class="field__hint">There are no artists yet. An admin adds them on the People page.</p>
  {% endif %}
```

If there's exactly one artist and it's rejected (for example, it was deleted meanwhile), `errors.artist_id` still needs to be shown. Add this line straight after the hidden input:

```html
  {% if errors.artist_id %}<p class="field__error" role="alert">{{ errors.artist_id }}</p>{% endif %}
```

`profiles/list.html` needs no change, because it already includes `_create_form.html` with the page context.

- [ ] **Step 7: Update existing test calls mechanically**

Run this Python script from the repo root. It prefixes the artist on every test call and adds the import where it's missing:

```bash
uv run python - <<'EOF'
from pathlib import Path

IMPORT = "from tests.factories import default_artist_id\n"
for test_file in sorted(Path("tests").glob("test_*.py")):
    source = test_file.read_text()
    updated = source.replace("create_profile(session, ", "create_profile(session, default_artist_id(session), ")
    updated = updated.replace(
        "import_profile(session, config_from(", "import_profile(session, default_artist_id(session), config_from("
    )
    if updated == source:
        continue
    if "default_artist_id" not in source:
        lines = updated.splitlines(keepends=True)
        first_import = next(i for i, line in enumerate(lines) if line.startswith(("from ", "import ")))
        lines.insert(first_import, IMPORT)
        updated = "".join(lines)
    test_file.write_text(updated)
    print("updated", test_file)
EOF
uv run ruff check --select I,F401 --fix tests
```

The script must not touch `tests/test_profiles_artist.py`, which already passes explicit artist ids. Its calls use `make_artist(session).id` or `artist.id`, so the `create_profile(session, ` pattern doesn't match there. Check with `git diff --stat tests/test_profiles_artist.py`; it should show no change.

In `tests/test_web_profiles.py`, every `htmx_post(client, "/profiles", {...})` needs the artist. Add `"artist_id": str(default_artist_id(session))` to each form dict; the tests already take `session`. Then add one test to `class TestCreateProfile`:

```python
    def test_a_missing_artist_is_explained_in_the_form(self, client, session):
        default_artist_id(session)
        make_artist(session, "Second Artist")

        response = htmx_post(client, "/profiles", {"name": "Synman", "digest_target": "15"})

        assert response.status_code == 422
        assert "Choose which artist this profile is for" in response.text
```

Import `make_artist` from `tests.factories` too.

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest -q`

Expected: everything passes, with the usual 5 skipped.

If any test still fails with `null value in column "artist_id"`, it builds a `Profile` without going through the factories or `create_profile`. Give it `artist_id=default_artist_id(session)`, following the example in Task 1 Step 6.

- [ ] **Step 9: Lint and commit Tasks 1 and 2 together**

```bash
uv run ruff check . && uv run ruff format .
git add core/models.py core/profiles.py core/profile_import.py pipeline/cli.py web/profiles.py \
  web/templates/profiles/_create_form.html migrations/versions/20260915_0006_artists_and_users.py tests
git commit -m "feat: profiles belong to an artist

Other artists will use Noble Hunter too, so profiles need an owner before access can be
restricted. Existing profiles move under Synman; names only clash within one artist.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: People and artists in `core/people.py`

**Files:**
- Create: `core/people.py`
- Test: `tests/test_people.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_people.py`:

```python
"""Who works on which artist: the rules behind the People page."""

import pytest

from core.models import Artist, ArtistMember, User
from core.people import (
    PeopleValidationError,
    add_member,
    create_artist,
    list_artists,
    normalize_email,
    remove_member,
    rename_artist,
)
from tests.factories import make_artist, make_member, make_profile, make_user

ADMIN = "owner@example.com"


class TestNormalizeEmail:
    @pytest.mark.parametrize("raw", ["  Nik@Example.COM ", "nik@example.com"])
    def test_valid_addresses_are_trimmed_and_lowercased(self, raw):
        assert normalize_email(raw) == "nik@example.com"

    @pytest.mark.parametrize("raw", ["", "nik", "nik@", "@example.com", "nik@example", "n ik@example.com"])
    def test_invalid_addresses_are_refused(self, raw):
        assert normalize_email(raw) is None

    def test_overlong_addresses_are_refused(self):
        assert normalize_email("a" * 310 + "@example.com") is None


class TestArtists:
    def test_create_tidies_the_name(self, session):
        assert create_artist(session, "  Synman   Live ").name == "Synman Live"

    def test_names_are_unique_whatever_the_case(self, session):
        create_artist(session, "Synman")

        with pytest.raises(PeopleValidationError) as error:
            create_artist(session, "SYNMAN")

        assert error.value.errors["name"] == "There's already an artist called “SYNMAN”"

    @pytest.mark.parametrize("name", ["", "   ", "x" * 81])
    def test_blank_or_overlong_names_are_refused(self, session, name):
        with pytest.raises(PeopleValidationError) as error:
            create_artist(session, name)

        assert "name" in error.value.errors

    def test_rename(self, session):
        artist = make_artist(session, "Old Name")

        rename_artist(session, artist.id, "New Name")

        assert session.get(Artist, artist.id).name == "New Name"

    def test_renaming_to_its_own_name_in_another_case_is_fine(self, session):
        artist = make_artist(session, "synman")

        assert rename_artist(session, artist.id, "Synman").name == "Synman"

    def test_renaming_to_another_artists_name_is_refused(self, session):
        make_artist(session, "Taken")
        artist = make_artist(session, "Mine")

        with pytest.raises(PeopleValidationError):
            rename_artist(session, artist.id, "taken")

    def test_renaming_a_missing_artist_raises_lookup_error(self, session):
        with pytest.raises(LookupError):
            rename_artist(session, 999_999, "Anything")

    def test_list_shows_members_and_profile_counts_sorted(self, session):
        artist = make_artist(session, "b artist")
        make_artist(session, "A artist")
        make_member(session, artist, make_user(session, "zed@example.com"))
        make_member(session, artist, make_user(session, "amy@example.com"))
        make_profile(session, artist=artist)

        summaries = [summary for summary in list_artists(session) if summary.name in ("A artist", "b artist")]

        assert [summary.name for summary in summaries] == ["A artist", "b artist"]
        assert [member.email for member in summaries[1].members] == ["amy@example.com", "zed@example.com"]
        assert summaries[1].profile_count == 1
        assert summaries[0].members == ()


class TestAddMember:
    def test_adds_a_new_person_to_an_existing_artist(self, session):
        artist = make_artist(session)

        user = add_member(session, email="Nik@Example.com", artist_id=artist.id, added_by=ADMIN)

        assert user.email == "nik@example.com"
        membership = session.get(ArtistMember, (artist.id, user.id))
        assert membership.added_by == ADMIN

    def test_creates_the_artist_when_given_a_new_name(self, session):
        user = add_member(session, email="nik@example.com", artist_id=None, new_artist_name="Nik Beats", added_by=ADMIN)

        artist = session.query(Artist).filter_by(name="Nik Beats").one()
        assert session.get(ArtistMember, (artist.id, user.id)) is not None

    def test_the_same_person_can_join_a_second_artist(self, session):
        first, second = make_artist(session), make_artist(session)
        add_member(session, email="nik@example.com", artist_id=first.id, added_by=ADMIN)

        add_member(session, email="nik@example.com", artist_id=second.id, added_by=ADMIN)

        assert session.query(User).filter_by(email="nik@example.com").count() == 1

    def test_adding_someone_twice_is_explained(self, session):
        artist = make_artist(session, "Synman")
        add_member(session, email="nik@example.com", artist_id=artist.id, added_by=ADMIN)

        with pytest.raises(PeopleValidationError) as error:
            add_member(session, email="NIK@example.com", artist_id=artist.id, added_by=ADMIN)

        assert error.value.errors["email"] == "nik@example.com is already on Synman"

    def test_an_invalid_email_and_no_artist_are_both_reported(self, session):
        with pytest.raises(PeopleValidationError) as error:
            add_member(session, email="not-an-email", artist_id=None, added_by=ADMIN)

        assert error.value.errors == {
            "email": "Enter a valid email address",
            "artist": "Choose an artist or name a new one",
        }

    def test_an_artist_that_no_longer_exists_is_explained(self, session):
        with pytest.raises(PeopleValidationError) as error:
            add_member(session, email="nik@example.com", artist_id=999_999, added_by=ADMIN)

        assert error.value.errors["artist"] == "That artist no longer exists"

    def test_a_new_artist_name_that_is_taken_is_explained_under_artist(self, session):
        make_artist(session, "Synman")

        with pytest.raises(PeopleValidationError) as error:
            add_member(session, email="nik@example.com", artist_id=None, new_artist_name="synman", added_by=ADMIN)

        assert error.value.errors["artist"] == "There's already an artist called “synman”"


class TestRemoveMember:
    def test_removes_the_membership_but_keeps_the_person(self, session):
        artist = make_artist(session)
        user = make_member(session, artist)

        remove_member(session, artist.id, user.id)

        assert session.get(ArtistMember, (artist.id, user.id)) is None
        assert session.get(User, user.id) is not None

    def test_removing_someone_who_is_not_a_member_raises_lookup_error(self, session):
        with pytest.raises(LookupError):
            remove_member(session, make_artist(session).id, make_user(session).id)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_people.py -q`

Expected: collection error `ModuleNotFoundError: No module named 'core.people'`.

- [ ] **Step 3: Write `core/people.py`**

```python
"""People and artists: who works on which artist.

Admins (everyone in ALLOWED_EMAILS) manage this on the People page. There are no invitation
emails: adding someone means their Google account is let in the next time they sign in. The
rules live here, not in the routes, so every way of changing membership obeys them, and
validation reports every problem at once in words an admin can act on.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from core.models import Artist, ArtistMember, Profile, User

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LENGTH = 320
MAX_ARTIST_NAME_LENGTH = 80


class PeopleValidationError(ValueError):
    """One message per field, e.g. {"email": "Enter a valid email address"}."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{field}: {message}" for field, message in errors.items()))


@dataclass(frozen=True)
class MemberSummary:
    user_id: int
    email: str
    name: str | None
    last_signed_in_at: datetime | None


@dataclass(frozen=True)
class ArtistSummary:
    id: int
    name: str
    members: tuple[MemberSummary, ...]
    profile_count: int


def normalize_email(value: str) -> str | None:
    email = value.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.match(email):
        return None
    return email


def list_artists(session: Session) -> list[ArtistSummary]:
    profile_counts = dict(
        session.execute(select(Profile.artist_id, func.count()).group_by(Profile.artist_id)).tuples()
    )
    artists = session.scalars(
        select(Artist)
        .options(selectinload(Artist.members).selectinload(ArtistMember.user))
        .order_by(func.lower(Artist.name), Artist.id)
    )
    return [_summary(artist, profile_counts.get(artist.id, 0)) for artist in artists]


def create_artist(session: Session, name: str) -> Artist:
    artist = Artist(name=_valid_artist_name(session, name, artist_id=None, field="name"))
    session.add(artist)
    session.flush()
    return artist


def rename_artist(session: Session, artist_id: int, name: str) -> Artist:
    artist = session.get(Artist, artist_id)
    if artist is None:
        raise LookupError(f"Artist {artist_id} not found")
    artist.name = _valid_artist_name(session, name, artist_id=artist_id, field="name")
    session.flush()
    return artist


def add_member(
    session: Session, *, email: str, artist_id: int | None, added_by: str, new_artist_name: str = ""
) -> User:
    """Put someone on an artist: an existing one by id, or a new one by name."""
    errors: dict[str, str] = {}
    clean_email = normalize_email(email)
    if clean_email is None:
        errors["email"] = "Enter a valid email address"

    artist = None
    if artist_id is not None:
        artist = session.get(Artist, artist_id)
        if artist is None:
            errors["artist"] = "That artist no longer exists"
    elif not " ".join(new_artist_name.split()):
        errors["artist"] = "Choose an artist or name a new one"
    else:
        try:
            _valid_artist_name(session, new_artist_name, artist_id=None, field="artist")
        except PeopleValidationError as error:
            errors.update(error.errors)
    if errors:
        raise PeopleValidationError(errors)

    if artist is None:
        artist = create_artist(session, new_artist_name)
    user = session.scalar(select(User).where(User.email == clean_email))
    if user is None:
        user = User(email=clean_email)
        session.add(user)
        session.flush()
    elif session.get(ArtistMember, (artist.id, user.id)) is not None:
        raise PeopleValidationError({"email": f"{clean_email} is already on {artist.name}"})

    session.add(ArtistMember(artist_id=artist.id, user_id=user.id, added_by=added_by))
    session.flush()
    return user


def remove_member(session: Session, artist_id: int, user_id: int) -> None:
    """Take someone off an artist. Their user row stays, so history still says who they were."""
    membership = session.get(ArtistMember, (artist_id, user_id))
    if membership is None:
        raise LookupError(f"User {user_id} is not on artist {artist_id}")
    session.delete(membership)
    session.flush()


def _valid_artist_name(session: Session, name: str, *, artist_id: int | None, field: str) -> str:
    clean = " ".join(name.split())
    if not clean:
        raise PeopleValidationError({field: "Give the artist a name"})
    if len(clean) > MAX_ARTIST_NAME_LENGTH:
        raise PeopleValidationError({field: f"Keep the name to {MAX_ARTIST_NAME_LENGTH} characters or fewer"})
    query = select(Artist.id).where(func.lower(Artist.name) == clean.lower())
    if artist_id is not None:
        query = query.where(Artist.id != artist_id)
    if session.scalar(query.limit(1)) is not None:
        raise PeopleValidationError({field: f"There's already an artist called “{clean}”"})
    return clean


def _summary(artist: Artist, profile_count: int) -> ArtistSummary:
    members = sorted(artist.members, key=lambda member: member.user.email)
    return ArtistSummary(
        id=artist.id,
        name=artist.name,
        members=tuple(
            MemberSummary(
                user_id=member.user.id,
                email=member.user.email,
                name=member.user.name,
                last_signed_in_at=member.user.last_signed_in_at,
            )
            for member in members
        ),
        profile_count=profile_count,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_people.py -q`

Expected: 28 passed.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check core/people.py tests/test_people.py && uv run ruff format core/people.py tests/test_people.py
git add core/people.py tests/test_people.py
git commit -m "feat: rules for adding people to artists

Admins will invite artists from a People page instead of editing ALLOWED_EMAILS and
redeploying. The rules live in core so every route obeys them.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Who can see what, in `core/access.py`

**Files:**
- Create: `core/access.py`
- Modify: `tests/factories.py` (viewer builders)
- Test: `tests/test_access.py`

- [ ] **Step 1: Add viewer builders to the factories**

Append to `tests/factories.py`, and add `from core.access import Viewer` to its imports:

```python
ADMIN_EMAIL = "owner@example.com"


def admin_viewer() -> Viewer:
    return Viewer(email=ADMIN_EMAIL, is_admin=True, artist_ids=frozenset())


def member_viewer(*artists: Artist, email: str = "member@example.com") -> Viewer:
    return Viewer(email=email, is_admin=False, artist_ids=frozenset(artist.id for artist in artists))
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_access.py`:

```python
"""Who can see what: members see their own artists' work, admins see everything."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, select

from core.access import (
    AdminOnly,
    NotVisible,
    record_sign_in,
    require_admin,
    require_outreach,
    require_profile,
    viewer_for,
    visible_to,
)
from core.models import ArtistMember, Profile, User
from tests.factories import (
    admin_viewer,
    make_artist,
    make_curator,
    make_member,
    make_outreach,
    make_profile,
    make_user,
    member_viewer,
)

ADMINS = frozenset({"owner@example.com"})
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)


class TestViewerFor:
    def test_an_admin_needs_no_membership(self, session):
        viewer = viewer_for(session, "Owner@Example.com", ADMINS)

        assert viewer.is_admin
        assert viewer.email == "owner@example.com"
        assert viewer.artist_ids == frozenset()

    def test_a_member_sees_their_artists(self, session):
        first, second = make_artist(session), make_artist(session)
        user = make_user(session, "nik@example.com")
        make_member(session, first, user)
        make_member(session, second, user)

        viewer = viewer_for(session, "nik@example.com", ADMINS)

        assert not viewer.is_admin
        assert viewer.artist_ids == {first.id, second.id}

    def test_an_admin_who_is_also_a_member_keeps_both(self, session):
        artist = make_artist(session)
        make_member(session, artist, make_user(session, "owner@example.com"))

        viewer = viewer_for(session, "owner@example.com", ADMINS)

        assert viewer.is_admin and viewer.artist_ids == {artist.id}

    @pytest.mark.parametrize("email", ["stranger@example.com", "", "   "])
    def test_anyone_else_gets_no_access(self, session, email):
        assert viewer_for(session, email, ADMINS) is None

    def test_a_person_with_a_user_row_but_no_membership_gets_no_access(self, session):
        make_user(session, "former@example.com")

        assert viewer_for(session, "former@example.com", ADMINS) is None

    def test_removal_takes_effect_on_the_next_check(self, session):
        artist = make_artist(session)
        user = make_member(session, artist, make_user(session, "nik@example.com"))
        assert viewer_for(session, "nik@example.com", ADMINS) is not None

        session.execute(delete(ArtistMember).where(ArtistMember.user_id == user.id))

        assert viewer_for(session, "nik@example.com", ADMINS) is None


class TestRecordSignIn:
    def test_creates_the_user_on_first_sign_in(self, session):
        record_sign_in(session, email="Nik@Example.com", name="Nik", picture_url=None, now=NOW)

        user = session.scalar(select(User).where(User.email == "nik@example.com"))
        assert (user.name, user.last_signed_in_at) == ("Nik", NOW)

    def test_updates_the_same_user_later_and_keeps_a_known_name(self, session):
        record_sign_in(session, email="nik@example.com", name="Nik", picture_url=None, now=NOW)
        later = datetime(2026, 9, 16, tzinfo=UTC)

        user = record_sign_in(session, email="nik@example.com", name=None, picture_url="https://x/p.png", now=later)

        assert (user.name, user.picture_url, user.last_signed_in_at) == ("Nik", "https://x/p.png", later)
        assert session.query(User).filter_by(email="nik@example.com").count() == 1


class TestVisibleTo:
    def test_a_member_only_sees_their_artists_profiles(self, session):
        mine, theirs = make_artist(session), make_artist(session)
        own = make_profile(session, artist=mine)
        make_profile(session, artist=theirs)

        ids = set(session.scalars(select(Profile.id).where(visible_to(member_viewer(mine), Profile.artist_id))))

        assert ids == {own.id}

    def test_an_admin_sees_every_profile(self, session):
        profiles = {make_profile(session, artist=make_artist(session)).id for _ in range(2)}

        ids = set(session.scalars(select(Profile.id).where(visible_to(admin_viewer(), Profile.artist_id))))

        assert profiles <= ids

    def test_a_viewer_with_no_artists_sees_nothing(self, session):
        make_profile(session, artist=make_artist(session))

        ids = session.scalars(select(Profile.id).where(visible_to(member_viewer(), Profile.artist_id))).all()

        assert ids == []


class TestRequire:
    def test_a_member_gets_their_own_profile(self, session):
        artist = make_artist(session)
        profile = make_profile(session, artist=artist)

        assert require_profile(session, member_viewer(artist), profile.id) is profile

    def test_another_artists_profile_is_not_visible(self, session):
        profile = make_profile(session, artist=make_artist(session))

        with pytest.raises(NotVisible):
            require_profile(session, member_viewer(make_artist(session)), profile.id)

    def test_a_missing_profile_is_not_visible_either(self, session):
        with pytest.raises(NotVisible):
            require_profile(session, admin_viewer(), 999_999)

    def test_an_admin_gets_any_profile(self, session):
        profile = make_profile(session, artist=make_artist(session))

        assert require_profile(session, admin_viewer(), profile.id) is profile

    def test_outreach_follows_its_profiles_artist(self, session):
        artist = make_artist(session)
        outreach = make_outreach(session, make_curator(session), make_profile(session, artist=artist), date(2026, 9, 15))

        assert require_outreach(session, member_viewer(artist), outreach.id) is outreach
        with pytest.raises(NotVisible):
            require_outreach(session, member_viewer(make_artist(session)), outreach.id)
        with pytest.raises(NotVisible):
            require_outreach(session, admin_viewer(), 999_999)

    def test_admin_only_actions(self, session):
        require_admin(admin_viewer())

        with pytest.raises(AdminOnly):
            require_admin(member_viewer(make_artist(session)))
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_access.py -q`

Expected: collection error `ModuleNotFoundError: No module named 'core.access'`.

- [ ] **Step 4: Write `core/access.py`**

```python
"""Who can see what. The one place that decides.

People see their own artists' profiles and digests (and later their mail); admins, everyone
in ALLOWED_EMAILS, see everything. Every web route asks this module instead of filtering on
its own, and tests/test_web_access.py walks every route to check none forgot. Anything a
viewer can't see is reported exactly like something that doesn't exist, so nobody can learn
what another artist has.

Access is worked out afresh on every request (see web/access.py), so taking someone off an
artist stops them on their next click, not when their session cookie expires.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, select, true
from sqlalchemy.orm import Session

from core.models import ArtistMember, Outreach, Profile, User


@dataclass(frozen=True)
class Viewer:
    email: str
    is_admin: bool
    artist_ids: frozenset[int]

    def can_see_artist(self, artist_id: int) -> bool:
        return self.is_admin or artist_id in self.artist_ids


class NotVisible(LookupError):
    """Missing, or on an artist the viewer isn't part of. The web app answers 404 either way."""


class AdminOnly(PermissionError):
    """Only admins may do this. The web app answers 403."""


def viewer_for(session: Session, email: str, admin_emails: frozenset[str]) -> Viewer | None:
    """The viewer for a signed-in email, or None if that email has no access at all."""
    clean = email.strip().lower()
    if not clean:
        return None
    artist_ids = frozenset(
        session.scalars(
            select(ArtistMember.artist_id).join(User, User.id == ArtistMember.user_id).where(User.email == clean)
        )
    )
    is_admin = clean in admin_emails
    if not is_admin and not artist_ids:
        return None
    return Viewer(email=clean, is_admin=is_admin, artist_ids=artist_ids)


def record_sign_in(
    session: Session, *, email: str, name: str | None, picture_url: str | None, now: datetime
) -> User:
    """Create or refresh the user row for someone who just signed in."""
    clean = email.strip().lower()
    user = session.scalar(select(User).where(User.email == clean))
    if user is None:
        user = User(email=clean)
        session.add(user)
    user.name = name or user.name
    user.picture_url = picture_url
    user.last_signed_in_at = now
    session.flush()
    return user


def visible_to(viewer: Viewer, artist_column) -> ColumnElement[bool]:
    """A WHERE clause keeping rows on the viewer's artists: every row for an admin."""
    if viewer.is_admin:
        return true()
    return artist_column.in_(sorted(viewer.artist_ids))


def require_profile(session: Session, viewer: Viewer, profile_id: int) -> Profile:
    profile = session.get(Profile, profile_id)
    if profile is None or not viewer.can_see_artist(profile.artist_id):
        raise NotVisible(f"Profile {profile_id} not found")
    return profile


def require_outreach(session: Session, viewer: Viewer, outreach_id: int) -> Outreach:
    outreach = session.get(Outreach, outreach_id)
    if outreach is None or not viewer.can_see_artist(outreach.profile.artist_id):
        raise NotVisible(f"Outreach {outreach_id} not found")
    return outreach


def require_admin(viewer: Viewer) -> None:
    if not viewer.is_admin:
        raise AdminOnly("Only admins can do that")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_access.py -q`

Expected: 19 passed.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check core/access.py tests && uv run ruff format core/access.py tests/test_access.py tests/factories.py
git add core/access.py tests/test_access.py tests/factories.py
git commit -m "feat: one module decides who can see what

Every route will ask core.access rather than filtering on its own, so a forgotten check
is a single place to look. Unseeable things raise the same error as missing ones.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Sign in by membership, checked on every request

**Files:**
- Create: `web/access.py`
- Modify: `web/auth.py`, `web/app.py`, `web/templating.py`, `web/templates/errors/access_denied.html`, `tests/web_helpers.py`, `tests/test_web_auth.py`
- Test: `tests/test_web_signin_access.py`

- [ ] **Step 1: Add client helpers for tests**

Append to `tests/web_helpers.py`. Add `from web.app import create_app` and `from web.db import get_db` to its imports:

```python
def app_client(session, google: FakeGoogle | None = None, **app_options) -> TestClient:
    """A test client whose routes use the rolled-back test session. Not signed in."""
    app = create_app(web_settings(), identity_provider=google or FakeGoogle(), **app_options)

    def use_test_session():
        yield session

    app.dependency_overrides[get_db] = use_test_session
    return TestClient(app, follow_redirects=False)


def member_client(session, email: str = "member@example.com", **app_options) -> TestClient:
    """A client signed in as `email`. Make them a member of an artist first, or sign-in is refused."""
    google = FakeGoogle()
    google.userinfo["email"] = email
    client = app_client(session, google, **app_options)
    sign_in(client)
    return client
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_web_signin_access.py`:

```python
"""Signing in needs an admin email or a place on an artist, and access is re-checked on every request."""

from sqlalchemy import delete, select

from core.models import ArtistMember, User
from tests.factories import make_artist, make_member, make_user
from tests.web_helpers import FakeGoogle, app_client, csrf_token, member_client

HTML = {"accept": "text/html"}


def test_a_member_of_an_artist_can_sign_in(session):
    make_member(session, make_artist(session), make_user(session, "nik@example.com"))

    client = member_client(session, "nik@example.com")

    assert client.get("/", headers=HTML).status_code == 200


def test_signing_in_records_the_person(session):
    make_member(session, make_artist(session), make_user(session, "nik@example.com"))

    member_client(session, "Nik@Example.com")

    user = session.scalar(select(User).where(User.email == "nik@example.com"))
    assert user.name == "Owner"  # FakeGoogle's display name
    assert user.last_signed_in_at is not None


def test_an_admin_signs_in_without_a_membership_and_is_recorded(session):
    member_client(session, "owner@example.com")

    assert session.scalar(select(User).where(User.email == "owner@example.com")) is not None


def test_someone_on_no_artist_is_refused(session):
    make_user(session, "former@example.com")
    google = FakeGoogle()
    google.userinfo["email"] = "former@example.com"
    client = app_client(session, google)
    client.get("/auth/google")

    response = client.get("/auth/callback?state=fake&code=fake", headers=HTML)

    assert response.status_code == 403
    assert client.get("/", headers=HTML).status_code == 303


def test_taking_someone_off_their_artist_stops_them_on_the_next_click(session):
    user = make_member(session, make_artist(session), make_user(session, "nik@example.com"))
    client = member_client(session, "nik@example.com")

    session.execute(delete(ArtistMember).where(ArtistMember.user_id == user.id))
    response = client.get("/profiles", headers=HTML)

    assert response.status_code == 403
    assert "not allowed" in response.text.lower()
    assert client.get("/", headers=HTML).status_code == 303  # the session was cleared


def test_an_htmx_request_after_removal_is_sent_back_to_login(session):
    user = make_member(session, make_artist(session), make_user(session, "nik@example.com"))
    client = member_client(session, "nik@example.com")

    session.execute(delete(ArtistMember).where(ArtistMember.user_id == user.id))
    response = client.get("/runs/status", headers={"hx-request": "true"})

    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login"


def test_signing_out_still_works_after_removal(session):
    user = make_member(session, make_artist(session), make_user(session, "nik@example.com"))
    client = member_client(session, "nik@example.com")
    token = csrf_token(client)

    session.execute(delete(ArtistMember).where(ArtistMember.user_id == user.id))
    response = client.post("/logout", headers={"x-csrf-token": token, "hx-request": "true"})

    assert response.status_code == 200
    assert response.headers["hx-redirect"] == "/login"
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_web_signin_access.py -q`

Expected:
- **Members are refused.** `test_a_member_of_an_artist_can_sign_in` fails with 403, because sign-in still checks `ALLOWED_EMAILS` only.
- **Nobody is recorded.** The recording tests fail with `user` being `None`.
- **Removal isn't noticed.** The removal tests fail with 200, because there's no per-request check yet.

- [ ] **Step 4: Write `web/access.py`**

```python
"""The viewer behind each request, and how access problems are answered.

`current_viewer` rebuilds the viewer from the database on every request. `web/app.py` includes
every router except sign-in with it as a dependency, so even a route that never mentions the
viewer refuses someone who has been taken off all their artists. The answers:

- signed in, but no access any more: the session is cleared and the access-denied page shown
  (htmx requests are sent back to /login);
- something on another artist, or missing: 404 with the normal not-found page;
- an admin-only action by a member: 403.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from core.access import AdminOnly, NotVisible, Viewer, viewer_for
from web.db import get_db
from web.sessions import current_user
from web.templating import templates


class NoAccess(Exception):
    """The signed-in email has no access (any more)."""


def current_viewer(request: Request, db: Annotated[Session, Depends(get_db)]) -> Viewer:
    user = current_user(request) or {}
    viewer = viewer_for(db, str(user.get("email") or ""), request.app.state.settings.allowed_email_set)
    if viewer is None:
        raise NoAccess()
    return viewer


CurrentViewer = Annotated[Viewer, Depends(current_viewer)]


def admin_viewer(viewer: CurrentViewer) -> Viewer:
    if not viewer.is_admin:
        raise AdminOnly("Only admins can do that")
    return viewer


AdminViewer = Annotated[Viewer, Depends(admin_viewer)]


def install_access_handlers(app: FastAPI) -> None:
    @app.exception_handler(NoAccess)
    async def no_access(request: Request, error: NoAccess) -> Response:
        request.session.clear()
        if request.headers.get("hx-request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return templates.TemplateResponse(request, "errors/access_denied.html", status_code=403)

    @app.exception_handler(NotVisible)
    async def not_visible(request: Request, error: NotVisible) -> Response:
        if "text/html" in request.headers.get("accept", ""):
            return templates.TemplateResponse(request, "errors/not_found.html", status_code=404)
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    @app.exception_handler(AdminOnly)
    async def admin_only(request: Request, error: AdminOnly) -> Response:
        return JSONResponse({"detail": str(error)}, status_code=403)
```

- [ ] **Step 5: Admit members at sign-in and record the person**

In `web/auth.py`, add these imports:

```python
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from core.access import record_sign_in, viewer_for
from web.db import get_db
```

Add `DbSession = Annotated[Session, Depends(get_db)]` after `router = APIRouter()`.

Replace `auth_callback`'s signature, and its checks up to the `next_url` line:

```python
@router.get("/auth/callback", name="auth_callback", include_in_schema=False)
async def auth_callback(request: Request, db: DbSession) -> Response:
    try:
        token = await request.app.state.identity_provider.authorize_access_token(request)
    except OAuthError as error:
        logger.warning("Google sign-in did not complete: %s", error.error)
        return templates.TemplateResponse(request, "errors/sign_in_failed.html", status_code=400)

    userinfo = token.get("userinfo") or {}
    email = str(userinfo.get("email") or "").strip().lower()
    verified = userinfo.get("email_verified") is True
    admins = request.app.state.settings.allowed_email_set
    admitted = verified and await run_in_threadpool(_admit, db, admins, email, userinfo)
    if not admitted:
        logger.warning("Refused sign-in for %s (email verified: %s)", email or "<no email>", verified)
        return templates.TemplateResponse(request, "errors/access_denied.html", status_code=403)
```

Keep the rest of the function as it is. Add below it:

```python
def _admit(db: Session, admins: frozenset[str], email: str, userinfo: dict) -> bool:
    """True if this email may come in (admin or member); records the sign-in when it may."""
    if viewer_for(db, email, admins) is None:
        return False
    record_sign_in(
        db, email=email, name=userinfo.get("name"), picture_url=userinfo.get("picture"), now=datetime.now(UTC)
    )
    db.commit()
    return True
```

Update the module docstring's policy sentence to: `Our part is the policy: only a *verified* email that is an admin (ALLOWED_EMAILS) or a member of an artist gets a session, ...`.

- [ ] **Step 6: Apply the viewer check to every router, and install the handlers**

In `web/app.py`:
- Add `from fastapi import Depends` to the FastAPI import.
- Add `from web.access import CurrentViewer, current_viewer, install_access_handlers`.
- Replace the router includes and the home route:

```python
    app.include_router(auth_router)  # sign-in and sign-out must work for someone without access
    signed_in = [Depends(current_viewer)]
    for router in (digest_router, profiles_router, profile_contents_router, suggestions_router, runs_router, budget_router):
        app.include_router(router, dependencies=signed_in)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "configured": True}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, viewer: CurrentViewer) -> Response:
        return templates.TemplateResponse(request, "home.html")
```

Then call `install_access_handlers(app)` directly after `app.state.suggester = ...`. The line above is 115 characters, so split the tuple over several lines to stay under 110.

- [ ] **Step 7: Let templates know whether the viewer is an admin**

In `web/templating.py`, replace `auth_context`:

```python
def auth_context(request: Request) -> dict:
    has_session = "session" in request.scope
    user = current_user(request)
    settings = getattr(request.app.state, "settings", None)
    admins = settings.allowed_email_set if settings is not None else frozenset()
    return {
        "current_user": user,
        "csrf_token": request.session.get(SESSION_CSRF) if has_session else None,
        "is_admin": bool(user) and str(user.get("email", "")).lower() in admins,
    }
```

`is_admin` only decides what to *show*. Every admin action is also refused on the server by `AdminViewer`.

- [ ] **Step 8: Reword the access-denied page**

Replace the `<h1>` and `<p class="message__body">` in `web/templates/errors/access_denied.html`:

```html
  <h1 id="message-title" class="message__title">This account is not allowed in</h1>
  <p class="message__body">
    Noble Hunter lets in people an admin has added to an artist. If you were just removed, ask the
    admin. If you use more than one Google account, switch to the one you were invited with and try again.
  </p>
```

- [ ] **Step 9: Give the sign-in tests a database**

The callback now reads the database, so the fixtures in `tests/test_web_auth.py` need the test session.

Add `from web.db import get_db` to its imports. Replace the `client` fixture:

```python
@pytest.fixture
def client(google, session):
    app = create_app(settings(), identity_provider=google)

    def use_test_session():
        yield session

    app.dependency_overrides[get_db] = use_test_session
    return TestClient(app, follow_redirects=False)
```

In `TestSessionCookie.test_cookie_is_httponly_lax_and_secure_in_production`:
- Add `session` to the parameters.
- Build the app into a variable, then set the same `dependency_overrides[get_db]` before creating the `TestClient`.

- [ ] **Step 10: Run the sign-in tests, then the whole suite**

Run: `uv run pytest tests/test_web_signin_access.py tests/test_web_auth.py -q`

Expected: all pass.

Run: `uv run pytest -q`

Expected: all pass. The web test fixtures sign in as `owner@example.com`, who is an admin, so nothing else changes.

- [ ] **Step 11: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add web/access.py web/auth.py web/app.py web/templating.py web/templates/errors/access_denied.html tests
git commit -m "feat: sign in by artist membership, re-checked on every request

Invited artists aren't in ALLOWED_EMAILS. Checking membership per request means taking
someone off an artist stops them on their next click, not when their cookie expires.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The People page

**Files:**
- Create: `web/people.py`, `web/templates/people/page.html`, `web/templates/people/_content.html`
- Modify: `web/app.py`, `web/templates/base.html`, `web/static/css/app.css`
- Test: `tests/test_web_people.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_people.py`:

```python
"""The People page: admins add people to artists, rename artists and take people off them."""

import pytest
from sqlalchemy import select

from core.models import Artist, ArtistMember, User
from tests.factories import make_artist, make_member, make_profile, make_user
from tests.web_helpers import csrf_token, member_client

HTML = {"accept": "text/html"}


@pytest.fixture
def admin(session):
    return member_client(session, "owner@example.com")


def post(client, path: str, data: dict | None = None):
    return client.post(path, data=data or {}, headers={"x-csrf-token": csrf_token(client), "hx-request": "true"})


class TestPage:
    def test_admins_see_artists_members_and_profile_counts(self, admin, session):
        artist = make_artist(session, "Synman")
        make_member(session, artist, make_user(session, "nik@example.com"))
        make_profile(session, artist=artist)

        html = admin.get("/people", headers=HTML).text

        assert "Synman" in html
        assert "nik@example.com" in html
        assert "1 profile" in html
        assert "hasn't signed in yet" in html

    def test_admins_get_a_people_link_and_members_do_not(self, admin, session):
        assert 'href="/people"' in admin.get("/", headers=HTML).text

        make_member(session, make_artist(session), make_user(session, "nik@example.com"))
        member = member_client(session, "nik@example.com")
        assert 'href="/people"' not in member.get("/", headers=HTML).text

    def test_members_are_refused(self, session):
        make_member(session, make_artist(session), make_user(session, "nik@example.com"))
        member = member_client(session, "nik@example.com")

        assert member.get("/people", headers=HTML).status_code == 403
        assert post(member, "/people/members", {"email": "x@example.com", "new_artist_name": "X"}).status_code == 403

    def test_names_are_escaped(self, admin, session):
        make_artist(session, "<b>Loud</b>")

        html = admin.get("/people", headers=HTML).text

        assert "<b>Loud</b>" not in html and "&lt;b&gt;Loud&lt;/b&gt;" in html


class TestAddPerson:
    def test_adding_to_an_existing_artist(self, admin, session):
        artist = make_artist(session, "Synman")

        response = post(admin, "/people/members", {"email": "Nik@Example.com", "artist_id": str(artist.id)})

        assert response.status_code == 200
        assert "Added nik@example.com to Synman" in response.text
        user = session.scalar(select(User).where(User.email == "nik@example.com"))
        assert session.get(ArtistMember, (artist.id, user.id)).added_by == "owner@example.com"

    def test_adding_with_a_new_artist(self, admin, session):
        response = post(
            admin, "/people/members", {"email": "nik@example.com", "artist_id": "new", "new_artist_name": "Nik Beats"}
        )

        assert response.status_code == 200
        assert session.scalar(select(Artist).where(Artist.name == "Nik Beats")) is not None

    def test_problems_are_shown_in_place_with_the_typed_email(self, admin):
        response = post(admin, "/people/members", {"email": "not-an-email", "artist_id": "new"})

        assert response.status_code == 422
        assert "Enter a valid email address" in response.text
        assert "Choose an artist or name a new one" in response.text
        assert 'value="not-an-email"' in response.text


class TestArtistActions:
    def test_rename(self, admin, session):
        artist = make_artist(session, "Old")

        response = post(admin, f"/people/artists/{artist.id}/rename", {"name": "New"})

        assert response.status_code == 200
        assert session.get(Artist, artist.id).name == "New"

    def test_renaming_to_a_taken_name_is_explained(self, admin, session):
        make_artist(session, "Taken")
        artist = make_artist(session, "Mine")

        response = post(admin, f"/people/artists/{artist.id}/rename", {"name": "taken"})

        assert response.status_code == 422
        assert "There's already an artist called" in response.text

    def test_renaming_a_missing_artist_is_404(self, admin):
        assert post(admin, "/people/artists/999999/rename", {"name": "X"}).status_code == 404

    def test_removing_a_member(self, admin, session):
        artist = make_artist(session, "Synman")
        user = make_member(session, artist, make_user(session, "nik@example.com"))

        response = post(admin, f"/people/artists/{artist.id}/members/{user.id}/remove")

        assert response.status_code == 200
        assert "Took nik@example.com off Synman" in response.text
        assert session.get(ArtistMember, (artist.id, user.id)) is None

    def test_removing_someone_not_on_the_artist_is_404(self, admin, session):
        artist, user = make_artist(session), make_user(session)

        assert post(admin, f"/people/artists/{artist.id}/members/{user.id}/remove").status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_web_people.py -q`

Expected: failures with 404 for `/people`, because the route doesn't exist.

- [ ] **Step 3: Write `web/people.py`**

```python
"""The People page, for admins: who is on which artist.

Every form swaps the whole page content back in, so the lists always match the database after
an add, rename or removal. Problems come back as 422 with what was typed kept. Adding someone
sends no email: Jarred tells them, and they sign in with that Google account.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.models import Artist, User
from core.people import PeopleValidationError, add_member, list_artists, remove_member, rename_artist
from web.access import AdminViewer
from web.db import get_db
from web.templating import templates

router = APIRouter(prefix="/people")
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]
NEW_ARTIST = "new"


@router.get("")
def people_page(request: Request, db: DbSession, viewer: AdminViewer) -> Response:
    return templates.TemplateResponse(request, "people/page.html", _context(request, db))


@router.post("/members")
def add(
    request: Request,
    db: DbSession,
    viewer: AdminViewer,
    email: FormText = "",
    artist_id: FormText = "",
    new_artist_name: FormText = "",
) -> Response:
    form = {"email": email, "artist_id": artist_id, "new_artist_name": new_artist_name}
    try:
        user = add_member(
            db,
            email=email,
            artist_id=_chosen_artist(artist_id),
            new_artist_name=new_artist_name,
            added_by=viewer.email,
        )
        db.commit()
    except PeopleValidationError as error:
        return _content(request, db, form=form, errors=error.errors, status_code=422)
    artist = _artist_named_for(db, artist_id, new_artist_name)
    return _content(request, db, notice=f"Added {user.email} to {artist}. They can sign in with that Google account.")


@router.post("/artists/{artist_id}/rename")
def rename(request: Request, artist_id: int, db: DbSession, viewer: AdminViewer, name: FormText = "") -> Response:
    try:
        artist = rename_artist(db, artist_id, name)
        db.commit()
    except LookupError:
        raise HTTPException(status_code=404) from None
    except PeopleValidationError as error:
        return _content(request, db, rename_errors={artist_id: error.errors["name"]}, status_code=422)
    return _content(request, db, notice=f"Renamed to {artist.name}.")


@router.post("/artists/{artist_id}/members/{user_id}/remove")
def remove(request: Request, artist_id: int, user_id: int, db: DbSession, viewer: AdminViewer) -> Response:
    artist, user = db.get(Artist, artist_id), db.get(User, user_id)
    try:
        remove_member(db, artist_id, user_id)
        db.commit()
    except LookupError:
        raise HTTPException(status_code=404) from None
    return _content(request, db, notice=f"Took {user.email} off {artist.name}.")


def _chosen_artist(raw: str) -> int | None:
    """None means "a new artist"; anything unreadable becomes 0, which matches no artist."""
    text = raw.strip()
    if text in ("", NEW_ARTIST):
        return None
    return int(text) if text.isdigit() else 0


def _artist_named_for(db: Session, raw_artist_id: str, new_artist_name: str) -> str:
    chosen = _chosen_artist(raw_artist_id)
    artist = db.get(Artist, chosen) if chosen else None
    return artist.name if artist is not None else " ".join(new_artist_name.split())


def _context(request: Request, db: Session, **extra) -> dict:
    return {
        "artists": list_artists(db),
        "admin_emails": sorted(request.app.state.settings.allowed_email_set),
        "now": datetime.now(UTC),
        "form": {"email": "", "artist_id": "", "new_artist_name": ""},
        "errors": {},
        "rename_errors": {},
        "notice": None,
        **extra,
    }


def _content(request: Request, db: Session, *, status_code: int = 200, **extra) -> Response:
    return templates.TemplateResponse(
        request, "people/_content.html", _context(request, db, **extra), status_code=status_code
    )
```

In `web/app.py`, add `from web.people import router as people_router` and include `people_router` in the `for router in (...)` loop.

- [ ] **Step 4: Write the templates**

Create `web/templates/people/page.html`:

```html
{% extends "base.html" %}

{% block title %}People · Noble Hunter{% endblock %}

{% block content %}
<section class="page-heading rise">
  <p class="eyebrow">People</p>
  <h1 class="page-heading__title">Who works on which artist</h1>
  <p class="page-heading__lede">
    People see only their own artists' profiles and digests. Add someone here, then tell them to sign in
    with that Google account. Nothing is emailed.
  </p>
</section>
{% include "people/_content.html" %}
{% endblock %}
```

Create `web/templates/people/_content.html`:

```html
{# Swapped back in whole after every add, rename or removal. #}
<div id="people-content" class="split-layout">
  <section aria-labelledby="artists-heading">
    <h2 id="artists-heading" class="visually-hidden">Artists</h2>
    {% if notice %}<p class="notice" role="status">{{ notice }}</p>{% endif %}

    {% if artists %}
    <ul class="people-list" role="list">
      {% for artist in artists %}
      <li class="card people-artist rise" aria-labelledby="artist-{{ artist.id }}-name">
        <div class="people-artist__header">
          <h3 class="card__title" id="artist-{{ artist.id }}-name">{{ artist.name }}</h3>
          <span class="tag">{{ artist.profile_count }} {{ "profile" if artist.profile_count == 1 else "profiles" }}</span>
        </div>

        {% if artist.members %}
        <ul class="people-members" role="list">
          {% for member in artist.members %}
          <li class="people-member">
            <span class="people-member__who">
              <span class="people-member__email">{{ member.email }}</span>
              <span class="people-member__seen">
                {% if member.last_signed_in_at %}signed in {{ relative_time(member.last_signed_in_at, now) }}{% else %}hasn't signed in yet{% endif %}
              </span>
            </span>
            <button class="button button--ghost button--small" type="button"
                    hx-post="/people/artists/{{ artist.id }}/members/{{ member.user_id }}/remove"
                    hx-target="#people-content" hx-swap="outerHTML" hx-disabled-elt="this"
                    hx-confirm="Take {{ member.email }} off {{ artist.name }}?">
              Remove <span class="visually-hidden">{{ member.email }}</span>
              <span class="htmx-indicator spinner" aria-hidden="true"></span>
            </button>
          </li>
          {% endfor %}
        </ul>
        {% else %}
        <p class="field__hint">No members yet.</p>
        {% endif %}

        <form class="form form--compact" hx-post="/people/artists/{{ artist.id }}/rename"
              hx-target="#people-content" hx-swap="outerHTML" hx-disabled-elt="find button" novalidate>
          <div class="form__row">
            <label class="visually-hidden" for="rename-{{ artist.id }}">New name for {{ artist.name }}</label>
            <input class="input" id="rename-{{ artist.id }}" name="name" value="{{ artist.name }}" maxlength="80"
                   autocomplete="off"{% if rename_errors.get(artist.id) %} aria-invalid="true" aria-describedby="rename-{{ artist.id }}-error"{% endif %}>
            <button class="button button--small" type="submit">Rename <span class="htmx-indicator spinner" aria-hidden="true"></span></button>
          </div>
          {% if rename_errors.get(artist.id) %}<p class="field__error" id="rename-{{ artist.id }}-error">{{ rename_errors.get(artist.id) }}</p>{% endif %}
        </form>
      </li>
      {% endfor %}
    </ul>
    {% else %}
    <div class="card empty-state rise">
      <h2 class="empty-state__title">No artists yet</h2>
      <p class="empty-state__body">Add the first person and name their artist at the same time.</p>
    </div>
    {% endif %}
  </section>

  <div class="stack">
    <aside class="card rise" aria-labelledby="add-person-heading">
      <h2 id="add-person-heading" class="card__title">Add a person</h2>
      <form class="form" hx-post="/people/members" hx-target="#people-content" hx-swap="outerHTML"
            hx-disabled-elt="find button" novalidate>
        <div class="field">
          <label class="field__label" for="person-email">Google account email</label>
          <input class="input" id="person-email" name="email" type="email" value="{{ form.email }}" autocomplete="off" required
                 {% if errors.email %}aria-invalid="true" aria-describedby="person-email-error"{% endif %}>
          {% if errors.email %}<p class="field__error" id="person-email-error">{{ errors.email }}</p>{% endif %}
        </div>
        <div class="field">
          <label class="field__label" for="person-artist">Artist</label>
          <select class="input" id="person-artist" name="artist_id"
                  {% if errors.artist %}aria-invalid="true" aria-describedby="person-artist-error"{% endif %}>
            <option value="new"{% if form.artist_id in ("", "new") %} selected{% endif %}>New artist…</option>
            {% for artist in artists %}
            <option value="{{ artist.id }}"{% if form.artist_id == artist.id | string %} selected{% endif %}>{{ artist.name }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="field">
          <label class="field__label" for="person-new-artist">New artist's name</label>
          <input class="input" id="person-new-artist" name="new_artist_name" value="{{ form.new_artist_name }}" maxlength="80"
                 autocomplete="off" aria-describedby="person-new-artist-hint">
          <p class="field__hint" id="person-new-artist-hint">Only needed when you chose New artist.</p>
          {% if errors.artist %}<p class="field__error" id="person-artist-error">{{ errors.artist }}</p>{% endif %}
        </div>
        <div class="form__actions">
          <button class="button button--primary" type="submit">Add person <span class="htmx-indicator spinner" aria-hidden="true"></span></button>
        </div>
      </form>
    </aside>

    <aside class="card rise" aria-labelledby="admins-heading">
      <h2 id="admins-heading" class="card__title">Admins</h2>
      <p class="field__hint">Admins see every artist. They're set in ALLOWED_EMAILS on Vercel, not here.</p>
      <ul class="people-members" role="list">
        {% for email in admin_emails %}<li class="people-member"><span class="people-member__email">{{ email }}</span></li>{% endfor %}
      </ul>
    </aside>
  </div>
</div>
```

In `web/templates/base.html`, add the link after the Profiles link:

```html
      {% if is_admin %}<a class="site-nav__link" href="/people">People</a>{% endif %}
```

Append to `web/static/css/app.css`, just above `/* ---- Small screens`:

```css
/* ---- People ------------------------------------------------------------------------------ */

.people-list {
  display: grid;
  gap: var(--space-4);
  margin: 0;
  padding: 0;
  list-style: none;
}

.people-artist__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.people-members {
  display: grid;
  gap: var(--space-2);
  margin: var(--space-3) 0;
  padding: 0;
  list-style: none;
}

.people-member {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  transition: border-color var(--duration) var(--ease-out);
}

.people-member:hover,
.people-member:focus-within {
  border-color: var(--color-border-strong);
}

.people-member__who {
  display: grid;
  min-width: 0;
}

.people-member__email {
  overflow-wrap: anywhere;
}

.people-member__seen {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_web_people.py -q`

Expected: 12 passed.

If `test_names_are_escaped` fails because `hx-confirm` shows the raw name, check that the attribute uses `{{ }}` (autoescaped), not `| safe`.

- [ ] **Step 6: Check it in a browser**

Start the app locally: `uv run uvicorn web.app:app --reload`, with `.env.local` in place. Open http://127.0.0.1:8000/people signed in as the admin. Check:
- **Adding:** add a person with a new artist.
- **Renaming:** rename the artist.
- **Removing:** remove the person, confirming the dialog.
- **Layout:** it's usable at 375px wide, and keyboard focus is visible on every control.

Stop the server afterwards.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add web/people.py web/app.py web/templates/people web/templates/base.html web/static/css/app.css tests/test_web_people.py
git commit -m "feat: People page for admins to add and remove artists' members

Inviting an artist no longer means editing ALLOWED_EMAILS on Vercel and redeploying.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Run now and the Claude budget are admin-only

**Files:**
- Modify: `web/runs.py`, `web/budget.py`, `web/profiles.py`, `web/templates/runs/_panel.html`, `web/templates/profiles/list.html`
- Test: `tests/test_web_admin_only.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_admin_only.py`:

```python
"""Run now and the nightly Claude budget spend everyone's money, so only admins get them."""

from decimal import Decimal

from sqlalchemy import select

from core.app_settings import nightly_claude_budget
from core.models import RunRequest
from tests.factories import make_artist, make_member, make_user
from tests.web_helpers import csrf_token, member_client

HTML = {"accept": "text/html"}


def a_member(session):
    make_member(session, make_artist(session), make_user(session, "nik@example.com"))
    return member_client(session, "nik@example.com")


def post(client, path: str, data: dict | None = None):
    return client.post(path, data=data or {}, headers={"x-csrf-token": csrf_token(client), "hx-request": "true"})


def test_members_see_the_runner_panel_without_run_now_or_the_budget(session):
    html = a_member(session).get("/profiles", headers=HTML).text

    assert 'id="run-status"' in html
    assert 'hx-post="/runs/request"' not in html
    assert 'hx-post="/settings/claude-budget"' not in html


def test_the_refreshed_panel_has_no_run_now_for_members(session):
    html = a_member(session).get("/runs/status", headers={"hx-request": "true"}).text

    assert 'hx-post="/runs/request"' not in html


def test_a_member_cannot_request_a_run(session):
    response = post(a_member(session), "/runs/request")

    assert response.status_code == 403
    assert session.scalars(select(RunRequest)).all() == []


def test_a_member_cannot_change_the_budget(session):
    response = post(a_member(session), "/settings/claude-budget", {"budget": "9.00"})

    assert response.status_code == 403
    assert nightly_claude_budget(session) == Decimal("2.00")


def test_an_admin_still_has_both(session):
    html = member_client(session, "owner@example.com").get("/profiles", headers=HTML).text

    assert 'hx-post="/runs/request"' in html
    assert 'hx-post="/settings/claude-budget"' in html
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_web_admin_only.py -q`

Expected: four failures (members still see and can use both); `test_an_admin_still_has_both` passes.

- [ ] **Step 3: Restrict the routes**

In `web/runs.py`:
- Import `from web.access import AdminViewer, CurrentViewer` and `from core.access import Viewer`.
- Remove the `current_user` import.
- Replace the routes and helpers:

```python
@router.get("/status")
def status_panel(request: Request, db: DbSession, viewer: CurrentViewer) -> Response:
    return _render_panel(request, db, viewer)


@router.post("/request")
def run_now(request: Request, db: DbSession, viewer: AdminViewer) -> Response:
    request_run(db, requested_by=viewer.email)
    db.commit()
    return _render_panel(request, db, viewer)


def panel_context(db: Session, viewer: Viewer) -> dict:
    now = datetime.now(UTC)
    return {"run_status_view": run_status(db, now), "now": now, "can_run_now": viewer.is_admin}


def _render_panel(request: Request, db: Session, viewer: Viewer) -> Response:
    context = {**panel_context(db, viewer), "refreshed": True}
    return templates.TemplateResponse(request, "runs/_panel.html", context)
```

In `web/budget.py`, import `from web.access import AdminViewer` and change the signature to `def save_budget(request: Request, db: DbSession, viewer: AdminViewer, budget: FormText = "") -> Response:`.

In `web/profiles.py`, import `from web.access import CurrentViewer`. Give `profiles_page` a `viewer: CurrentViewer` parameter, and call `panel_context(db, viewer)`.

- [ ] **Step 4: Hide the controls**

In `web/templates/runs/_panel.html`, replace the final branch of the actions block, from `{% else %}` through its `{% endif %}`:

```html
    {% elif can_run_now %}
    <button class="button button--primary" type="button" hx-post="/runs/request" hx-target="#run-status"
            hx-swap="outerHTML" hx-disabled-elt="this">
      Run now
      <span class="htmx-indicator spinner" aria-hidden="true"></span>
    </button>
    <p class="run-status__note">Searches for every active profile.</p>
    {% else %}
    <p class="run-status__note">The runner searches for every active profile each night.</p>
    {% endif %}
```

In `web/templates/profiles/list.html`, wrap the budget include:

```html
    {% if is_admin %}{% include "settings/_budget.html" %}{% endif %}
```

- [ ] **Step 5: Run the tests and the suite**

Run: `uv run pytest tests/test_web_admin_only.py tests/test_web_runs.py tests/test_web_budget.py -q`

Expected: all pass.

Run: `uv run pytest -q`

Expected: all pass.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add web/runs.py web/budget.py web/profiles.py web/templates/runs/_panel.html web/templates/profiles/list.html tests/test_web_admin_only.py
git commit -m "feat: only admins can start runs or change the Claude budget

Both spend money every artist shares, so invited members see the runner's status but
not the controls, and the server refuses them too.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Database grants, docs and shipping stage 1

**Files:**
- Modify: `core/db_roles.py`, `tests/test_db_roles.py`, `IMPLEMENTATION_PLAN.md`, `.claude/DEVELOPER_LOGS.md`

- [ ] **Step 1: Write the failing grant tests**

In `tests/test_db_roles.py`, add to `class TestWebRole`:

```python
    @pytest.mark.parametrize("table", ["users", "artists", "artist_members"])
    def test_can_manage_people(self, roles, table):
        assert all(can(roles, WEB, table, privilege) for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"))
```

Add to `class TestPipelineRole`:

```python
    @pytest.mark.parametrize("table", ["users", "artists", "artist_members"])
    def test_can_read_people(self, roles, table):
        assert can(roles, PIPELINE, table, "SELECT")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_db_roles.py -q`

Expected: `test_can_manage_people` fails for all three tables (INSERT not granted). `test_can_read_people` already passes, because the pipeline gets every data table.

- [ ] **Step 3: Grant**

In `core/db_roles.py`, add `"users", "artists", "artist_members",` to `WEB_EDITABLE_TABLES`. In the module docstring, change the web bullet to begin: `web (Vercel, public internet): read everything; manage people and artists, profile settings, ...`.

- [ ] **Step 4: Run the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: all pass, clean.

- [ ] **Step 5: Update `IMPLEMENTATION_PLAN.md`**

In the decisions table:
- **Change the "Web login" row** to: `Sign in with Google. Admins are listed in ALLOWED_EMAILS; other people are added to an artist on the People page | 2026-09-15`.
- **Add this row:** `| Several artists | Other artists can use Noble Hunter. Each person sees only their own artists' profiles, digests and mail; admins see everything. Curators are separate per artist (own 90-day window and bad-fit list; dead is shared) | 2026-09-15 |`.

Add a new stage at the end of the file:

```markdown
## Stage 11: Artists and access
Goal: Several artists use Noble Hunter without seeing each other's work or competing for curators.
Success Criteria: Plan tasks in docs/superpowers/plans/2026-09-15-artists-and-access.md pass; a member of one artist gets 404 for every route on another artist's data; the same curator can reach two artists' digests on one night.
Status: In Progress (stage 1 of 3 complete)
```

- [ ] **Step 6: Log the session**

Append to `.claude/DEVELOPER_LOGS.md`:

```markdown
## 2026-09-15: Making room for more than one artist

Jarred wants to invite other artists, and a shared app would have meant a shared inbox once email pitching lands. So before any email work, Noble Hunter learned who owns what. An *artist* now owns profiles, and *members* are the Google accounts that work on one. Admins are still whoever is in `ALLOWED_EMAILS`, and they see everything. Jarred's existing profiles moved under an artist called Synman in migration 0006. On an empty database it creates nothing, which keeps a stray artist out of every test database.

Access is decided in one place, `core/access.py`, and re-checked on every request by a dependency that wraps every router except sign-in. Being removed from your last artist therefore stops you on your next click, not when a 14-day cookie runs out. The migration couldn't create users, because `ALLOWED_EMAILS` lives in Vercel, so a user row is written at sign-in instead. A new People page replaces editing that variable and redeploying. Run now and the Claude budget became admin-only, because they spend everyone's money.
```

- [ ] **Step 7: Commit**

```bash
git add core/db_roles.py tests/test_db_roles.py IMPLEMENTATION_PLAN.md .claude/DEVELOPER_LOGS.md
git commit -m "chore: grants for people tables; record stage 1 of artists and access

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 8: Ship stage 1 (ask Jarred first)**

**Warning: stage 1 does not restrict pages by artist yet.** Profiles, the digest and verdicts
are all unfiltered until stage 2 ships. Any member added on the People page can see and edit
every artist's work, not just their own. Do not add a non-admin member until stage 2 has
shipped. Until then, only admins should be given access.

Ask Jarred for the go-ahead to migrate Neon and push. Then, run this from the **main
checkout**, not this worktree: a push from a feature branch has no upstream and at best
creates a Vercel preview, and the runner must be installed from the checkout that stays in
place, not one that can later be removed:

1. Confirm no pipeline run is in progress, and that it's well away from 02:00.
2. On `main`: `git merge --ff-only feature/artists-and-access`. Don't push yet.
3. `uv run alembic upgrade head`, then `uv run alembic current` (expect `0006`, head).
4. `uv run python -m pipeline.cli db grant` (grants for users, artists, artist_members).
5. `git push origin main`, then wait for Vercel's production deploy to finish.
6. `scripts/install-worker.sh install`, from the main checkout, to restart the runner on the new code.
7. Smoke test `/profiles`, `/people`, and signing in.

Between steps 3 and 5, the schema already requires `profiles.artist_id NOT NULL` but the old
code on Vercel hasn't deployed yet and doesn't send it. Don't create a profile or run
`profile import` during that window, or the insert will fail.

After Vercel deploys, sign in at noblehunter.vercel.app and check:
- `/profiles` still lists Synman's profiles.
- `/people` shows the Synman artist with no members.

Unless he says otherwise, add Jarred to Synman as a member there, so his own view stays correct if he's ever removed as an admin.

Before Jarred invites anyone, remind him to set the Google Cloud project's OAuth publishing status to **In production**. Otherwise every invitee must first be added as a Google test user. Sign-in only asks for email and profile, so there's no warning screen.

---

# Stage 2: Scope every route

### Task 9: Profiles list, create and pages follow the viewer

**Files:**
- Modify: `core/profiles.py`, `web/profiles.py`, `web/templates/profiles/list.html`, `web/static/css/app.css`, `tests/test_profiles_core.py` (line 148), `tests/test_profiles_artist.py`
- Test: `tests/test_profiles_visibility.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_profiles_visibility.py`:

```python
"""Members see and create profiles only on their own artists; admins see all of them, grouped by artist."""

from sqlalchemy import select

from core.models import Profile
from core.profiles import artist_choices, list_profiles
from tests.factories import admin_viewer, make_artist, make_member, make_profile, make_user, member_viewer
from tests.web_helpers import csrf_token, member_client

HTML = {"accept": "text/html"}


def test_a_member_lists_only_their_artists_profiles(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    make_profile(session, "Own profile", artist=mine)
    make_profile(session, "Their profile", artist=theirs)

    names = [summary.name for summary in list_profiles(session, member_viewer(mine))]

    assert names == ["Own profile"]


def test_admins_list_everything_ordered_by_artist_then_name(session):
    b_artist, a_artist = make_artist(session, "b artist"), make_artist(session, "A artist")
    make_profile(session, "zeta", artist=a_artist)
    make_profile(session, "alpha", artist=b_artist)
    make_profile(session, "Beta", artist=a_artist)

    rows = [
        (summary.artist_name, summary.name)
        for summary in list_profiles(session, admin_viewer())
        if summary.artist_name in ("A artist", "b artist")
    ]

    assert rows == [("A artist", "Beta"), ("A artist", "zeta"), ("b artist", "alpha")]


def test_artist_choices_follow_the_viewer(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")

    assert artist_choices(session, member_viewer(mine)) == [(mine.id, "Mine")]
    assert {(mine.id, "Mine"), (theirs.id, "Theirs")} <= set(artist_choices(session, admin_viewer()))


def a_member_of(session, artist):
    make_member(session, artist, make_user(session, "nik@example.com"))
    return member_client(session, "nik@example.com")


def test_the_profiles_page_hides_other_artists(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    make_profile(session, "Own profile", artist=mine)
    make_profile(session, "Their profile", artist=theirs)

    html = a_member_of(session, mine).get("/profiles", headers=HTML).text

    assert "Own profile" in html
    assert "Their profile" not in html
    assert "Theirs" not in html


def test_admins_see_artist_headings(session):
    make_profile(session, "Their profile", artist=make_artist(session, "Theirs"))

    html = member_client(session, "owner@example.com").get("/profiles", headers=HTML).text

    assert 'class="profile-group__title"' in html
    assert "Theirs" in html


def test_a_member_cannot_create_a_profile_on_another_artist(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    client = a_member_of(session, mine)

    response = client.post(
        "/profiles",
        data={"name": "Sneaky", "digest_target": "10", "artist_id": str(theirs.id)},
        headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
    )

    assert response.status_code == 404
    assert session.scalar(select(Profile).where(Profile.name == "Sneaky")) is None


def test_a_member_cannot_open_another_artists_profile(session):
    mine = make_artist(session, "Mine")
    theirs = make_profile(session, "Their profile", artist=make_artist(session, "Theirs"))

    assert a_member_of(session, mine).get(f"/profiles/{theirs.id}", headers=HTML).status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_profiles_visibility.py -q`

Expected: `TypeError: list_profiles() takes 1 positional argument but 2 were given` in the core tests, and 200s where 404s are expected in the web tests.

- [ ] **Step 3: Filter in `core/profiles.py`**

Add `from core.access import Viewer, visible_to`.

Add two fields to `ProfileSummary`, straight after `id`:

```python
    artist_id: int
    artist_name: str
```

Replace `list_profiles` and `artist_choices`:

```python
def list_profiles(session: Session, viewer: Viewer) -> list[ProfileSummary]:
    profiles = session.scalars(
        select(Profile)
        .join(Artist, Artist.id == Profile.artist_id)
        .where(visible_to(viewer, Profile.artist_id))
        .options(
            selectinload(Profile.artist),
            selectinload(Profile.genres),
            selectinload(Profile.reference_artists),
            selectinload(Profile.tracks),
            selectinload(Profile.search_terms),
        )
        .order_by(func.lower(Artist.name), Artist.id, func.lower(Profile.name), Profile.id)
    )
    return [_summarise(profile) for profile in profiles]


def artist_choices(session: Session, viewer: Viewer) -> list[tuple[int, str]]:
    """The artists this viewer may create profiles under, as (id, name)."""
    rows = session.execute(
        select(Artist.id, Artist.name)
        .where(visible_to(viewer, Artist.id))
        .order_by(func.lower(Artist.name), Artist.id)
    )
    return [(artist_id, name) for artist_id, name in rows]
```

In `_summarise`, pass `artist_id=profile.artist_id, artist_name=profile.artist.name`.

Update the existing callers in tests:
- `tests/test_profiles_core.py` line 148: `list_profiles(session, admin_viewer())`.
- `tests/test_profiles_artist.py`: `artist_choices(session, admin_viewer())`.

Import `admin_viewer` from `tests.factories` in both files.

- [ ] **Step 4: Use the viewer in `web/profiles.py`**

Add these imports:

```python
from itertools import groupby

from core.access import NotVisible, Viewer, require_profile
```

Delete `_profile_or_404` and the now-unused `get_profile` import. Replace the routes and helpers from `profiles_page` down to `_change_status`:

```python
@router.get("")
def profiles_page(request: Request, db: DbSession, viewer: CurrentViewer) -> Response:
    context = {
        "profile_groups": _grouped(list_profiles(db, viewer)),
        "show_artists": _shows_artists(viewer),
        "form": {"name": "", "digest_target": DEFAULT_DIGEST_TARGET, "artist_id": ""},
        "errors": {},
        "artist_choices": artist_choices(db, viewer),
        **panel_context(db, viewer),
        **budget_context(db),
    }
    return templates.TemplateResponse(request, "profiles/list.html", context)


@router.post("")
def create(
    request: Request,
    db: DbSession,
    viewer: CurrentViewer,
    name: FormText = "",
    digest_target: FormText = "",
    artist_id: FormText = "",
) -> Response:
    chosen = _form_id(artist_id)
    if chosen and not viewer.can_see_artist(chosen):
        raise NotVisible(f"Artist {chosen} not found")
    try:
        profile = create_profile(db, chosen, name, digest_target)
        db.commit()
    except ProfileValidationError as error:
        context = {
            "form": {"name": name, "digest_target": digest_target, "artist_id": artist_id},
            "errors": error.errors,
            "artist_choices": artist_choices(db, viewer),
        }
        return templates.TemplateResponse(request, "profiles/_create_form.html", context, status_code=422)
    return _redirect(request, f"/profiles/{profile.id}")


@router.get("/{profile_id}")
def profile_page(request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    profile = require_profile(db, viewer, profile_id)
    context = {
        "profile": profile,
        "form": _settings_form(profile),
        "errors": {},
        "saved": False,
        "problems": activation_problems(profile),
    }
    return templates.TemplateResponse(request, "profiles/detail.html", context)


@router.post("/{profile_id}/settings")
def save_settings(
    request: Request,
    profile_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    name: FormText = "",
    digest_target: FormText = "",
    min_followers: FormText = "",
) -> Response:
    profile = require_profile(db, viewer, profile_id)
    try:
        # A blank floor (e.g. an older form) keeps the current value.
        update_profile_settings(db, profile_id, name, digest_target, min_followers.strip() or None)
        db.commit()
    except ProfileValidationError as error:
        typed = {"name": name, "digest_target": digest_target, "min_followers": min_followers}
        context = {"profile": profile, "form": typed, "errors": error.errors, "saved": False}
        return templates.TemplateResponse(request, "profiles/_settings_form.html", context, 422)
    context = {"profile": profile, "form": _settings_form(profile), "errors": {}, "saved": True}
    return templates.TemplateResponse(request, "profiles/_settings_form.html", context)


@router.post("/{profile_id}/activate")
def activate(request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    return _change_status(request, db, viewer, profile_id, active=True)


@router.post("/{profile_id}/pause")
def pause(request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    return _change_status(request, db, viewer, profile_id, active=False)


def _change_status(request: Request, db: Session, viewer: Viewer, profile_id: int, active: bool) -> Response:
    profile = require_profile(db, viewer, profile_id)
    status_code, refused = 200, False
    try:
        set_profile_active(db, profile_id, active)
        db.commit()
    except ProfileValidationError:
        status_code, refused = 422, True
    context = {"profile": profile, "problems": activation_problems(profile), "refused": refused}
    return templates.TemplateResponse(request, "profiles/_status.html", context, status_code=status_code)


def _grouped(summaries: list) -> list[tuple[str, list]]:
    """Profiles in (artist name, profiles) groups, keeping the list's order."""
    return [(artist_name, list(group)) for artist_name, group in groupby(summaries, key=lambda s: s.artist_name)]


def _shows_artists(viewer: Viewer) -> bool:
    return viewer.is_admin or len(viewer.artist_ids) > 1
```

Keep `_settings_form`, `_form_id` and `_redirect` as they are.

- [ ] **Step 5: Group the list template**

In `web/templates/profiles/list.html`, replace everything from `{% if profiles %}` to the matching `{% else %}` (the `<ul class="profile-list">` block):

```html
    {% if profile_groups %}
    {% for artist_name, profiles in profile_groups %}
    <div class="profile-group">
      {% if show_artists %}<h3 class="profile-group__title">{{ artist_name }}</h3>{% endif %}
      <ul class="profile-list" role="list">
        {% for profile in profiles %}
        <li class="rise">
          <a class="card profile-card" href="/profiles/{{ profile.id }}">
            <span class="profile-card__header">
              <span class="profile-card__name">{{ profile.name }}</span>
              {% include "profiles/_badge.html" %}
            </span>
            <span class="profile-card__meta">
              {{ profile.genre_count }} {{ 'genre' if profile.genre_count == 1 else 'genres' }} ·
              {{ profile.reference_artist_count }} reference {{ 'artist' if profile.reference_artist_count == 1 else 'artists' }} ·
              {{ profile.track_count }} {{ 'track' if profile.track_count == 1 else 'tracks' }} ·
              {{ profile.active_term_count }} active {{ 'term' if profile.active_term_count == 1 else 'terms' }} ·
              {{ profile.digest_target }} per digest
            </span>
            {% if not profile.ready %}
            <span class="profile-card__hint">
              {{ profile.problems | length }} {{ 'thing' if profile.problems | length == 1 else 'things' }} to add before it can run
            </span>
            {% endif %}
          </a>
        </li>
        {% endfor %}
      </ul>
    </div>
    {% endfor %}
```

Append to the People section of `web/static/css/app.css`:

```css
.profile-group + .profile-group {
  margin-top: var(--space-8);
}

.profile-group__title {
  margin: 0 0 var(--space-3);
  color: var(--color-text-muted);
  font-size: var(--text-sm);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
```

- [ ] **Step 6: Run the tests and the suite**

Run: `uv run pytest tests/test_profiles_visibility.py tests/test_web_profiles.py tests/test_profiles_core.py -q`

Expected: all pass.

Run: `uv run pytest -q`

Expected: all pass. If a template test fails on `profiles` being undefined, it's `list.html`: check the `{% for artist_name, profiles in profile_groups %}` line.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add core/profiles.py web/profiles.py web/templates/profiles/list.html web/static/css/app.css tests
git commit -m "feat: profiles pages show and change only the viewer's artists

Invited artists must not see or edit each other's profiles. Admins see every artist's
profiles under a heading.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Profile editing and Ask Claude check the profile's artist

**Files:**
- Modify: `web/profile_contents.py`, `web/suggestions.py`
- Test: `tests/test_web_contents_visibility.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_contents_visibility.py`:

```python
"""Editing a profile, and Ask Claude, work only on the viewer's own artists."""

from core.models import ProfileGenre
from tests.factories import make_artist, make_member, make_profile, make_user
from tests.profile_helpers import add_contents
from tests.web_helpers import csrf_token, member_client


def setup(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    own = add_contents(session, make_profile(session, "Own", artist=mine))
    other = add_contents(session, make_profile(session, "Other", artist=theirs))
    make_member(session, mine, make_user(session, "nik@example.com"))
    return member_client(session, "nik@example.com"), own, other


def post(client, path: str, data: dict):
    return client.post(path, data=data, headers={"x-csrf-token": csrf_token(client), "hx-request": "true"})


def test_a_member_edits_their_own_profile(session):
    client, own, _ = setup(session)

    assert post(client, f"/profiles/{own.id}/genres", {"tag": "Braindance"}).status_code == 200


def test_editing_another_artists_profile_is_404_and_changes_nothing(session):
    client, _, other = setup(session)

    response = post(client, f"/profiles/{other.id}/genres", {"tag": "Sneaky"})

    assert response.status_code == 404
    assert session.query(ProfileGenre).filter_by(profile_id=other.id, tag="Sneaky").count() == 0


def test_removing_from_another_artists_profile_is_404(session):
    client, _, other = setup(session)
    genre = other.genres[0]

    response = client.delete(
        f"/profiles/{other.id}/genres/{genre.id}", headers={"x-csrf-token": csrf_token(client), "hx-request": "true"}
    )

    assert response.status_code == 404
    assert session.get(ProfileGenre, genre.id) is not None


def test_ask_claude_about_another_artists_profile_is_404(session):
    client, _, other = setup(session)

    assert post(client, f"/profiles/{other.id}/suggest/genres", {"prompt": "More like this"}).status_code == 404
    assert post(client, f"/profiles/{other.id}/suggest/genres/add", {"choice": '{"value": "x"}'}).status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_web_contents_visibility.py -q`

Expected: the three "another artist" tests fail with 200 or 422 instead of 404.

- [ ] **Step 3: Rewrite `web/profile_contents.py`**

Replace the whole file with this version, where every route takes the viewer:

```python
"""Editing a profile's contents: one small route per action, one shared way of answering.

Every action first checks the profile is on one of the viewer's artists (404 if not), then
returns the section it changed plus the status panel as an htmx out-of-band swap, so the
readiness checklist and Active/Paused badge stay truthful without a reload. Validation
problems come back as a 422 section with the typed values kept.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core import profile_contents as contents
from core.access import Viewer, require_profile
from core.profiles import ProfileValidationError, activation_problems
from web.access import CurrentViewer
from web.db import get_db
from web.templating import templates

router = APIRouter(prefix="/profiles/{profile_id}")
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]


@router.post("/genres")
def add_genre(request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer, tag: FormText = "") -> Response:
    return _apply(
        request, db, viewer, profile_id, "genres", lambda: contents.add_genre(db, profile_id, tag), {"tag": tag}
    )


@router.post("/genres/{genre_id}/move")
def move_genre(
    request: Request, profile_id: int, genre_id: int, db: DbSession, viewer: CurrentViewer, direction: FormText = ""
) -> Response:
    def move():
        if direction not in contents.MOVE_DIRECTIONS:
            raise HTTPException(status_code=400, detail="direction must be up or down")
        contents.move_genre(db, profile_id, genre_id, direction)

    return _apply(request, db, viewer, profile_id, "genres", move)


@router.delete("/genres/{genre_id}")
def remove_genre(request: Request, profile_id: int, genre_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    return _apply(
        request, db, viewer, profile_id, "genres", lambda: contents.remove_genre(db, profile_id, genre_id)
    )


@router.post("/artists")
def add_artist(request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer, name: FormText = "") -> Response:
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "artists",
        lambda: contents.add_reference_artist(db, profile_id, name),
        {"name": name},
    )


@router.delete("/artists/{artist_id}")
def remove_artist(
    request: Request, profile_id: int, artist_id: int, db: DbSession, viewer: CurrentViewer
) -> Response:
    """`artist_id` here is a reference artist on the profile, not an Artist who owns profiles."""
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "artists",
        lambda: contents.remove_reference_artist(db, profile_id, artist_id),
    )


@router.post("/anti-signals")
def add_anti_signal(
    request: Request,
    profile_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    kind: FormText = "",
    value: FormText = "",
) -> Response:
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "anti_signals",
        lambda: contents.add_anti_signal(db, profile_id, kind, value),
        {"kind": kind, "value": value},
    )


@router.delete("/anti-signals/{signal_id}")
def remove_anti_signal(
    request: Request, profile_id: int, signal_id: int, db: DbSession, viewer: CurrentViewer
) -> Response:
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "anti_signals",
        lambda: contents.remove_anti_signal(db, profile_id, signal_id),
    )


@router.post("/tracks")
def add_track(
    request: Request,
    profile_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    title: FormText = "",
    spotify_url: FormText = "",
    description: FormText = "",
) -> Response:
    form = {"title": title, "spotify_url": spotify_url, "description": description}
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "tracks",
        lambda: contents.add_track(db, profile_id, title, spotify_url, description),
        form,
    )


@router.delete("/tracks/{track_id}")
def remove_track(request: Request, profile_id: int, track_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    return _apply(
        request, db, viewer, profile_id, "tracks", lambda: contents.remove_track(db, profile_id, track_id)
    )


@router.post("/terms")
def add_term(request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer, term: FormText = "") -> Response:
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "terms",
        lambda: contents.add_search_term(db, profile_id, term),
        {"term": term},
    )


@router.post("/terms/{term_id}/status")
def set_term_status(
    request: Request, profile_id: int, term_id: int, db: DbSession, viewer: CurrentViewer, status: FormText = ""
) -> Response:
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "terms",
        lambda: contents.set_search_term_status(db, profile_id, term_id, status),
    )


@router.delete("/terms/{term_id}")
def remove_term(request: Request, profile_id: int, term_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    return _apply(
        request, db, viewer, profile_id, "terms", lambda: contents.remove_search_term(db, profile_id, term_id)
    )


def _apply(
    request: Request,
    db: Session,
    viewer: Viewer,
    profile_id: int,
    section: str,
    action: Callable[[], object],
    form: dict | None = None,
) -> Response:
    profile = require_profile(db, viewer, profile_id)
    try:
        result = action()
        db.commit()
    except LookupError:
        raise HTTPException(status_code=404) from None
    except ProfileValidationError as error:
        return render_section(
            request, profile, section, errors=error.errors, form=form or {}, status_code=422
        )
    return render_section(request, profile, section, auto_paused=result is True)
```

Then append the existing `render_section` function unchanged.

Wrap any line longer than 110 characters, which `ruff format` will do.

- [ ] **Step 4: Check the profile in `web/suggestions.py`**

- **Imports:** add `from core.access import require_profile` and `from web.access import CurrentViewer`. Delete `_profile_or_404` and the `get_profile` import.
- **Signatures:**
  - `def ask(request: Request, profile_id: int, section: str, db: DbSession, viewer: CurrentViewer, prompt: FormText = "") -> Response:`
  - `def add(request: Request, profile_id: int, section: str, db: DbSession, viewer: CurrentViewer, choice: FormList = None) -> Response:`
- **Bodies:** in both, the first two lines become:

```python
    profile = require_profile(db, viewer, profile_id)
    section = _known_section(section)
```

- [ ] **Step 5: Run the tests and the suite**

Run: `uv run pytest tests/test_web_contents_visibility.py tests/test_web_profile_contents.py tests/test_web_suggestions.py -q`

Expected: all pass.

Run: `uv run pytest -q`

Expected: all pass.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add web/profile_contents.py web/suggestions.py tests/test_web_contents_visibility.py
git commit -m "feat: profile editing and Ask Claude refuse other artists' profiles

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: The digest shows only the viewer's artists

**Files:**
- Modify: `core/digest_view.py`, `web/digest.py`, `web/templates/digest/page.html`, `web/static/css/app.css`, `tests/test_digest_view.py`
- Test: `tests/test_digest_visibility.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_digest_visibility.py`:

```python
"""Members see digest entries for their own artists only; admins see every artist, labelled."""

from datetime import date

from core.digest_view import digest_view
from core.models import Outreach
from tests.factories import (
    admin_viewer,
    make_artist,
    make_curator,
    make_member,
    make_outreach,
    make_profile,
    make_user,
    member_viewer,
)
from tests.web_helpers import csrf_token, member_client

NIGHT, EARLIER = date(2026, 9, 15), date(2026, 9, 10)


def entry(session, artist, day, brief):
    profile = make_profile(session, artist=artist)
    return make_outreach(session, make_curator(session), profile, day, brief_text=brief)


def test_a_member_sees_only_their_artists_entries_and_nights(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    entry(session, mine, NIGHT, "Mine tonight")
    entry(session, theirs, NIGHT, "Theirs tonight")
    entry(session, theirs, EARLIER, "Theirs earlier")

    view = digest_view(session, viewer=member_viewer(mine), digest_date=None, today=NIGHT)

    briefs = [item.brief for group in view.profiles for item in group.entries]
    assert briefs == ["Mine tonight"]
    assert view.earlier_date is None  # their earlier night isn't offered
    assert not view.show_artists


def test_admins_see_every_artist_labelled(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    entry(session, mine, NIGHT, "Mine tonight")
    entry(session, theirs, NIGHT, "Theirs tonight")

    view = digest_view(session, viewer=admin_viewer(), digest_date=NIGHT, today=NIGHT)

    labels = {group.artist_name for group in view.profiles}
    assert {"Mine", "Theirs"} <= labels
    assert view.show_artists


def test_a_member_cannot_record_a_verdict_on_another_artists_entry(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    other = entry(session, theirs, NIGHT, "Theirs tonight")
    make_member(session, mine, make_user(session, "nik@example.com"))
    client = member_client(session, "nik@example.com")

    response = client.post(
        f"/outreach/{other.id}/verdict",
        data={"verdict": "skip"},
        headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
    )

    assert response.status_code == 404
    assert session.get(Outreach, other.id).status == "new"


def test_the_digest_page_hides_other_artists(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    entry(session, mine, NIGHT, "Mine tonight")
    entry(session, theirs, NIGHT, "Theirs tonight")
    make_member(session, mine, make_user(session, "nik@example.com"))

    html = member_client(session, "nik@example.com").get("/digest", headers={"accept": "text/html"}).text

    assert "Mine tonight" in html
    assert "Theirs tonight" not in html
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_digest_visibility.py -q`

Expected: `TypeError: digest_view() got an unexpected keyword argument 'viewer'`, and the web tests fail with 200 and a visible brief.

- [ ] **Step 3: Filter in `core/digest_view.py`**

Add `from core.access import Viewer, visible_to`, and add `Artist` to the models import.

Change the dataclasses:

```python
@dataclass(frozen=True)
class ProfileDigest:
    artist_name: str
    profile_name: str
    entries: tuple[EntryView, ...]


@dataclass(frozen=True)
class DigestView:
    digest_date: date | None
    profiles: tuple[ProfileDigest, ...]
    counts: tuple[NightCount, ...]
    earlier_date: date | None
    later_date: date | None
    show_artists: bool = False
    # total and short properties unchanged
```

Replace `digest_view` and `_profiles`:

```python
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
            digest_date=None, profiles=(), counts=(), earlier_date=None, later_date=None, show_artists=show_artists
        )

    return DigestView(
        digest_date=chosen,
        profiles=_profiles(session, chosen, today, viewer),
        counts=_counts(session, chosen),
        earlier_date=next((day for day in dates if day < chosen), None),
        later_date=next((day for day in reversed(dates) if day > chosen), None),
        show_artists=show_artists,
    )


def _profiles(session: Session, chosen: date, today: date, viewer: Viewer) -> tuple[ProfileDigest, ...]:
    groups: dict[tuple[str, str], list[EntryView]] = {}
    rows = session.execute(
        _entry_rows()
        .join(Artist, Artist.id == Profile.artist_id)
        .add_columns(Artist.name)
        .where(Outreach.digest_date == chosen, visible_to(viewer, Profile.artist_id))
        .order_by(Outreach.id)
    )
    for outreach, profile, playlist, curator, artist_name in rows:
        groups.setdefault((artist_name, profile.name), []).append(
            _entry(session, outreach, profile, playlist, curator, today=today)
        )
    # Artists in name order; within one artist, profiles keep the pipeline's ranking order.
    ordered = sorted(groups.items(), key=lambda item: item[0][0].casefold())
    return tuple(ProfileDigest(artist, profile, tuple(entries)) for (artist, profile), entries in ordered)
```

Update the existing tests mechanically:

```bash
uv run python - <<'EOF'
from pathlib import Path

test_file = Path("tests/test_digest_view.py")
source = test_file.read_text()
source = source.replace("digest_view(session, ", "digest_view(session, viewer=admin_viewer(), digest_date=")
source = source.replace(
    "from tests.factories import ", "from tests.factories import admin_viewer, ", 1
)
test_file.write_text(source)
EOF
uv run ruff check --select I --fix tests/test_digest_view.py
```

- [ ] **Step 4: Use the viewer in `web/digest.py`**

Add `from core.access import Viewer, require_outreach` and `from web.access import CurrentViewer`. Replace the three routes and `_page`:

```python
@router.get("/digest")
def latest_digest(request: Request, db: DbSession, viewer: CurrentViewer) -> Response:
    return _page(request, db, None, viewer)


@router.get("/digest/{day}")
def digest_for_day(request: Request, day: str, db: DbSession, viewer: CurrentViewer) -> Response:
    try:
        chosen = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=404) from None
    return _page(request, db, chosen, viewer)


@router.post("/outreach/{outreach_id}/verdict")
def save_verdict(
    request: Request, outreach_id: int, db: DbSession, viewer: CurrentViewer, verdict: FormText = ""
) -> Response:
    require_outreach(db, viewer, outreach_id)
    now = datetime.now(UTC)
    try:
        record_verdict(db, outreach_id, verdict, now)
        db.commit()
    except LookupError:
        raise HTTPException(status_code=404) from None
    except ValueError:
        return _entry(request, db, outreach_id, now.date(), error=UNKNOWN_VERDICT, status_code=422)
    return _entry(request, db, outreach_id, now.date())


def _page(request: Request, db: Session, chosen: date | None, viewer: Viewer) -> Response:
    view = digest_view(db, chosen, today=datetime.now(UTC).date(), viewer=viewer)
    return templates.TemplateResponse(request, "digest/page.html", {"view": view, **_verdict_context()})
```

- [ ] **Step 5: Label groups with the artist**

In `web/templates/digest/page.html`, replace the group heading's inner content:

```html
    <h2 class="digest-group__title" id="digest-group-{{ loop.index }}">
      {% if view.show_artists %}<span class="digest-group__artist">{{ group.artist_name }}</span>{% endif %}
      {{ group.profile_name }} <span class="digest-group__count">{{ group.entries | length }}</span>
    </h2>
```

Append after `.digest-counts` in `web/static/css/app.css`:

```css
.digest-group__artist {
  display: block;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
```

- [ ] **Step 6: Run the tests and the suite**

Run: `uv run pytest tests/test_digest_visibility.py tests/test_digest_view.py tests/test_web_digest.py -q`

Expected: all pass.

Run: `uv run pytest -q`

Expected: all pass.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add core/digest_view.py web/digest.py web/templates/digest/page.html web/static/css/app.css tests
git commit -m "feat: the digest shows and records verdicts only for the viewer's artists

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Walk every route as an outsider, then ship stage 2

**Files:**
- Test: `tests/test_web_access.py`
- Modify: `IMPLEMENTATION_PLAN.md`, `.claude/DEVELOPER_LOGS.md`

- [ ] **Step 1: Write the route-walk test**

Create `tests/test_web_access.py`:

```python
"""Every route, walked by someone on a different artist: nothing of the other artist's can be seen or changed.

A new route must be added to ROUTES, or test_every_route_is_covered fails. That is the point:
access can't be forgotten quietly.
"""

from datetime import date

import pytest
from fastapi.routing import APIRoute

from core.models import AntiSignal, Outreach, Profile
from tests.factories import make_artist, make_curator, make_member, make_outreach, make_profile, make_user
from tests.profile_helpers import TRACK_URL, add_contents
from tests.web_helpers import csrf_token, member_client

OK, FORBIDDEN, NOT_FOUND = 200, 403, 404
NIGHT = date(2026, 9, 15)

# Usable without access to any artist: signing in and out, and the health check.
EXEMPT = {
    ("GET", "/login"),
    ("GET", "/auth/google"),
    ("GET", "/auth/callback"),
    ("POST", "/logout"),
    ("GET", "/health"),
}

ROUTES = {
    ("GET", "/"): OK,
    ("GET", "/profiles"): OK,
    ("POST", "/profiles"): NOT_FOUND,
    ("GET", "/profiles/{profile_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/settings"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/activate"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/pause"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/genres"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/genres/{genre_id}/move"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/genres/{genre_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/artists"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/artists/{artist_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/anti-signals"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/anti-signals/{signal_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/tracks"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/tracks/{track_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/terms"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/terms/{term_id}/status"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/terms/{term_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/suggest/{section}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/suggest/{section}/add"): NOT_FOUND,
    ("GET", "/digest"): OK,
    ("GET", "/digest/{day}"): OK,
    ("POST", "/outreach/{outreach_id}/verdict"): NOT_FOUND,
    ("GET", "/runs/status"): OK,
    ("POST", "/runs/request"): FORBIDDEN,
    ("POST", "/settings/claude-budget"): FORBIDDEN,
    ("GET", "/people"): FORBIDDEN,
    ("POST", "/people/members"): FORBIDDEN,
    ("POST", "/people/artists/{artist_id}/rename"): FORBIDDEN,
    ("POST", "/people/artists/{artist_id}/members/{user_id}/remove"): FORBIDDEN,
}

FORMS = {
    "/profiles": {"name": "Stolen", "digest_target": "10"},
    "/profiles/{profile_id}/settings": {"name": "Renamed", "digest_target": "10", "min_followers": "0"},
    "/profiles/{profile_id}/genres": {"tag": "Stolen"},
    "/profiles/{profile_id}/genres/{genre_id}/move": {"direction": "down"},
    "/profiles/{profile_id}/artists": {"name": "Stolen"},
    "/profiles/{profile_id}/anti-signals": {"kind": "term", "value": "stolen"},
    "/profiles/{profile_id}/tracks": {"title": "Stolen", "spotify_url": TRACK_URL, "description": ""},
    "/profiles/{profile_id}/terms": {"term": "stolen"},
    "/profiles/{profile_id}/terms/{term_id}/status": {"status": "paused"},
    "/profiles/{profile_id}/suggest/{section}": {"prompt": "More like this"},
    "/profiles/{profile_id}/suggest/{section}/add": {"choice": '{"value": "stolen"}'},
    "/outreach/{outreach_id}/verdict": {"verdict": "bad-fit"},
    "/settings/claude-budget": {"budget": "9.00"},
    "/people/members": {"email": "x@example.com", "artist_id": "new", "new_artist_name": "Stolen"},
    "/people/artists/{artist_id}/rename": {"name": "Stolen"},
}


@pytest.fixture
def world(session):
    """Another artist with a full profile and a digest entry, and a signed-in member of a different artist."""
    theirs = make_artist(session, "Their Artist")
    profile = add_contents(session, make_profile(session, "Their Profile", artist=theirs))
    session.add(AntiSignal(profile=profile, kind="term", value="lofi"))
    session.flush()
    owner = make_member(session, theirs, make_user(session, "them@example.com"))
    outreach = make_outreach(session, make_curator(session), profile, NIGHT, brief_text="Their secret brief")

    make_member(session, make_artist(session, "My Artist"), make_user(session, "me@example.com"))
    client = member_client(session, "me@example.com")
    ids = {
        "profile_id": profile.id,
        "genre_id": profile.genres[0].id,
        "reference_artist_id": profile.reference_artists[0].id,
        "signal_id": profile.anti_signals[0].id,
        "track_id": profile.tracks[0].id,
        "term_id": profile.search_terms[0].id,
        "section": "genres",
        "day": NIGHT.isoformat(),
        "outreach_id": outreach.id,
        "artist_id": theirs.id,
        "user_id": owner.id,
    }
    return client, ids, profile, outreach


def url_for(path: str, ids: dict) -> str:
    values = dict(ids)
    if path.startswith("/profiles/"):
        values["artist_id"] = ids["reference_artist_id"]  # on profile routes it's a reference artist
    return path.format(**values)


def call(client, method: str, path: str, ids: dict):
    form = dict(FORMS.get(path, {}))
    if path == "/profiles":
        form["artist_id"] = str(ids["artist_id"])
    headers = {"accept": "text/html", "x-csrf-token": csrf_token(client), "hx-request": "true"}
    return client.request(method, url_for(path, ids), data=form or None, headers=headers)


def test_every_route_is_covered(world):
    client, *_ = world
    registered = {
        (method, route.path) for route in client.app.routes if isinstance(route, APIRoute) for method in route.methods
    }

    assert registered - EXEMPT == set(ROUTES)


@pytest.mark.parametrize(("method", "path"), sorted(ROUTES))
def test_an_outsider_gets_the_expected_answer(world, method, path):
    client, ids, *_ = world

    response = call(client, method, path, ids)

    assert response.status_code == ROUTES[(method, path)], response.text[:300]


def test_walking_every_route_changes_nothing_of_theirs(world, session):
    client, ids, profile, outreach = world
    before = (profile.name, len(profile.genres), len(profile.search_terms), profile.is_active)

    for method, path in sorted(ROUTES):
        call(client, method, path, ids)

    session.expire_all()
    stored = session.get(Profile, profile.id)
    assert (stored.name, len(stored.genres), len(stored.search_terms), stored.is_active) == before
    assert session.get(Outreach, outreach.id).status == "new"


@pytest.mark.parametrize("path", ["/profiles", "/digest", f"/digest/{NIGHT.isoformat()}", "/"])
def test_pages_an_outsider_can_open_show_nothing_of_theirs(world, path):
    client, *_ = world

    html = client.get(path, headers={"accept": "text/html"}).text

    assert "Their Profile" not in html
    assert "Their secret brief" not in html
    assert "Their Artist" not in html
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_web_access.py -q`

Expected: all pass, because Tasks 5–11 already restrict every route.

A failure names the route and the status it gave. Fix that route to take `CurrentViewer`/`AdminViewer` and call `require_profile`/`require_outreach`; don't loosen the test.

If `test_every_route_is_covered` fails:
- **Listed, but no such route:** a path changed. Update `ROUTES`.
- **A route missing from `ROUTES`:** add it along with its expected answer.

- [ ] **Step 3: Prove the walk catches a forgotten check**

Temporarily delete the `require_outreach(db, viewer, outreach_id)` line from `web/digest.py`.

Run: `uv run pytest tests/test_web_access.py -q -k verdict`

Expected: FAIL, `assert 200 == 404` for `/outreach/{outreach_id}/verdict`.

Restore the line and run the tests again: PASS.

- [ ] **Step 4: Run the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: all pass, clean.

- [ ] **Step 5: Record progress**

In `IMPLEMENTATION_PLAN.md`, set Stage 11's status to `In Progress (stages 1 and 2 of 3 complete)`.

Append to `.claude/DEVELOPER_LOGS.md` under the 2026-09-15 entry:

```markdown
Stage 2 put every route behind the same question: is this on one of your artists? Profiles, profile editing, Ask Claude, the digest and verdicts all ask `core/access.py`. Anything on another artist answers 404, exactly like something that doesn't exist. The safety net is `tests/test_web_access.py`. Signed in as someone on a different artist, it calls every registered route with real ids from another artist's data and expects 404, or 403 for admin-only actions. It also fails if a route is added without being listed. Deleting a single `require_outreach` line made it fail, which is how we know it isn't just passing.
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_web_access.py IMPLEMENTATION_PLAN.md .claude/DEVELOPER_LOGS.md
git commit -m "test: walk every route as another artist's member

A single forgotten access check would leak one artist's profiles or digest to another.
The walk calls every route with the other artist's ids and fails on any new, unlisted route.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 7: Ship stage 2 (ask Jarred first)**

Stage 2 has no migration. With Jarred's go-ahead, run `git push`, and restart the runner with `scripts/install-worker.sh install`.

After Vercel deploys, check signed in as Jarred:
- `/profiles` and `/digest` look as before, with artist headings, because he's an admin.

---

# Stage 3: Curators per artist

### Task 13: Outreach belongs to an artist; bad fit gets its own table (migration 0007)

**Files:**
- Modify: `core/models.py`, `core/db_roles.py`, `pipeline/digest.py` (`_add_entry`), `tests/factories.py`, `tests/test_schema.py`, `tests/test_db_roles.py`
- Create: `migrations/versions/20260915_0007_curators_per_artist.py`
- Test: `tests/test_schema_curators_per_artist.py`, `tests/test_migration_0007.py`

- [ ] **Step 1: Check the constraint names the migration relies on**

Run: `grep -n "ck_curators_exclusion_reason\|ex_outreach_curator_cooldown" migrations/versions/20260913_0001_initial_schema.py`

Expected: both names appear. If the check constraint has another name, use that name in Step 5.

- [ ] **Step 2: Write the failing schema tests**

Create `tests/test_schema_curators_per_artist.py`:

```python
"""The 90-day rule and bad fit are per artist; dead is the only reason stored on the curator."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from core.models import ArtistCuratorExclusion
from tests.factories import make_artist, make_bad_fit, make_curator, make_outreach, make_profile

TODAY = date(2026, 9, 15)
NOW = datetime(2026, 9, 15, 2, 0, tzinfo=UTC)


def test_two_artists_can_have_the_same_curator_on_the_same_night(session):
    curator = make_curator(session)
    make_outreach(session, curator, make_profile(session, artist=make_artist(session)), TODAY)

    later = make_outreach(session, curator, make_profile(session, artist=make_artist(session)), TODAY)

    assert later.id is not None


def test_one_artist_still_cannot_have_a_curator_twice_within_90_days(session):
    artist, curator = make_artist(session), make_curator(session)
    make_outreach(session, curator, make_profile(session, artist=artist), TODAY)

    with pytest.raises(IntegrityError):
        make_outreach(session, curator, make_profile(session, artist=artist), TODAY + timedelta(days=89))


def test_outreach_needs_an_artist(session):
    entry = make_outreach(session, make_curator(session), make_profile(session), TODAY)
    entry.artist_id = None

    with pytest.raises(IntegrityError):
        session.flush()


def test_bad_fit_is_recorded_once_per_artist_and_curator(session):
    artist, curator = make_artist(session), make_curator(session)
    make_bad_fit(session, artist, curator)

    session.add(ArtistCuratorExclusion(artist_id=artist.id, curator_id=curator.id, reason="bad-fit", excluded_at=NOW))
    with pytest.raises(IntegrityError):
        session.flush()


def test_bad_fit_can_no_longer_be_stored_on_the_curator(session):
    with pytest.raises(IntegrityError):
        make_curator(session, excluded_at=NOW, exclusion_reason="bad-fit")


def test_dead_is_still_stored_on_the_curator(session):
    assert make_curator(session, excluded_at=NOW, exclusion_reason="dead").id is not None
```

In `tests/test_schema.py`, rename `test_same_curator_cannot_be_digested_twice_within_90_days_across_profiles` to `test_same_curator_cannot_be_digested_twice_within_90_days_across_one_artists_profiles`. Its body is unchanged, because both profiles use the default test artist.

- [ ] **Step 3: Write the failing data-migration test**

Create `tests/test_migration_0007.py`:

```python
"""Migration 0007 gives outreach its artist and moves bad-fit marks from curators to that artist."""

from sqlalchemy import text

from tests.migration_helpers import migrate_to


def test_outreach_and_bad_fit_marks_move_to_the_artist(engine):
    try:
        migrate_to("0006")
        with engine.begin() as connection:
            artist_id = connection.scalar(text("insert into artists (name) values ('Synman') returning id"))
            profile_id = connection.scalar(
                text("insert into profiles (name, artist_id) values ('IDM', :artist) returning id"),
                {"artist": artist_id},
            )
            bad = connection.scalar(
                text(
                    "insert into curators (display_name, excluded_at, exclusion_reason) "
                    "values ('Bad fit', now(), 'bad-fit') returning id"
                )
            )
            dead = connection.scalar(
                text(
                    "insert into curators (display_name, excluded_at, exclusion_reason) "
                    "values ('Dead', now(), 'dead') returning id"
                )
            )
            connection.execute(text("insert into playlists (spotify_id, name) values (:id, 'P')"), {"id": "7" * 22})
            connection.execute(
                text(
                    "insert into outreach (curator_id, playlist_id, profile_id, digest_date, brief_text) "
                    "values (:curator, :playlist, :profile, date '2026-09-01', 'Brief')"
                ),
                {"curator": bad, "playlist": "7" * 22, "profile": profile_id},
            )

        migrate_to("0007")

        with engine.connect() as connection:
            assert connection.scalar(text("select artist_id from outreach")) == artist_id
            exclusions = connection.execute(
                text("select artist_id, curator_id, reason from artist_curator_exclusions")
            ).all()
            assert [tuple(row) for row in exclusions] == [(artist_id, bad, "bad-fit")]
            curators = dict(connection.execute(text("select id, exclusion_reason from curators")).all())
            assert curators == {bad: None, dead: "dead"}
    finally:
        with engine.begin() as connection:
            connection.execute(text("truncate outreach, playlists, curators, profiles, artists restart identity cascade"))
        migrate_to("head")
```

- [ ] **Step 4: Run to verify failure**

Run: `uv run pytest tests/test_schema_curators_per_artist.py tests/test_migration_0007.py -q`

Expected: `ImportError: cannot import name 'make_bad_fit'`, and `Can't locate revision identified by '0007'`.

- [ ] **Step 5: Update the models**

In `core/models.py`, replace `ExclusionReason` and add `ArtistExclusionReason` below it:

```python
class ExclusionReason(StrEnum):
    """Why a curator is out for everyone. Bad fit is per artist since migration 0007."""

    DEAD = "dead"


class ArtistExclusionReason(StrEnum):
    BAD_FIT = "bad-fit"
```

Add below `class Curator`:

```python
class ArtistCuratorExclusion(Base):
    """A curator one artist never wants in their digest again. Other artists can still get them."""

    __tablename__ = "artist_curator_exclusions"
    __table_args__ = (one_of("reason", ArtistExclusionReason),)

    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), primary_key=True)
    curator_id: Mapped[int] = mapped_column(
        ForeignKey("curators.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    reason: Mapped[str] = mapped_column(String(20))
    excluded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

Make three more edits:
- **`Outreach`:** add `artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="RESTRICT"))` below `profile_id`.
- **`Outreach` docstring:** change it to `"""One digest entry. The 90-day per-artist, per-curator EXCLUDE constraint lives in the migrations."""`.
- **Module docstring:** change the outreach bullet to `- outreach: an EXCLUDE constraint so one artist can't have the same curator twice within 90 days (migration 0007).`

- [ ] **Step 6: Write the migration**

Create `migrations/versions/20260915_0007_curators_per_artist.py`:

```python
"""curators per artist: the 90-day window and bad fit belong to an artist

Jarred (2026-09-15): artists shouldn't compete for curators. Each artist gets their own 90-day
window and their own bad-fit list; "dead" (an abandoned playlist) stays shared. Existing bad-fit
marks move to the artist whose digest they came from.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-15 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("outreach", sa.Column("artist_id", sa.Integer(), nullable=True))
    op.execute("UPDATE outreach SET artist_id = profiles.artist_id FROM profiles WHERE profiles.id = outreach.profile_id")
    op.alter_column("outreach", "artist_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_outreach_artist_id_artists"), "outreach", "artists", ["artist_id"], ["id"], ondelete="RESTRICT"
    )
    # 90 is written literally on purpose: migrations are frozen history.
    op.execute("ALTER TABLE outreach DROP CONSTRAINT ex_outreach_curator_cooldown")
    op.execute(
        """
        ALTER TABLE outreach ADD CONSTRAINT ex_outreach_artist_curator_cooldown
        EXCLUDE USING gist (artist_id WITH =, curator_id WITH =, daterange(digest_date, digest_date + 90) WITH &&)
        """
    )

    op.create_table(
        "artist_curator_exclusions",
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("curator_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("excluded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reason in ('bad-fit')", name=op.f("ck_artist_curator_exclusions_reason")),
        sa.ForeignKeyConstraint(
            ["artist_id"], ["artists.id"], name=op.f("fk_artist_curator_exclusions_artist_id_artists"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["curator_id"],
            ["curators.id"],
            name=op.f("fk_artist_curator_exclusions_curator_id_curators"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("artist_id", "curator_id", name=op.f("pk_artist_curator_exclusions")),
    )
    op.create_index(op.f("ix_artist_curator_exclusions_curator_id"), "artist_curator_exclusions", ["curator_id"])

    # A bad-fit mark belongs to the artist whose digest entry it was recorded on.
    op.execute(
        """
        INSERT INTO artist_curator_exclusions (artist_id, curator_id, reason, excluded_at)
        SELECT DISTINCT outreach.artist_id, curators.id, 'bad-fit', curators.excluded_at
        FROM curators JOIN outreach ON outreach.curator_id = curators.id
        WHERE curators.exclusion_reason = 'bad-fit'
        """
    )
    # A bad-fit curator with no digest entry shouldn't exist; if one does, keep them out for every artist.
    op.execute(
        """
        INSERT INTO artist_curator_exclusions (artist_id, curator_id, reason, excluded_at)
        SELECT artists.id, curators.id, 'bad-fit', curators.excluded_at
        FROM curators CROSS JOIN artists
        WHERE curators.exclusion_reason = 'bad-fit'
          AND NOT EXISTS (SELECT 1 FROM outreach WHERE outreach.curator_id = curators.id)
        """
    )
    op.execute("UPDATE curators SET excluded_at = NULL, exclusion_reason = NULL WHERE exclusion_reason = 'bad-fit'")
    op.drop_constraint(op.f("ck_curators_exclusion_reason"), "curators", type_="check")
    op.create_check_constraint(op.f("ck_curators_exclusion_reason"), "curators", "exclusion_reason in ('dead')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_curators_exclusion_reason"), "curators", type_="check")
    op.create_check_constraint(
        op.f("ck_curators_exclusion_reason"), "curators", "exclusion_reason in ('bad-fit', 'dead')"
    )
    op.execute(
        """
        UPDATE curators SET excluded_at = marks.excluded_at, exclusion_reason = 'bad-fit'
        FROM (SELECT curator_id, min(excluded_at) AS excluded_at FROM artist_curator_exclusions GROUP BY curator_id) marks
        WHERE curators.id = marks.curator_id AND curators.exclusion_reason IS NULL
        """
    )
    op.drop_index(op.f("ix_artist_curator_exclusions_curator_id"), table_name="artist_curator_exclusions")
    op.drop_table("artist_curator_exclusions")
    op.execute("ALTER TABLE outreach DROP CONSTRAINT ex_outreach_artist_curator_cooldown")
    # Fails if two artists had the same curator within 90 days: that history can't fit the old rule.
    op.execute(
        """
        ALTER TABLE outreach ADD CONSTRAINT ex_outreach_curator_cooldown
        EXCLUDE USING gist (curator_id WITH =, daterange(digest_date, digest_date + 90) WITH &&)
        """
    )
    op.drop_constraint(op.f("fk_outreach_artist_id_artists"), "outreach", type_="foreignkey")
    op.drop_column("outreach", "artist_id")
```

Run `uv run ruff format migrations/versions/20260915_0007_curators_per_artist.py` to wrap the long lines.

- [ ] **Step 7: Factories, the digest's new entries, and grants**

In `tests/factories.py`:
- Add `ArtistCuratorExclusion` to the models import.
- In `make_outreach`, add `"artist_id": profile.artist_id,` to the field dict, before `**overrides`.
- Append:

```python
def make_bad_fit(session, artist: Artist, curator: Curator, when: datetime | None = None) -> ArtistCuratorExclusion:
    mark = ArtistCuratorExclusion(
        artist_id=artist.id, curator_id=curator.id, reason="bad-fit", excluded_at=when or now()
    )
    session.add(mark)
    session.flush()
    return mark
```

In `pipeline/digest.py` `_add_entry`, add `artist_id=candidate.profile.artist_id,` after `profile_id=...`.

In `core/db_roles.py`, add `"artist_curator_exclusions",` to `WEB_EDITABLE_TABLES`. In `tests/test_db_roles.py`, extend `test_can_manage_people`'s parametrize list with `"artist_curator_exclusions"`.

- [ ] **Step 8: Run the new tests**

Run: `uv run pytest tests/test_schema_curators_per_artist.py tests/test_migration_0007.py tests/test_schema.py tests/test_db_roles.py -q`

Expected: all pass.

- [ ] **Step 9: Don't commit yet**

`core.exclusion.record_verdict` still writes `bad-fit` onto curators, which the database now refuses. Task 14 fixes it and commits both tasks together.

---

### Task 14: Artist-aware exclusion rules

**Files:**
- Modify: `core/exclusion.py`, `pipeline/digest.py`, `pipeline/research.py`, `pipeline/report.py`
- Modify tests: `tests/test_exclusion.py`, `tests/test_digest.py` (around line 163), `tests/test_research.py` (around line 302), `tests/test_pipeline_report.py` (line 81), `tests/test_web_digest.py` (line 151)

- [ ] **Step 1: Rewrite the exclusion tests**

In `tests/test_exclusion.py`:
- Replace the module docstring's first sentence with: `Decisions (2026-09-13, per artist since 2026-09-15): a curator appears at most once per 90 days for each artist; pitched and skip wait out the 90 days; bad-fit excludes the curator from that artist for good, dead from everyone.`
- Replace the imports:

```python
from core.exclusion import contact_is_excluded, curator_is_eligible, playlist_ids_to_skip, record_verdict
from core.models import ArtistCuratorExclusion, PlaylistStatus, RejectionReason
from tests.factories import (
    make_artist,
    make_bad_fit,
    make_contact,
    make_curator,
    make_outreach,
    make_playlist,
    make_profile,
)
```

Replace `TestCuratorEligibility`, `TestRecordVerdict` and `TestContactExclusion` with the classes below. Leave `TestPlaylistsToSkip` as it is.

```python
class TestCuratorEligibility:
    def test_new_curator_is_eligible(self, session):
        assert curator_is_eligible(session, make_curator(session).id, make_artist(session).id, TODAY)

    def test_curator_digested_30_days_ago_is_not_eligible_for_that_artist(self, session):
        curator, profile = make_curator(session), make_profile(session)
        make_outreach(session, curator, profile, TODAY - timedelta(days=30))

        assert not curator_is_eligible(session, curator.id, profile.artist_id, TODAY)

    def test_another_artist_can_have_a_recently_digested_curator(self, session):
        curator = make_curator(session)
        make_outreach(session, curator, make_profile(session), TODAY - timedelta(days=30))

        assert curator_is_eligible(session, curator.id, make_artist(session).id, TODAY)

    def test_curator_digested_exactly_90_days_ago_is_eligible(self, session):
        curator, profile = make_curator(session), make_profile(session)
        make_outreach(session, curator, profile, TODAY - timedelta(days=90))

        assert curator_is_eligible(session, curator.id, profile.artist_id, TODAY)

    def test_a_dead_curator_is_never_eligible_for_anyone(self, session):
        curator = make_curator(session, excluded_at=at(TODAY - timedelta(days=400)), exclusion_reason="dead")

        assert not curator_is_eligible(session, curator.id, make_artist(session).id, TODAY)

    def test_bad_fit_blocks_only_that_artist_and_for_good(self, session):
        curator, blocked, other = make_curator(session), make_artist(session), make_artist(session)
        make_bad_fit(session, blocked, curator)

        assert not curator_is_eligible(session, curator.id, blocked.id, TODAY + timedelta(days=1000))
        assert curator_is_eligible(session, curator.id, other.id, TODAY)


class TestRecordVerdict:
    def test_pitched_records_status_and_time(self, session):
        outreach = make_outreach(session, make_curator(session), make_profile(session), TODAY)

        record_verdict(session, outreach.id, "pitched", at(TODAY))

        assert outreach.status == "pitched"
        assert outreach.pitched_at == at(TODAY)
        assert outreach.status_changed_at == at(TODAY)

    def test_skip_leaves_curator_eligible_after_90_days(self, session):
        curator, profile = make_curator(session), make_profile(session)
        outreach = make_outreach(session, curator, profile, TODAY)

        record_verdict(session, outreach.id, "skip", at(TODAY))

        assert curator.excluded_at is None
        assert curator_is_eligible(session, curator.id, profile.artist_id, TODAY + timedelta(days=90))

    def test_bad_fit_excludes_the_curator_for_this_artist_only(self, session):
        curator, profile = make_curator(session), make_profile(session)
        outreach = make_outreach(session, curator, profile, TODAY)

        record_verdict(session, outreach.id, "bad-fit", at(TODAY))

        mark = session.get(ArtistCuratorExclusion, (profile.artist_id, curator.id))
        assert (mark.reason, mark.excluded_at) == ("bad-fit", at(TODAY))
        assert curator.excluded_at is None
        assert curator_is_eligible(session, curator.id, make_artist(session).id, TODAY)

    def test_bad_fit_twice_is_recorded_once(self, session):
        outreach = make_outreach(session, make_curator(session), make_profile(session), TODAY)

        record_verdict(session, outreach.id, "bad-fit", at(TODAY))
        record_verdict(session, outreach.id, "bad-fit", at(TODAY + timedelta(days=1)))

        assert session.query(ArtistCuratorExclusion).filter_by(curator_id=outreach.curator_id).count() == 1

    def test_dead_excludes_the_curator_for_everyone(self, session):
        curator = make_curator(session)
        outreach = make_outreach(session, curator, make_profile(session), TODAY)

        record_verdict(session, outreach.id, "dead", at(TODAY))

        assert (curator.excluded_at, curator.exclusion_reason) == (at(TODAY), "dead")
        assert not curator_is_eligible(session, curator.id, make_artist(session).id, TODAY + timedelta(days=1000))

    def test_unknown_verdict_raises(self, session):
        outreach = make_outreach(session, make_curator(session), make_profile(session), TODAY)

        with pytest.raises(ValueError, match="Unknown verdict"):
            record_verdict(session, outreach.id, "maybe", at(TODAY))

    def test_missing_outreach_raises(self, session):
        with pytest.raises(LookupError, match="999999"):
            record_verdict(session, 999999, "skip", at(TODAY))


class TestContactExclusion:
    def test_unknown_contact_is_not_excluded(self, session):
        assert not contact_is_excluded(session, "email:new@label.com", None, TODAY, artist_ids=[make_artist(session).id])

    def test_contact_of_a_curator_recently_digested_for_the_artist_is_excluded(self, session):
        curator, profile = make_curator(session), make_profile(session)
        contact = make_contact(session, curator, value="curator@label.com")
        make_outreach(session, curator, profile, TODAY - timedelta(days=10))

        assert contact_is_excluded(session, contact.contact_key, None, TODAY, artist_ids=[profile.artist_id])
        assert not contact_is_excluded(
            session, contact.contact_key, None, TODAY, artist_ids=[make_artist(session).id]
        )

    def test_contact_of_curator_digested_long_ago_is_not_excluded(self, session):
        curator, profile = make_curator(session), make_profile(session)
        contact = make_contact(session, curator, value="curator@label.com")
        make_outreach(session, curator, profile, TODAY - timedelta(days=100))

        assert not contact_is_excluded(session, contact.contact_key, None, TODAY, artist_ids=[profile.artist_id])

    def test_contact_of_a_dead_curator_is_excluded_for_anyone_even_with_no_artists(self, session):
        curator = make_curator(session, excluded_at=at(TODAY - timedelta(days=500)), exclusion_reason="dead")
        contact = make_contact(session, curator, value="curator@label.com")

        assert contact_is_excluded(session, contact.contact_key, None, TODAY, artist_ids=[make_artist(session).id])
        assert contact_is_excluded(session, contact.contact_key, None, TODAY, artist_ids=[])

    def test_a_bad_fit_blocks_the_contact_only_when_every_artist_is_blocked(self, session):
        curator, blocked, other = make_curator(session), make_artist(session), make_artist(session)
        contact = make_contact(session, curator, value="curator@label.com")
        make_bad_fit(session, blocked, curator)

        assert contact_is_excluded(session, contact.contact_key, None, TODAY, artist_ids=[blocked.id])
        assert not contact_is_excluded(session, contact.contact_key, None, TODAY, artist_ids=[blocked.id, other.id])

    def test_new_address_at_a_bad_fit_curators_company_domain_is_excluded_for_that_artist(self, session):
        curator, artist = make_curator(session), make_artist(session)
        make_contact(session, curator, value="promo@coollabel.com")
        make_bad_fit(session, artist, curator)

        assert contact_is_excluded(
            session, "email:someone-else@coollabel.com", "domain:coollabel.com", TODAY, artist_ids=[artist.id]
        )

    def test_domain_of_eligible_curator_does_not_exclude(self, session):
        make_contact(session, make_curator(session), value="promo@coollabel.com")

        assert not contact_is_excluded(
            session, "email:other@coollabel.com", "domain:coollabel.com", TODAY, artist_ids=[make_artist(session).id]
        )
```

- [ ] **Step 2: Update the other tests that stored bad fit on a curator**

- **`tests/test_digest.py`** (`test_curators_on_cooldown_or_excluded_are_left_out`): replace the second `ready_lead(...)` call with:

  ```python
          blocked = make_curator(session)
          make_bad_fit(session, profile.artist, blocked)
          ready_lead(session, profile, curator=blocked)
  ```

  and import `make_bad_fit`.
- **`tests/test_pipeline_report.py`** (`test_curators_who_are_not_eligible_are_left_out`): replace `excluded = make_curator(session, excluded_at=at(10), exclusion_reason="bad-fit")` with:

  ```python
      excluded = make_curator(session)
      make_bad_fit(session, profile.artist, excluded, when=at(10))
  ```

  and import `make_bad_fit` from `tests.factories`.
- **`tests/test_research.py`** (`test_contacts_of_an_excluded_curator_are_not_reused`): change `exclusion_reason="bad-fit"` to `exclusion_reason="dead"`. Per-artist contact rules get their own tests in Task 16.
- **`tests/test_web_digest.py`** (`test_bad_fit_keeps_the_curator_out_for_good`): replace the assertion with the lines below, and add `ArtistCuratorExclusion` to its `core.models` import:

  ```python
          stored = session.get(Outreach, outreach.id)
          assert session.get(ArtistCuratorExclusion, (stored.artist_id, stored.curator_id)) is not None
  ```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_exclusion.py -q`

Expected: `TypeError: curator_is_eligible() takes 3 positional arguments but 4 were given`, plus IntegrityErrors from `record_verdict` on bad-fit.

- [ ] **Step 4: Rewrite the eligibility, verdict and contact rules in `core/exclusion.py`**

Replace the module docstring:

```python
"""Who and what must not reach the digest again, and for how long.

The rules (Jarred, 2026-09-13; per artist since 2026-09-15):
- A curator appears at most once per 90 days for each artist, across that artist's profiles.
  Two different artists can each have the same curator.
- `pitched` and `skip` just wait out those 90 days. `bad-fit` keeps the curator out of that
  artist's digests for good; `dead` keeps them out of everyone's.
- `no-contact`, digested, not-alive and no-fit playlists are re-checked after 90 days.
  Pay-to-play and bot rejections (`not-real`) are permanent.

The 90-day rule is also enforced by an EXCLUDE constraint on `outreach` over (artist, curator,
90 days), so even a bug here can't put the same curator in one artist's digest twice in the window.
"""
```

Update the imports:

```python
from collections.abc import Collection, Iterable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from core.models import (
    ArtistCuratorExclusion,
    ArtistExclusionReason,
    Contact,
    Curator,
    ExclusionReason,
    Outreach,
    OutreachStatus,
    Playlist,
    PlaylistStatus,
    RejectionReason,
)
```

Replace `_curator_blocked`, `curator_is_eligible`, `record_verdict` and `contact_is_excluded`. Leave `playlist_ids_to_skip` and `_should_skip` untouched.

```python
def _curator_blocked(artist_id: int, today: date) -> ColumnElement[bool]:
    recently_digested = (
        select(Outreach.id)
        .where(
            Outreach.curator_id == Curator.id,
            Outreach.artist_id == artist_id,
            Outreach.digest_date > cooldown_start(today),
        )
        .exists()
    )
    bad_fit = (
        select(ArtistCuratorExclusion.curator_id)
        .where(ArtistCuratorExclusion.curator_id == Curator.id, ArtistCuratorExclusion.artist_id == artist_id)
        .exists()
    )
    return or_(Curator.excluded_at.is_not(None), recently_digested, bad_fit)


def curator_is_eligible(session: Session, curator_id: int, artist_id: int, today: date) -> bool:
    if session.get(Curator, curator_id) is None:
        raise LookupError(f"Curator {curator_id} not found")
    blocked = session.scalar(
        select(Curator.id).where(Curator.id == curator_id, _curator_blocked(artist_id, today))
    )
    return blocked is None


def record_verdict(session: Session, outreach_id: int, verdict: str, now: datetime) -> Outreach:
    if verdict not in VERDICTS:
        allowed = ", ".join(sorted(VERDICTS))
        raise ValueError(f"Unknown verdict {verdict!r}; expected one of: {allowed}")

    outreach = session.get(Outreach, outreach_id)
    if outreach is None:
        raise LookupError(f"Outreach {outreach_id} not found")

    outreach.status = verdict
    outreach.status_changed_at = now
    if verdict == OutreachStatus.PITCHED:
        outreach.pitched_at = now
    if verdict == OutreachStatus.BAD_FIT:
        _exclude_for_artist(session, outreach, now)
    if verdict == OutreachStatus.DEAD and outreach.curator.excluded_at is None:
        outreach.curator.excluded_at = now
        outreach.curator.exclusion_reason = ExclusionReason.DEAD

    session.flush()
    return outreach


def _exclude_for_artist(session: Session, outreach: Outreach, now: datetime) -> None:
    if session.get(ArtistCuratorExclusion, (outreach.artist_id, outreach.curator_id)) is not None:
        return
    session.add(
        ArtistCuratorExclusion(
            artist_id=outreach.artist_id,
            curator_id=outreach.curator_id,
            reason=ArtistExclusionReason.BAD_FIT,
            excluded_at=now,
        )
    )


def contact_is_excluded(
    session: Session, contact_key: str, domain_key: str | None, today: date, *, artist_ids: Collection[int]
) -> bool:
    """True if this contact (or, for company email, its domain) belongs to a curator blocked for
    every one of these artists. With no artists to ask about, only a dead curator blocks."""
    matches = Contact.contact_key == contact_key
    if domain_key is not None:
        matches = or_(matches, Contact.domain_key == domain_key)
    owners = select(Contact.id).join(Curator, Contact.curator_id == Curator.id).where(matches).limit(1)

    if not artist_ids:
        return session.scalar(owners.where(Curator.excluded_at.is_not(None))) is not None
    return all(
        session.scalar(owners.where(_curator_blocked(artist_id, today))) is not None for artist_id in artist_ids
    )
```

`PERMANENT_VERDICTS` is no longer used inside the module. Run `grep -rn PERMANENT_VERDICTS core pipeline web tests`, and delete the constant if nothing else uses it.

`playlist_ids_to_skip` still uses `Iterable`, so keep that import. `ruff check` reports anything left unused.

- [ ] **Step 5: Pass the artist from the pipeline**

In `pipeline/digest.py` `_ranked_candidates`, replace the loop:

```python
    eligible: dict[tuple[int, int], bool] = {}
    reachable = []
    for playlist, profile, fit in rows:
        key = (profile.artist_id, playlist.curator_id)
        if key not in eligible:
            eligible[key] = curator_is_eligible(session, playlist.curator_id, profile.artist_id, today)
        contact = best_contact(session, playlist.curator_id) if eligible[key] else None
        if contact is not None:
            reachable.append((playlist, profile, fit, contact))
```

In `build_digest`, track used curators per artist:

```python
    used: set[tuple[int, int]] = set()

    for candidate in candidates:
        playlist, profile = candidate.playlist, candidate.profile
        key = (profile.artist_id, playlist.curator_id)
        if key in used or taken.get(profile.id, 0) >= profile.digest_target:
            continue
        brief = _write_brief(writer, _brief_request(candidate, now), summary)
        if not _add_entry(session, candidate, brief, today):
            continue
        playlist.status = PlaylistStatus.DIGESTED
        used.add(key)
        taken[profile.id] = taken.get(profile.id, 0) + 1
        summary.entries += 1
        summary.per_profile[profile.name] = summary.per_profile.get(profile.name, 0) + 1
        session.commit()
```

In `pipeline/research.py` `run_research`, replace the first lines of the loop body:

```python
    for playlist, profile, fit in _candidates(session):
        if playlist.curator_id in seen_curators or ready[profile.id] >= profile.digest_target:
            continue
        if not curator_is_eligible(session, playlist.curator_id, profile.artist_id, today):
            continue  # another artist's profile may still want this curator further down the list
        seen_curators.add(playlist.curator_id)
```

In `_save_contact`, the call becomes `contact_is_excluded(session, key, domain, today, artist_ids=())` for now. Task 16 passes the real artists.

In `pipeline/report.py`, the check becomes `curator_is_eligible(session, playlist.curator_id, profile.artist_id, today)`. Update its docstring: "Curators who are excluded, or who appeared in that artist's digest within the last 90 days, are left out."

- [ ] **Step 6: Run the tests and the suite**

Run: `uv run pytest tests/test_exclusion.py tests/test_digest.py tests/test_research.py tests/test_pipeline_report.py tests/test_web_digest.py -q`

Expected: all pass.

Run: `uv run pytest -q`

Expected: all pass.

- [ ] **Step 7: Lint and commit Tasks 13 and 14 together**

```bash
uv run ruff check . && uv run ruff format .
git add core/models.py core/exclusion.py core/db_roles.py pipeline/digest.py pipeline/research.py pipeline/report.py \
  migrations/versions/20260915_0007_curators_per_artist.py tests
git commit -m "feat: curators are separate per artist

Artists shouldn't compete for curators. Each artist gets its own 90-day window (now a
database constraint over artist and curator) and its own bad-fit list; dead stays shared.
Existing bad-fit marks move to the artist whose digest recorded them.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: A playlist can reach more than one artist's digest

**Files:**
- Modify: `pipeline/digest.py`
- Test: `tests/test_digest_artists.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_digest_artists.py`:

```python
"""With several artists, each gets their own chance at a curator and a playlist."""

from datetime import timedelta

from core.models import PlaylistProfileFit, PlaylistStatus
from tests.factories import make_artist, make_bad_fit, make_curator, make_outreach, make_profile
from tests.test_digest import NOW, TODAY, digest_tonight, entries_today, ready_lead


def active_for(session, artist, name="Main", digest_target=20):
    profile = make_profile(session, name, artist=artist)
    profile.is_active = True
    profile.digest_target = digest_target
    session.flush()
    return profile


def also_fits(session, playlist, profile, fit=0.8):
    session.add(
        PlaylistProfileFit(
            playlist_id=playlist.spotify_id,
            profile_id=profile.id,
            fit_score=fit,
            reference_artists_present=["Autechre"],
            qualified=True,
        )
    )
    session.flush()


def test_the_same_curator_reaches_two_artists_on_one_night(session):
    first, second = active_for(session, make_artist(session)), active_for(session, make_artist(session))
    curator = make_curator(session)
    ready_lead(session, first, curator=curator)
    ready_lead(session, second, curator=curator, grade=None)

    digest_tonight(session)

    assert {entry.artist_id for entry in entries_today(session)} == {first.artist_id, second.artist_id}


def test_the_same_playlist_reaches_two_artists_on_one_night(session):
    first, second = active_for(session, make_artist(session)), active_for(session, make_artist(session))
    playlist = ready_lead(session, first)
    also_fits(session, playlist, second)

    digest_tonight(session)

    entries = entries_today(session)
    assert [entry.playlist_id for entry in entries] == [playlist.spotify_id, playlist.spotify_id]
    assert {entry.artist_id for entry in entries} == {first.artist_id, second.artist_id}


def test_one_artists_two_profiles_still_share_a_curator_once(session):
    artist = make_artist(session)
    first, second = active_for(session, artist, "One"), active_for(session, artist, "Two")
    playlist = ready_lead(session, first, fit=0.9)
    also_fits(session, playlist, second, fit=0.5)

    digest_tonight(session)

    assert [entry.profile_id for entry in entries_today(session)] == [first.id]


def test_a_playlist_digested_for_one_artist_last_week_reaches_another_tonight(session):
    first, second = active_for(session, make_artist(session)), active_for(session, make_artist(session))
    playlist = ready_lead(session, first)
    make_outreach(session, playlist.curator, first, TODAY - timedelta(days=7), playlist=playlist)
    playlist.status = PlaylistStatus.DIGESTED
    also_fits(session, playlist, second)

    digest_tonight(session)

    assert [entry.artist_id for entry in entries_today(session)] == [second.artist_id]


def test_a_playlist_digested_over_90_days_ago_waits_for_a_fresh_check(session):
    artist = active_for(session, make_artist(session))
    playlist = ready_lead(session, artist)
    make_outreach(session, playlist.curator, artist, TODAY - timedelta(days=100), playlist=playlist)
    playlist.status = PlaylistStatus.DIGESTED
    session.flush()

    digest_tonight(session)

    assert entries_today(session) == []


def test_one_artists_bad_fit_does_not_block_another(session):
    blocked, other = active_for(session, make_artist(session)), active_for(session, make_artist(session))
    playlist = ready_lead(session, blocked)
    also_fits(session, playlist, other)
    make_bad_fit(session, blocked.artist, playlist.curator, when=NOW)

    digest_tonight(session)

    assert [entry.artist_id for entry in entries_today(session)] == [other.artist_id]
```

`ready_lead(..., grade=None)` in the first test avoids a second contact row for the same curator; the contact from the first `ready_lead` is shared.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_digest_artists.py -q`

Expected: only `test_a_playlist_digested_for_one_artist_last_week_reaches_another_tonight` fails, because `_ranked_candidates` reads only `qualified` playlists.

The other five pass already, thanks to Task 14's per-artist `used` set and eligibility. The same-playlist case passes too: candidates are read once before any entry is added, so marking the playlist `digested` mid-run doesn't remove the second artist's candidate. Keep all six tests; they guard the behaviour.

- [ ] **Step 3: Consider recently digested playlists too**

In `pipeline/digest.py`:
- Add `and_`, `or_` to the SQLAlchemy import.
- Change `from core.exclusion import curator_is_eligible` to `from core.exclusion import cooldown_start, curator_is_eligible`.

Replace the query at the top of `_ranked_candidates`:

```python
def _ranked_candidates(session: Session, today: date, now: datetime) -> list[_Candidate]:
    # A playlist digested for one artist in the last 90 days is still fresh enough for another.
    digested_recently = (
        select(Outreach.id)
        .where(Outreach.playlist_id == Playlist.spotify_id, Outreach.digest_date > cooldown_start(today))
        .exists()
    )
    rows = session.execute(
        select(Playlist, Profile, PlaylistProfileFit)
        .join(PlaylistProfileFit, PlaylistProfileFit.playlist_id == Playlist.spotify_id)
        .join(Profile, Profile.id == PlaylistProfileFit.profile_id)
        .where(
            or_(
                Playlist.status == PlaylistStatus.QUALIFIED,
                and_(Playlist.status == PlaylistStatus.DIGESTED, digested_recently),
            ),
            Playlist.curator_id.is_not(None),
            PlaylistProfileFit.qualified.is_(True),
            Profile.is_active.is_(True),
        )
    )
```

Keep the rest of the function (the loop from Task 14, scoring and sorting).

Candidates are chosen before any entry is added, so tonight's entries don't affect the query. The per-artist `used` set and the database constraint stop duplicates within one artist.

Update the module docstring's first paragraph:

```python
"""The digest: tonight's best reachable curators for each artist, with a brief to pitch from.

A playlist is considered if it's qualified (or was digested for some artist in the last 90 days)
for an active profile, its curator is eligible for that profile's artist (not dead, not a bad fit
for them, not in their digest in the last 90 days), and its curator is reachable (an A or B
contact). Candidates are ranked with the spec's weighted sum: fit first, then contact confidence,
how recently the playlist moved, and size. A curator appears once per artist, under the profile
they fit best, and each profile takes at most its digest target. Different artists can each get
the same curator, and even the same playlist.
```

Keep the docstring's remaining paragraphs.

- [ ] **Step 4: Run the tests and the suite**

Run: `uv run pytest tests/test_digest_artists.py tests/test_digest.py tests/test_digest_ranking.py -q`

Expected: all pass.

Run: `uv run pytest -q`

Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add pipeline/digest.py tests/test_digest_artists.py
git commit -m "feat: a playlist digested for one artist can still reach another

Marking a playlist digested used to take it away from everyone for 90 days. Now only the
artist who got it waits; stale playlists still wait for discovery to re-check them.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Research and the report think per artist

**Files:**
- Modify: `pipeline/research.py`
- Test: `tests/test_research_artists.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_artists.py`:

```python
"""Research runs once per curator, but whether a curator is worth it depends on which artists want them."""

from datetime import timedelta

from sqlalchemy import select

from core.models import Contact, PlaylistProfileFit
from core.profiles import create_profile, set_profile_active
from pipeline.report import qualified_playlists
from pipeline.research import record_research
from tests.factories import make_artist, make_bad_fit, make_contact, make_curator, make_outreach, make_profile
from tests.profile_helpers import add_contents
from tests.test_pipeline_report import qualified
from tests.test_research import NOW, TODAY, FakeAgent, finding, outcome, qualified_lead, ready_playlist, research_tonight


def active_for(session, artist, name="Main"):
    profile = make_profile(session, name, artist=artist)
    profile.is_active = True
    session.flush()
    return profile


def fits(session, playlist, *profiles):
    for profile in profiles:
        session.add(
            PlaylistProfileFit(
                playlist_id=playlist.spotify_id,
                profile_id=profile.id,
                fit_score=0.8,
                reference_artists_present=["Autechre"],
                qualified=True,
            )
        )
    session.flush()


class TestContactsPerArtist:
    def test_a_company_address_blocked_for_the_only_interested_artist_is_not_saved(self, session):
        artist = make_artist(session)
        blocked = make_curator(session)
        make_contact(session, blocked, value="promo@coollabel.com")
        make_bad_fit(session, artist, blocked)
        playlist = ready_playlist(session, make_curator(session))
        fits(session, playlist, active_for(session, artist))

        saved = record_research(session, playlist, outcome(finding(value="demos@coollabel.com")), today=TODAY, now=NOW)

        assert not saved
        assert session.scalar(select(Contact).where(Contact.value == "demos@coollabel.com")) is None

    def test_it_is_saved_when_another_interested_artist_is_not_blocked(self, session):
        blocked_artist, other_artist = make_artist(session), make_artist(session)
        blocked = make_curator(session)
        make_contact(session, blocked, value="promo@coollabel.com")
        make_bad_fit(session, blocked_artist, blocked)
        playlist = ready_playlist(session, make_curator(session))
        fits(session, playlist, active_for(session, blocked_artist), active_for(session, other_artist))

        saved = record_research(session, playlist, outcome(finding(value="demos@coollabel.com")), today=TODAY, now=NOW)

        assert saved
        assert session.scalar(select(Contact).where(Contact.value == "demos@coollabel.com")) is not None


class TestNightlyResearchPerArtist:
    def test_a_curator_on_cooldown_for_one_artist_is_researched_for_another(self, session):
        first, second = active_for(session, make_artist(session)), active_for(session, make_artist(session))
        curator = make_curator(session)
        make_outreach(session, curator, first, TODAY - timedelta(days=10))
        qualified_lead(session, first, fit=0.9, curator=curator)
        for_second = qualified_lead(session, second, fit=0.5, curator=curator)
        agent = FakeAgent()

        research_tonight(session, agent=agent)

        assert [lead.playlist_id for lead in agent.leads] == [for_second.spotify_id]

    def test_a_curator_blocked_for_every_interested_artist_is_not_researched(self, session):
        first, second = active_for(session, make_artist(session)), active_for(session, make_artist(session))
        curator = make_curator(session)
        make_bad_fit(session, first.artist, curator)
        make_bad_fit(session, second.artist, curator)
        qualified_lead(session, first, fit=0.9, curator=curator)
        qualified_lead(session, second, fit=0.5, curator=curator)
        agent = FakeAgent()

        research_tonight(session, agent=agent)

        assert agent.leads == []


def test_the_report_checks_each_fit_against_its_own_artist(session):
    mine = add_contents(session, create_profile(session, make_artist(session).id, "Mine"))
    set_profile_active(session, mine.id, True)
    theirs = add_contents(session, create_profile(session, make_artist(session).id, "Theirs"))
    set_profile_active(session, theirs.id, True)
    curator = make_curator(session)
    make_outreach(session, curator, mine, TODAY - timedelta(days=5))
    qualified(session, mine, name="Mine again", score=1.0, artists=["Plaid"], curator=curator)
    qualified(session, theirs, name="Theirs fresh", score=1.0, artists=["Plaid"], curator=curator)

    names = [item.name for item in qualified_playlists(session, today=TODAY)]

    assert names == ["Theirs fresh"]
```

The two modules share dates: `tests/test_pipeline_report.py` uses `TODAY = date(2026, 9, 14)`, as does `tests/test_research.py`. The report test above uses research's `TODAY`, which is the same date.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_research_artists.py -q`

Expected: `test_a_company_address_blocked_for_the_only_interested_artist_is_not_saved` fails, because Task 14 passed `artist_ids=()`, so only dead curators block. The other tests pass already, thanks to Task 14.

- [ ] **Step 3: Pass the interested artists when saving contacts**

In `pipeline/research.py`:
- Add `Profile` and `PlaylistProfileFit` to the models import, if they aren't already imported.
- Change `record_research` and `_save_contact`:

```python
def record_research(
    session: Session, playlist: Playlist, result: ResearchResult, *, today: date, now: datetime
) -> bool:
    """Store what was found. True if the curator can now be reached (a stored A or B contact)."""
    if result.deferred:
        return False

    curator = playlist.curator
    strongest = None
    if curator is not None:
        artist_ids = _interested_artist_ids(session, playlist.spotify_id)
        for finding in result.contacts:
            stored = _save_contact(session, curator, playlist, finding, today, artist_ids)
            if stored is not None and (strongest is None or GRADE_ORDER[stored] < GRADE_ORDER[strongest]):
                strongest = stored

    reachable = strongest in STRONG and not result.service_account
    playlist.last_checked_at = now
    if not reachable:
        playlist.status = PlaylistStatus.NO_CONTACT
    session.flush()
    return reachable


def _interested_artist_ids(session: Session, playlist_id: str) -> tuple[int, ...]:
    """Artists with an active profile this playlist qualified for. A contact is only refused if
    its owner is blocked for all of them."""
    return tuple(
        session.scalars(
            select(Profile.artist_id)
            .join(PlaylistProfileFit, PlaylistProfileFit.profile_id == Profile.id)
            .where(
                PlaylistProfileFit.playlist_id == playlist_id,
                PlaylistProfileFit.qualified.is_(True),
                Profile.is_active.is_(True),
            )
            .distinct()
        )
    )
```

`_save_contact` gains a final `artist_ids: tuple[int, ...]` parameter, and its exclusion check becomes `if contact_is_excluded(session, key, domain, today, artist_ids=artist_ids):`.

A playlist with no qualifying active profile (for example in older tests) gets no artists, so only a dead owner blocks its contacts. That's the safe default.

- [ ] **Step 4: Run the tests and the suite**

Run: `uv run pytest tests/test_research_artists.py tests/test_research.py -q`

Expected: all pass.

Run: `uv run pytest -q`

Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add pipeline/research.py tests/test_research_artists.py
git commit -m "feat: research refuses a contact only when every interested artist is blocked

One artist's bad fit shouldn't hide a curator's address from another artist who wants them.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: Docs and shipping stage 3

**Files:**
- Modify: `IMPLEMENTATION_PLAN.md`, `.claude/DEVELOPER_LOGS.md`, `web/digest.py` (docstring), `/Users/jarredcinman/.claude/projects/-Users-jarredcinman-Coding-Stuff-noble-hunter/memory/project-noble-hunter-decisions.md`

- [ ] **Step 1: Run the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: all pass, clean.

- [ ] **Step 2: Bring the docs up to date**

In `web/digest.py`, the docstring's second paragraph becomes: `A verdict goes through core.exclusion.record_verdict, so its effects are the same wherever it's recorded. pitched and skip let the curator come back after 90 days; bad-fit keeps them out of this artist's digests for good, and dead keeps them out of everyone's.`

In `IMPLEMENTATION_PLAN.md`:
- Change the "Cross-profile pitching" row to: `A curator appears **at most once per 90 days per artist**, across that artist's profiles. Different artists can each get the same curator | 2026-09-15`.
- Change the "Verdict effects" row to: `skip and pitched: eligible again after 90 days. bad-fit: excluded permanently for that artist. dead: excluded permanently for everyone | 2026-09-15`.
- Set Stage 11's status to `Complete`.

Append to the 2026-09-15 entry in `.claude/DEVELOPER_LOGS.md`:

```markdown
Stage 3 stopped artists competing for curators. `outreach` now records its artist, and the database's 90-day constraint covers (artist, curator) instead of the curator alone. Bad fit moved off `curators` into `artist_curator_exclusions`; migration 0007 hands each existing mark to the artist whose digest recorded it. Only "dead" stays on the curator, because an abandoned playlist is abandoned for everyone. The digest now also considers playlists another artist was given in the last 90 days, so one great playlist can reach two artists on the same night. Research still runs once per curator, since contacts are public facts, but it refuses an address only when every artist who wants the playlist has that curator blocked.
```

Update the memory file `project-noble-hunter-decisions.md` with one line: `- Several artists (2026-09-15): artists own profiles; members see only their artists; admins = ALLOWED_EMAILS; curators per artist (90-day window and bad fit per artist, dead shared). Email pitching follows, with mailboxes per artist (docs/superpowers/specs/2026-09-15-inline-email-pitching-design.md).`

- [ ] **Step 3: Commit**

```bash
git add IMPLEMENTATION_PLAN.md .claude/DEVELOPER_LOGS.md web/digest.py
git commit -m "docs: curators are per artist; artists and access complete

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Check production before migrating (read-only)**

Run this against Neon with the owner connection used for migrations, and note the numbers for Jarred:

```bash
uv run python - <<'EOF'
from sqlalchemy import create_engine, text
from core.settings import load_settings

engine = create_engine(load_settings().sqlalchemy_url(pooled=False))
with engine.connect() as connection:
    for label, sql in [
        ("bad-fit curators", "select count(*) from curators where exclusion_reason = 'bad-fit'"),
        ("dead curators", "select count(*) from curators where exclusion_reason = 'dead'"),
        ("outreach rows", "select count(*) from outreach"),
    ]:
        print(label, connection.scalar(text(sql)))
engine.dispose()
EOF
```

- [ ] **Step 5: Ship stage 3 (ask Jarred first)**

With his go-ahead:

```bash
uv run alembic upgrade head                      # Neon: 0006 -> 0007
uv run alembic current                           # expect 0007 (head)
uv run python -m pipeline.cli db grant           # web may now write artist_curator_exclusions
git push
scripts/install-worker.sh install                # the runner must load the per-artist rules before 02:00
```

Run the Step 4 script again. Expected: `bad-fit curators 0`, the same number of dead curators, and the same number of outreach rows.

Then check the live app as Jarred:
- `/digest` still shows past entries.
- A "Bad fit" click on a test entry records without error.
- The runner panel shows it online.

---

## Self-review notes for the executor

- **Stages ship separately.** Never push stage 3's model changes before migration 0007 is on Neon.
- **If the route walk fails after a later change, fix the route.** Don't loosen the test. It exists because one missing `require_*` line leaks another artist's work.
- **Inline email pitching comes next,** in its own plan. It depends on `core/access.py` and uses migration 0008.

