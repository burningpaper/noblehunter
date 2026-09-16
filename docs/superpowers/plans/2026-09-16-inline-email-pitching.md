# Inline Email Pitching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pitch a curator by email from inside the digest, with a Claude draft to edit, and read and answer their replies in the app.

**Architecture:**
- **Gmail per artist.** A mailbox belongs to an artist; each profile picks which of its artist's mailboxes it pitches from. Refresh tokens are encrypted at rest with Fernet.
- **Shared core.** Gmail, MIME, drafts, sending, reply sync and the Claude writer live in `core/`, because both the Vercel web app and the Mac Mini runner use them. The web app still never imports `pipeline/`.
- **Conversation load.** The digest works to a ceiling of open conversations (20) rather than a flat nightly number, and research spends only up to that allowance.

**Tech Stack:** Python 3.12, FastAPI, Jinja, htmx, SQLAlchemy 2, Alembic, Postgres 18 (Docker for tests, Neon in production), httpx, `cryptography` (Fernet), the Anthropic SDK, pytest, ruff, uv.

**Spec:** [docs/superpowers/specs/2026-09-15-inline-email-pitching-design.md](../specs/2026-09-15-inline-email-pitching-design.md)

**Where this plan knowingly differs from the spec.** Three places, so nobody "fixes" them back:

- **No `Replied` status.** The spec speaks of marking an entry Replied. This plan derives whose turn it is from the last message's direction instead (`EntryMail`, `core/conversations.py`), because a status column and the messages can disagree — and then a reply that arrives while a verdict is being recorded leaves the entry lying about itself. Nothing is stored that the messages don't already say.
- **`PitchWriter.write()`, not `draft()`.** The codebase's other Claude callers are `Suggester.suggest()` and `BriefWriter.write()`; this one matches them.
- **`read_at` is used only for unread counts in the Inbox**, set when a thread is opened. It isn't part of deciding whether a conversation is open — that's the messages' business.

One thing the spec asks for is deliberately **not** built here: the People page showing how many mailboxes are connected, for Google's 100-user cap. Nobody but Jarred can connect one until stage 3 of Artists and access ships, so the number would read "1" and mean nothing. Build it when members can actually connect mailboxes.

---

## Before you start

- **Worktree:** work only in `/Users/jarredcinman/Coding Stuff/noble_hunter/.claude/worktrees/email-pitching`, on branch `feature/email-pitching`. Never cd to the main checkout: the live runner uses it.
- **Test database:** Postgres in Docker on 127.0.0.1:55432, started with `scripts/test-db.sh up`. The baseline suite is **1430 passed, 5 skipped**.
- **Never touch production:** no Neon, no `db grant` against Neon, no `git push`, no uvicorn with the real environment, and never read or print `.env.local`. No real Gmail or Anthropic calls in tests.
- **Lint:** `uv run ruff check . && uv run ruff format --check .`, line length 110. `docs/` is excluded from ruff.
- **Shell:** zsh. Never name a loop variable `path`. Don't use `git stash`; make a WIP commit instead.
- **pytest turns SQLAlchemy `SAWarning` into an error**, so a query that forgets a join fails loudly.
- **Commits** end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **A few code blocks here run past 110 columns** to stay readable on this page. `ruff format` rewraps them; every task runs it before committing, so don't hand-wrap as you type.

### What already exists (stages 1 and 2 of Artists and access, shipped 2026-09-16)

- **`core/access.py`:** `Viewer`, `visible_to(viewer, column)`, `require_profile(session, viewer, id)`, `require_outreach(...)`, `require_admin`, `is_storable_id(n)`, `MAX_POSTGRES_INT`, `NotVisible` (404), `AdminOnly` (403).
- **`web/access.py`:** `CurrentViewer` and `AdminViewer` dependencies, `wants_html(request)`. Every router except auth is included with `Depends(current_viewer)` in `web/app.py`.
- **Route walk:** `tests/route_walk.py` (ROUTES, FORMS, `call`, `registered_routes`), `tests/access_world.py` (the world, `their_data` snapshot, `seed_route_state`), `tests/test_web_access.py`, `tests/test_web_access_controls.py`, `tests/test_web_access_sweeps.py`. **Every new route must be added to ROUTES, or `test_every_route_is_covered` fails.**
- **Digest:** `core/digest_view.py` (`EntryView`, `ProfileDigest`, `DigestView`, `digest_view(session, day, *, today, viewer)`), `web/digest.py`, `web/templates/digest/page.html` and `_entry.html`.
- **Profiles:** `core/profiles.py` (`get_profile`, `update_profile_settings`, `_validated_settings`), `web/profiles.py`, `web/templates/profiles/_settings_form.html`.
- **Pipeline:** `pipeline/digest.py` `build_digest`, `pipeline/research.py` `run_research`, `pipeline/worker.py` (the launchd loop, `keep_checking_in`, `run_forever`), `pipeline/cli.py` `worker` command with `--check`.
- **Settings:** `core/settings.py` `Settings` (pipeline, from `.env`/`.env.local`), `web/settings.py` `WebSettings` (Vercel).
- **Grants:** `core/db_roles.py` `WEB_EDITABLE_TABLES`, `WEB_OUTREACH_COLUMNS`, `WEB_CURATOR_COLUMNS`.

## File map

| File | Status | Responsibility |
|---|---|---|
| `core/mail_crypto.py` | create | Fernet encrypt/decrypt of refresh tokens; "mail isn't configured" when the key is absent |
| `core/gmail.py` | create | Gmail REST over httpx: refresh access token, profile, send, history, thread, message. `GmailError(kind)` |
| `core/mime.py` | create | Build a plain-text message (base64url) and parse one back, splitting quoted history |
| `core/mailboxes.py` | create | An artist's mailboxes: connect, attach, detach, mark needing reconnect |
| `core/pitches.py` | create | Drafts, sending (one-time key), status transitions |
| `core/mail_sync.py` | create | One mailbox's new messages into `email_messages`, matched to the entry that started the thread |
| `core/pitch_writer.py` | create | `PitchWriter` protocol and `ClaudePitchWriter` (Opus 5, `{subject, body}`) |
| `core/conversations.py` | create | Which entries count as open, per profile, and tonight's allowance |
| `core/inbox_view.py` | create | Read models for the thread panel and the Inbox, scoped by viewer |
| `web/mail.py` | create | Pitch mailbox card; connect/callback/attach/disconnect |
| `web/pitches.py` | create | Compose panel, draft autosave, Claude draft, send, thread panel |
| `web/inbox.py` | create | Inbox page, "Check now" |
| `web/templates/mail/*`, `web/templates/pitch/*`, `web/templates/inbox/*` | create | The card, compose panel, thread and Inbox |
| `migrations/versions/20260916_0007_email_pitching.py` | create | `mail_accounts`, `email_messages`, profile and outreach columns |
| `core/models.py` | modify | `MailAccount`, `EmailMessage`, new `Profile` and `Outreach` columns |
| `core/settings.py`, `web/settings.py`, `.env.example`, `pyproject.toml` | modify | `MAIL_TOKEN_KEY`, Google client on the Mac, the `cryptography` dependency |
| `core/db_roles.py` | modify | Grants for the new tables and columns |
| `core/profiles.py`, `web/profiles.py`, `web/templates/profiles/_settings_form.html` | modify | The two conversation settings |
| `pipeline/digest.py`, `pipeline/research.py` | modify | Tonight's allowance from the open-conversation ceiling |
| `pipeline/worker.py`, `pipeline/cli.py` | modify | The mail-check thread and its `--check` |
| `core/digest_view.py`, `web/digest.py`, `web/templates/digest/*` | modify | Mail state on each entry, the conversation count |
| `core/run_status.py`, `web/templates/runs/_panel.html` | modify | "Reconnect Gmail" and "replies not checked" warnings |
| `tests/route_walk.py`, `tests/access_world.py` | modify | The new routes join the walk and the snapshot |

**Stages** (each ends green and committed; nothing is pushed):
1. **Tasks 1–4:** foundations — dependency, settings, crypto, migration, models, Gmail client, MIME.
2. **Tasks 5–6:** connect a mailbox (core, then the card and routes).
3. **Tasks 7–9:** writing and sending (the Claude writer, pitches core, the compose panel).
4. **Tasks 10–13:** replies (the sync, the runner's mail thread, mail state and warnings, the Inbox).
5. **Tasks 14–17:** conversation load (core, the settings, the digest and research allowance), then docs and the ship checklist.

---

## Stage 1: Foundations

### Task 1: The mail key and the crypto that uses it

**Files:**
- Modify: `pyproject.toml`, `core/settings.py`, `web/settings.py`, `.env.example`
- Create: `core/mail_crypto.py`
- Test: `tests/test_mail_crypto.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mail_crypto.py`:

```python
"""Refresh tokens are encrypted at rest: without MAIL_TOKEN_KEY, mail isn't configured at all."""

import pytest
from cryptography.fernet import Fernet

from core.mail_crypto import MailNotConfigured, decrypt_token, encrypt_token, mail_cipher

TOKEN = "1//0gFAKE-refresh-token_value"


def test_a_token_survives_a_round_trip():
    cipher = mail_cipher(Fernet.generate_key().decode())

    assert decrypt_token(cipher, encrypt_token(cipher, TOKEN)) == TOKEN


def test_the_stored_form_does_not_contain_the_token():
    cipher = mail_cipher(Fernet.generate_key().decode())

    assert TOKEN not in encrypt_token(cipher, TOKEN)


def test_another_key_cannot_read_it():
    stored = encrypt_token(mail_cipher(Fernet.generate_key().decode()), TOKEN)
    other = mail_cipher(Fernet.generate_key().decode())

    with pytest.raises(MailNotConfigured, match="couldn't be read"):
        decrypt_token(other, stored)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_without_a_key_mail_is_not_configured(value):
    with pytest.raises(MailNotConfigured, match="MAIL_TOKEN_KEY"):
        mail_cipher(value)


def test_a_key_that_is_not_a_fernet_key_says_so():
    with pytest.raises(MailNotConfigured, match="MAIL_TOKEN_KEY"):
        mail_cipher("not-a-real-key")


def test_a_generated_key_is_usable():
    # What the setup instructions tell Jarred to run.
    key = Fernet.generate_key().decode()

    assert decrypt_token(mail_cipher(key), encrypt_token(mail_cipher(key), TOKEN)) == TOKEN
```

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run pytest tests/test_mail_crypto.py -q`

Expected: collection error, `ModuleNotFoundError: No module named 'cryptography'` or `No module named 'core.mail_crypto'`.

- [ ] **Step 3: Add the dependency**

In `pyproject.toml`, add `"cryptography>=43",` to `[project] dependencies`, keeping the list alphabetical (after `authlib`). It belongs in the main dependencies, not a group: Vercel installs those, and the web app encrypts tokens when a mailbox is connected.

Run: `uv sync --all-groups`

- [ ] **Step 4: Write `core/mail_crypto.py`**

```python
"""Encrypting the Gmail refresh tokens Noble Hunter stores.

A refresh token is a long-lived key to someone's mailbox, so it never goes in the database in
readable form. `MAIL_TOKEN_KEY` (a Fernet key, the same value on Vercel and the Mac Mini)
encrypts it; without that key the app still runs and simply says mail isn't configured.

Fernet gives authenticated encryption, so a token altered in the database fails to decrypt
rather than being used. The key itself lives only in the environment, never in the database.
"""

from cryptography.fernet import Fernet, InvalidToken

KEY_NAME = "MAIL_TOKEN_KEY"


class MailNotConfigured(RuntimeError):
    """No usable mail key, or a stored token this key can't read."""


def mail_cipher(key: str | None) -> Fernet:
    """The cipher for `key`, or raise if mail isn't configured on this machine."""
    if key is None or not key.strip():
        raise MailNotConfigured(
            f"{KEY_NAME} is not set, so Noble Hunter can't store Gmail access. "
            "Generate one with `python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"` and add it to the environment.'
        )
    try:
        return Fernet(key.strip().encode())
    except (ValueError, TypeError) as error:
        raise MailNotConfigured(f"{KEY_NAME} is not a valid Fernet key") from error


def encrypt_token(cipher: Fernet, token: str) -> str:
    """The stored form of a refresh token."""
    return cipher.encrypt(token.encode()).decode()


def decrypt_token(cipher: Fernet, stored: str) -> str:
    """The refresh token back, or raise if this key can't read it (rotated key, altered row)."""
    try:
        return cipher.decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError) as error:
        raise MailNotConfigured(
            "A stored Gmail token couldn't be read with this MAIL_TOKEN_KEY. "
            "Reconnect the mailbox to store a fresh one."
        ) from error
```

- [ ] **Step 5: Run the test again**

Run: `uv run pytest tests/test_mail_crypto.py -q`

Expected: 7 passed.

- [ ] **Step 6: Add the settings**

In `core/settings.py`, add three optional fields to `Settings` (the pipeline's settings), below `database_url_unpooled`:

```python
    # Mail: the runner refreshes Gmail tokens and reads replies, so it needs the same key the
    # web app uses, plus the Google OAuth client the tokens were issued to. All optional: the
    # pipeline runs exactly as before until a mailbox is connected.
    mail_token_key: SecretStr | None = None
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
```

In `web/settings.py`, add one optional field to `WebSettings`, below `anthropic_api_key`:

```python
    # Optional on purpose: without it the Pitch mailbox card explains that mail isn't configured.
    mail_token_key: SecretStr | None = None
```

In `.env.example`, add a section at the end:

```
# --- Email pitching (both the Mac Mini and Vercel) --------------------------------------
# Fernet key that encrypts stored Gmail refresh tokens. The SAME value in both places.
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Without it, mail is simply switched off; changing it means reconnecting every mailbox.
MAIL_TOKEN_KEY=
```

Also extend the comment above `GOOGLE_CLIENT_ID` in that file to: `# Sign-in, and the Gmail connection. The Mac Mini needs these too, to refresh mail tokens.`

- [ ] **Step 7: Test the settings**

Add to `tests/test_settings.py`:

```python
def test_mail_settings_are_optional(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    monkeypatch.delenv("MAIL_TOKEN_KEY", raising=False)

    settings = load_settings(env_file=None)

    assert settings.mail_token_key is None
    assert settings.google_client_id is None
```

Add to `tests/test_web_foundation.py`, inside the settings tests:

```python
    def test_mail_token_key_is_optional(self, web_env):
        settings = load_web_settings(env_file=None)

        assert settings.mail_token_key is None
```

Check the surrounding fixtures in each file before adding, and match how they set the environment.

- [ ] **Step 8: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add pyproject.toml uv.lock core/mail_crypto.py core/settings.py web/settings.py .env.example tests
git commit -m "feat: encrypt stored Gmail tokens with MAIL_TOKEN_KEY

A refresh token is a long-lived key to a mailbox, so it never sits readable in the
database. Without the key, mail is simply switched off rather than half-working.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

Expected: 1440 passed, 5 skipped (1430, plus 8 from `test_mail_crypto.py` — the parametrized test collects three — plus one settings test each side).

One collision this task will hit: `tests/test_settings.py::test_repr_never_leaks_password` asserts the bare substring `"secret"` is absent from `Settings`' repr, and the new **field name** `google_client_secret` contains it. No value leaks — `SecretStr` masking still works — and the field must keep that name to match `GOOGLE_CLIENT_SECRET`. Fix the test by giving its sentinel password a distinctive value (not the word "secret"), keeping the assertion's intent, and leave a comment saying why.

---

### Task 2: Tables for mailboxes and messages (migration 0007)

**Files:**
- Modify: `core/models.py`, `core/db_roles.py`, `tests/factories.py`, `tests/test_db_roles.py`
- Create: `migrations/versions/20260916_0007_email_pitching.py`, `tests/test_schema_mail.py`, `tests/test_migration_0007.py`

Migration 0006 is the current head. Stage 3 of Artists and access will become 0008.

- [ ] **Step 1: Write the failing schema tests**

Create `tests/test_schema_mail.py`:

```python
"""Mailboxes belong to an artist, messages belong to a mailbox, and a profile can only use its own artist's."""

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from core.models import EmailMessage, MailAccount
from tests.factories import make_artist, make_curator, make_mailbox, make_outreach, make_profile


def test_the_mail_tables_exist(engine):
    assert {"mail_accounts", "email_messages"} <= set(inspect(engine).get_table_names())


def test_one_address_per_artist(session):
    artist = make_artist(session)
    make_mailbox(session, artist, address="synman@gmail.com")

    with pytest.raises(IntegrityError):
        make_mailbox(session, artist, address="synman@gmail.com")


def test_two_artists_can_connect_the_same_address(session):
    make_mailbox(session, make_artist(session), address="shared@gmail.com")

    assert make_mailbox(session, make_artist(session), address="shared@gmail.com").id is not None


def test_a_profile_cannot_use_another_artists_mailbox(session):
    theirs = make_mailbox(session, make_artist(session))
    profile = make_profile(session, artist=make_artist(session))

    profile.mail_account_id = theirs.id
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_profile_can_use_its_own_artists_mailbox(session):
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    profile = make_profile(session, artist=artist)

    profile.mail_account_id = mailbox.id
    session.flush()

    assert profile.mail_account_id == mailbox.id


def test_a_gmail_message_is_stored_once_per_mailbox(session):
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    entry = make_outreach(session, make_curator(session), make_profile(session, artist=artist), None)
    fields = {
        "mail_account_id": mailbox.id,
        "outreach_id": entry.id,
        "gmail_message_id": "m1",
        "gmail_thread_id": "t1",
        "direction": "out",
        "from_address": "synman@gmail.com",
        "to_address": "curator@example.com",
        "subject": "Hello",
        "body_text": "Hi there",
        "sent_at": entry.created_at,
    }
    session.add(EmailMessage(**fields))
    session.flush()

    session.add(EmailMessage(**fields))
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_same_gmail_id_can_exist_in_another_mailbox(session):
    artist = make_artist(session)
    profile = make_profile(session, artist=artist)
    entry = make_outreach(session, make_curator(session), profile, None)
    for address in ("one@gmail.com", "two@gmail.com"):
        mailbox = make_mailbox(session, artist, address=address)
        session.add(
            EmailMessage(
                mail_account_id=mailbox.id,
                outreach_id=entry.id,
                gmail_message_id="same",
                gmail_thread_id="t1",
                direction="out",
                from_address=address,
                to_address="curator@example.com",
                subject="Hello",
                body_text="Hi",
                sent_at=entry.created_at,
            )
        )
    session.flush()


def test_direction_must_be_in_or_out(session):
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    entry = make_outreach(session, make_curator(session), make_profile(session, artist=artist), None)
    session.add(
        EmailMessage(
            mail_account_id=mailbox.id,
            outreach_id=entry.id,
            gmail_message_id="m2",
            gmail_thread_id="t1",
            direction="up",  # three characters: `direction` is varchar(3), so a longer bad
            # value is refused for its length and never reaches the CHECK this test is about
            from_address="a@b.com",
            to_address="c@d.com",
            subject="",
            body_text="",
            sent_at=entry.created_at,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_one_send_key_across_the_app(session):
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    entry = make_outreach(session, make_curator(session), make_profile(session, artist=artist), None)
    for gmail_id in ("m3", "m4"):
        session.add(
            EmailMessage(
                mail_account_id=mailbox.id,
                outreach_id=entry.id,
                gmail_message_id=gmail_id,
                gmail_thread_id="t1",
                direction="out",
                from_address="a@b.com",
                to_address="c@d.com",
                subject="",
                body_text="",
                sent_at=entry.created_at,
                send_key="the-same-key",
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_profile_starts_with_the_default_conversation_settings(session):
    profile = make_profile(session)

    assert (profile.open_conversation_limit, profile.quiet_after_days) == (20, 14)


@pytest.mark.parametrize(("limit", "days"), [(0, 14), (201, 14), (20, 0), (20, 366)])
def test_the_conversation_settings_have_sane_bounds(session, limit, days):
    profile = make_profile(session)

    profile.open_conversation_limit, profile.quiet_after_days = limit, days
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_a_mailbox_detaches_the_profile(session):
    """The composite key must clear only the mailbox pointer, never the artist."""
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    profile = make_profile(session, artist=artist)
    profile.mail_account_id = mailbox.id
    session.flush()

    session.delete(mailbox)
    session.flush()
    session.expire(profile)  # the database cleared the column, not SQLAlchemy

    assert profile.mail_account_id is None
    assert profile.artist_id == artist.id


def test_a_mailbox_is_kept_when_a_profile_lets_go(session):
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    profile = make_profile(session, artist=artist)
    profile.mail_account_id = mailbox.id
    session.flush()

    profile.mail_account_id = None
    session.flush()

    assert session.get(MailAccount, mailbox.id) is not None
```

`make_outreach(session, curator, profile, None)` uses today's date; check the factory's signature and pass a real `date` if it has no default.

- [ ] **Step 2: Write the failing data-migration test**

Create `tests/test_migration_0007.py`:

```python
"""Migration 0007 adds mail without disturbing what's already there."""

from sqlalchemy import text

from tests.migration_helpers import downgrade_to, upgrade_to


def test_existing_profiles_and_outreach_survive(engine):
    try:
        downgrade_to("0006")
        with engine.begin() as connection:
            artist_id = connection.scalar(text("insert into artists (name) values ('Synman') returning id"))
            profile_id = connection.scalar(
                text("insert into profiles (name, artist_id) values ('IDM', :artist) returning id"),
                {"artist": artist_id},
            )

        upgrade_to("head")

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "select mail_account_id, open_conversation_limit, quiet_after_days "
                    "from profiles where id = :id"
                ),
                {"id": profile_id},
            ).one()
        assert row.mail_account_id is None
        assert (row.open_conversation_limit, row.quiet_after_days) == (20, 14)
    finally:
        upgrade_to("head")
        with engine.begin() as connection:
            connection.execute(text("truncate profiles, artists restart identity cascade"))
```

- [ ] **Step 3: Run both to watch them fail**

Run: `uv run pytest tests/test_schema_mail.py tests/test_migration_0007.py -q`

Expected: `ImportError: cannot import name 'MailAccount' from 'core.models'`, and the migration test failing because `open_conversation_limit` doesn't exist.

- [ ] **Step 4: Add the models**

In `core/models.py`, add to the allowed-value enums, next to `RouteType`:

```python
class MailDirection(StrEnum):
    OUT = "out"
    IN = "in"
```

Add a section after `ArtistMember` (mailboxes belong to artists, and `Profile` points at one):

```python
class MailAccount(Base):
    """A Gmail mailbox an artist pitches from. The refresh token is encrypted (core/mail_crypto.py).

    `(id, artist_id)` is unique so `profiles` can point a composite foreign key at it and the
    database itself refuses a profile using another artist's mailbox.
    """

    __tablename__ = "mail_accounts"
    __table_args__ = (
        UniqueConstraint("artist_id", "address"),
        UniqueConstraint("id", "artist_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(String(320))
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    history_id: Mapped[str | None] = mapped_column(String(40))
    connected_at: Mapped[datetime] = created_at_column()
    connected_by: Mapped[str | None] = mapped_column(String(320))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    needs_reconnect: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_error: Mapped[str | None] = mapped_column(Text)

    artist: Mapped["Artist"] = relationship()

    @property
    def is_connected(self) -> bool:
        return self.refresh_token_encrypted is not None and self.disconnected_at is None


class EmailMessage(Base):
    """One message in a pitch conversation, sent from Noble Hunter, from Gmail, or received."""

    __tablename__ = "email_messages"
    __table_args__ = (
        UniqueConstraint("mail_account_id", "gmail_message_id"),
        one_of("direction", MailDirection),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mail_account_id: Mapped[int] = mapped_column(ForeignKey("mail_accounts.id", ondelete="RESTRICT"))
    outreach_id: Mapped[int] = mapped_column(ForeignKey("outreach.id", ondelete="CASCADE"), index=True)
    gmail_message_id: Mapped[str] = mapped_column(String(64))
    gmail_thread_id: Mapped[str] = mapped_column(String(64), index=True)
    # The message's own RFC822 Message-ID, so our reply can quote it in In-Reply-To and land in
    # the curator's thread rather than starting a new one in their client.
    rfc822_message_id: Mapped[str | None] = mapped_column(String(400))
    direction: Mapped[str] = mapped_column(String(3))
    from_address: Mapped[str] = mapped_column(String(320))
    to_address: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text)
    quoted_text: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    send_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = created_at_column()
```

Change `Profile`:
- add to `__table_args__`, after the existing `UniqueConstraint`:

```python
        ForeignKeyConstraint(
            ["mail_account_id", "artist_id"],
            ["mail_accounts.id", "mail_accounts.artist_id"],
            name="mail_account_same_artist",
            # Names the column so only the mailbox pointer is cleared: a bare SET NULL would
            # null `artist_id` as well, which is NOT NULL, and the delete would fail instead.
            ondelete="SET NULL (mail_account_id)",
        ),
        CheckConstraint("open_conversation_limit between 1 and 200", name="open_conversation_limit"),
        CheckConstraint("quiet_after_days between 1 and 365", name="quiet_after_days"),
```

- add `ForeignKeyConstraint` to the `sqlalchemy` import at the top of the file;
- add these columns after `min_followers`:

```python
    # Which of the artist's mailboxes this profile pitches from (migration 0007).
    mail_account_id: Mapped[int | None] = mapped_column(Integer)
    # Conversation load (Jarred, 2026-09-16): the digest tops up to this many open conversations,
    # and a pitch stops counting as open once it has gone unanswered this long.
    open_conversation_limit: Mapped[int] = mapped_column(Integer, default=20, server_default="20")
    quiet_after_days: Mapped[int] = mapped_column(Integer, default=14, server_default="14")
```

Change `Outreach`: add after `profile_id`:

```python
    # The conversation, once a pitch has been sent (migration 0007). The mailbox is recorded here
    # so replies keep working even if the profile later pitches from a different one.
    mail_account_id: Mapped[int | None] = mapped_column(ForeignKey("mail_accounts.id", ondelete="RESTRICT"))
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64))
    draft_subject: Mapped[str | None] = mapped_column(Text)
    draft_body: Mapped[str | None] = mapped_column(Text)
    draft_track_id: Mapped[int | None] = mapped_column(ForeignKey("profile_tracks.id", ondelete="SET NULL"))
    draft_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

and add to its `__table_args__`: `UniqueConstraint("mail_account_id", "gmail_thread_id"),`.

- [ ] **Step 5: Write the migration**

Create `migrations/versions/20260916_0007_email_pitching.py`:

```python
"""email pitching: mailboxes, messages, drafts and the conversation ceiling

Jarred (2026-09-16): pitch curators by email from inside the digest and read replies there.
A mailbox belongs to an artist; a profile pitches from one of its artist's mailboxes, which a
composite foreign key enforces. Profiles also gain the conversation-load settings: the digest
tops up to `open_conversation_limit` open conversations, and a pitch stops counting as open
after `quiet_after_days` without an answer.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-16 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=320), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("history_id", sa.String(length=40), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("connected_by", sa.String(length=320), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("needs_reconnect", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["artist_id"], ["artists.id"], name=op.f("fk_mail_accounts_artist_id_artists"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_accounts")),
        sa.UniqueConstraint("artist_id", "address", name=op.f("uq_mail_accounts_artist_id_address")),
        # Lets profiles point a composite foreign key here, so a profile can only use its own
        # artist's mailbox.
        sa.UniqueConstraint("id", "artist_id", name=op.f("uq_mail_accounts_id_artist_id")),
    )
    op.create_index(op.f("ix_mail_accounts_artist_id"), "mail_accounts", ["artist_id"])

    op.create_table(
        "email_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mail_account_id", sa.Integer(), nullable=False),
        sa.Column("outreach_id", sa.Integer(), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=64), nullable=False),
        sa.Column("gmail_thread_id", sa.String(length=64), nullable=False),
        sa.Column("rfc822_message_id", sa.String(length=400), nullable=True),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("from_address", sa.String(length=320), nullable=False),
        sa.Column("to_address", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("quoted_text", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("send_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("direction in ('out', 'in')", name=op.f("ck_email_messages_direction")),
        sa.ForeignKeyConstraint(
            ["mail_account_id"],
            ["mail_accounts.id"],
            name=op.f("fk_email_messages_mail_account_id_mail_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["outreach_id"], ["outreach.id"], name=op.f("fk_email_messages_outreach_id_outreach"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_messages")),
        sa.UniqueConstraint(
            "mail_account_id", "gmail_message_id", name=op.f("uq_email_messages_mail_account_id_gmail_message_id")
        ),
        sa.UniqueConstraint("send_key", name=op.f("uq_email_messages_send_key")),
    )
    op.create_index(op.f("ix_email_messages_outreach_id"), "email_messages", ["outreach_id"])
    op.create_index(op.f("ix_email_messages_gmail_thread_id"), "email_messages", ["gmail_thread_id"])

    op.add_column("profiles", sa.Column("mail_account_id", sa.Integer(), nullable=True))
    op.add_column(
        "profiles",
        sa.Column("open_conversation_limit", sa.Integer(), server_default="20", nullable=False),
    )
    op.add_column("profiles", sa.Column("quiet_after_days", sa.Integer(), server_default="14", nullable=False))
    op.create_foreign_key(
        op.f("fk_profiles_mail_account_id_mail_accounts"),
        "profiles",
        "mail_accounts",
        ["mail_account_id", "artist_id"],
        ["id", "artist_id"],
        # Only the mailbox pointer is cleared. A bare SET NULL would null `artist_id` too -- it's
        # part of the composite key -- and that column is NOT NULL, so deleting a mailbox would
        # fail instead of detaching the profile. (Column lists need Postgres 15+.)
        ondelete="SET NULL (mail_account_id)",
    )
    op.create_check_constraint(
        op.f("ck_profiles_open_conversation_limit"), "profiles", "open_conversation_limit between 1 and 200"
    )
    op.create_check_constraint(
        op.f("ck_profiles_quiet_after_days"), "profiles", "quiet_after_days between 1 and 365"
    )

    op.add_column("outreach", sa.Column("mail_account_id", sa.Integer(), nullable=True))
    op.add_column("outreach", sa.Column("gmail_thread_id", sa.String(length=64), nullable=True))
    op.add_column("outreach", sa.Column("draft_subject", sa.Text(), nullable=True))
    op.add_column("outreach", sa.Column("draft_body", sa.Text(), nullable=True))
    op.add_column("outreach", sa.Column("draft_track_id", sa.Integer(), nullable=True))
    op.add_column("outreach", sa.Column("draft_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_outreach_mail_account_id_mail_accounts"),
        "outreach",
        "mail_accounts",
        ["mail_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_outreach_draft_track_id_profile_tracks"),
        "outreach",
        "profile_tracks",
        ["draft_track_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        op.f("uq_outreach_mail_account_id_gmail_thread_id"), "outreach", ["mail_account_id", "gmail_thread_id"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_outreach_mail_account_id_gmail_thread_id"), "outreach", type_="unique")
    op.drop_constraint(op.f("fk_outreach_draft_track_id_profile_tracks"), "outreach", type_="foreignkey")
    op.drop_constraint(op.f("fk_outreach_mail_account_id_mail_accounts"), "outreach", type_="foreignkey")
    for column in (
        "draft_updated_at",
        "draft_track_id",
        "draft_body",
        "draft_subject",
        "gmail_thread_id",
        "mail_account_id",
    ):
        op.drop_column("outreach", column)

    op.drop_constraint(op.f("ck_profiles_quiet_after_days"), "profiles", type_="check")
    op.drop_constraint(op.f("ck_profiles_open_conversation_limit"), "profiles", type_="check")
    op.drop_constraint(op.f("fk_profiles_mail_account_id_mail_accounts"), "profiles", type_="foreignkey")
    for column in ("quiet_after_days", "open_conversation_limit", "mail_account_id"):
        op.drop_column("profiles", column)

    op.drop_index(op.f("ix_email_messages_gmail_thread_id"), table_name="email_messages")
    op.drop_index(op.f("ix_email_messages_outreach_id"), table_name="email_messages")
    op.drop_table("email_messages")
    op.drop_index(op.f("ix_mail_accounts_artist_id"), table_name="mail_accounts")
    op.drop_table("mail_accounts")
```

- [ ] **Step 6: Add the factory**

In `tests/factories.py`, add `MailAccount` to the models import and append:

```python
def make_mailbox(session, artist: Artist, address: str | None = None, **overrides) -> MailAccount:
    fields = {
        "artist_id": artist.id,
        "address": address or f"mailbox{next(_sequence)}@gmail.com",
        "refresh_token_encrypted": "encrypted-refresh-token",
        "history_id": "1000",
        **overrides,
    }
    mailbox = MailAccount(**fields)
    session.add(mailbox)
    session.flush()
    return mailbox
```

- [ ] **Step 7: Grant the web and pipeline roles**

In `core/db_roles.py`:
- add `"mail_accounts",` and `"email_messages",` to `WEB_EDITABLE_TABLES`;
- add a new constant below `WEB_CURATOR_COLUMNS`:

```python
# The web app records an entry's draft and the thread it becomes; the rest of an outreach row
# stays pipeline-owned. A profile's `mail_account_id` needs no entry here -- the web role can
# already write every column of `profiles`.
WEB_OUTREACH_MAIL_COLUMNS = (
    "mail_account_id",
    "gmail_thread_id",
    "draft_subject",
    "draft_body",
    "draft_track_id",
    "draft_updated_at",
)
```

Extend the outreach grant line in `_statements` to include the new columns:

```python
        f"GRANT UPDATE ({', '.join(WEB_OUTREACH_COLUMNS + WEB_OUTREACH_MAIL_COLUMNS)}) ON outreach TO {web}",
```

In `tests/test_db_roles.py`, add to `TestWebRole`:

```python
    @pytest.mark.parametrize("table", ["mail_accounts", "email_messages"])
    def test_can_manage_mail(self, roles, table):
        assert all(can(roles, WEB, table, privilege) for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"))

    @pytest.mark.parametrize("column", ["mail_account_id", "gmail_thread_id", "draft_body"])
    def test_can_record_a_draft_and_its_thread(self, roles, column):
        assert can_on_column(roles, WEB, "outreach", column, "UPDATE")
```

and to `TestPipelineRole`:

```python
    @pytest.mark.parametrize("table", ["mail_accounts", "email_messages"])
    def test_can_read_and_write_mail(self, roles, table):
        assert all(can(roles, PIPELINE, table, p) for p in ("SELECT", "INSERT", "UPDATE", "DELETE"))
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/test_schema_mail.py tests/test_migration_0007.py tests/test_db_roles.py -q`

Expected: all pass.

If `test_a_profile_cannot_use_another_artists_mailbox` passes for the wrong reason (no constraint), check the composite foreign key exists:

```bash
uv run python -c "
from sqlalchemy import create_engine, inspect
from tests.conftest import TEST_DATABASE_URL
print([fk['name'] for fk in inspect(create_engine(TEST_DATABASE_URL)).get_foreign_keys('profiles')])
"
```

Expected to include `fk_profiles_mail_account_id_mail_accounts`.

- [ ] **Step 9: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add core/models.py core/db_roles.py migrations/versions/20260916_0007_email_pitching.py tests
git commit -m "feat: tables for mailboxes, messages and the conversation ceiling

A mailbox belongs to an artist, and the composite foreign key means a profile can only
pitch from its own artist's. Profiles gain the open-conversation settings now so the
digest can work to them later.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

Expected: about 1458 passed, 5 skipped.

---

### Task 3: A small Gmail client

**Files:**
- Create: `core/gmail.py`, `tests/test_gmail.py`

Modelled on `pipeline/web.py`: httpx, explicit timeouts, and an error type whose `kind` the callers switch on. It never logs a token or a message body.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gmail.py`:

```python
"""The Gmail client: refresh a token, send, and read messages, with failures sorted into kinds."""

import json

import httpx
import pytest

from core.gmail import ACCESS_TOKEN_URL, API_ROOT, Gmail, GmailError

REFRESH = "1//0refresh"
CLIENT = {"client_id": "id.apps.googleusercontent.com", "client_secret": "secret"}


def gmail(handler, refresh_token: str = REFRESH) -> Gmail:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Gmail(client, refresh_token=refresh_token, **CLIENT)


def token_response(expires_in: int = 3600) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "ya29.access", "expires_in": expires_in})


def test_it_refreshes_once_and_reuses_the_access_token():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        assert request.headers["authorization"] == "Bearer ya29.access"
        return httpx.Response(200, json={"emailAddress": "synman@gmail.com", "historyId": "9001"})

    api = gmail(handler)
    first = api.profile()
    second = api.profile()

    assert first == second == ("synman@gmail.com", "9001")
    assert calls.count(ACCESS_TOKEN_URL) == 1


def test_a_refused_refresh_token_is_an_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(GmailError) as error:
        gmail(handler).profile()

    assert error.value.kind == "auth"
    assert REFRESH not in str(error.value)


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (429, "transient"), (503, "transient"), (400, "rejected")],
)
def test_api_failures_are_sorted_into_kinds(status, kind):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(status, json={"error": {"message": "nope"}})

    with pytest.raises(GmailError) as error:
        gmail(handler).profile()

    assert error.value.kind == kind


def test_a_network_failure_is_transient_and_names_no_token():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        raise httpx.ConnectError("boom")

    with pytest.raises(GmailError) as error:
        gmail(handler).profile()

    assert error.value.kind == "transient"
    assert "ya29" not in str(error.value)


def test_send_posts_the_raw_message_and_returns_its_ids():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        sent["path"] = request.url.path
        sent["body"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"id": "m1", "threadId": "t1"})

    result = gmail(handler).send("cmF3", thread_id="t1")

    assert result == ("m1", "t1")
    assert sent["path"] == "/gmail/v1/users/me/messages/send"
    assert sent["body"] == {"raw": "cmF3", "threadId": "t1"}


def test_a_first_pitch_is_sent_without_a_thread():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        sent["body"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"id": "m1", "threadId": "t1"})

    gmail(handler).send("cmF3")

    assert sent["body"] == {"raw": "cmF3"}


def test_history_returns_added_message_ids_and_the_new_history_id():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(
            200,
            json={
                "historyId": "9100",
                "history": [
                    {"messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}]},
                    {"messagesAdded": [{"message": {"id": "m2", "threadId": "t2"}}]},
                    {"labelsAdded": [{"message": {"id": "ignored", "threadId": "t9"}}]},
                ],
            },
        )

    added, history_id = gmail(handler).history_since("9000")

    assert added == [("m1", "t1"), ("m2", "t2")]
    assert history_id == "9100"


def test_an_expired_history_id_is_its_own_kind():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(404, json={"error": {"message": "Requested entity was not found."}})

    with pytest.raises(GmailError) as error:
        gmail(handler).history_since("1")

    assert error.value.kind == "history-gone"


def test_an_answer_that_is_not_json_is_rejected_rather_than_crashing():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(200, text="<html>a captive portal</html>")

    with pytest.raises(GmailError) as error:
        gmail(handler).profile()

    assert error.value.kind == "rejected"


def test_a_send_that_names_no_message_is_rejected_rather_than_crashing():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(200, json={"nothing": "useful"})

    with pytest.raises(GmailError) as error:
        gmail(handler).send("cmF3")

    assert error.value.kind == "rejected"


def test_a_refused_access_token_is_not_presented_again():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(401, json={"error": {"message": "nope"}})

    api = gmail(handler)
    for _ in range(2):
        with pytest.raises(GmailError):
            api.profile()

    # Two refreshes, not one: the refused token was dropped rather than offered again.
    assert calls.count(ACCESS_TOKEN_URL) == 2


def test_message_and_thread_ask_for_full_format():
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        asked.append(str(request.url))
        return httpx.Response(200, json={"id": "m1", "threadId": "t1", "messages": []})

    api = gmail(handler)
    api.message("m1")
    api.thread("t1")

    assert f"{API_ROOT}/messages/m1?format=full" in asked[0]
    assert f"{API_ROOT}/threads/t1?format=full" in asked[1]
```

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run pytest tests/test_gmail.py -q`

Expected: `ModuleNotFoundError: No module named 'core.gmail'`.

- [ ] **Step 3: Write `core/gmail.py`**

```python
"""Just enough of the Gmail API to pitch and to read replies.

Both halves of Noble Hunter use this: the web app sends, and the runner reads replies, so it
lives in `core/` and depends only on httpx. It holds a refresh token (decrypted by the caller),
swaps it for a short-lived access token, and keeps that in memory until it expires.

Failures come back as `GmailError` with a `kind` the caller acts on:

- `auth`: Google refused the token. The mailbox needs reconnecting; nothing will fix itself.
- `transient`: rate limit, server error or network. Worth trying again later.
- `rejected`: Gmail understood and said no (a malformed message, a missing thread).
- `history-gone`: the mailbox's history id is too old, so the caller re-reads whole threads.

Nothing here logs a token, an address or a message body.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

ACCESS_TOKEN_URL = "https://oauth2.googleapis.com/token"
API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT_SECONDS = 30
TOKEN_EARLY_REFRESH_SECONDS = 60  # refresh a little before Google's expiry, to avoid a race


class GmailError(RuntimeError):
    """Gmail couldn't do it. `kind` is auth, transient, rejected or history-gone."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class _AccessToken:
    value: str
    expires_at: float


class Gmail:
    """One mailbox's API access. Build one per mailbox; it caches its access token in memory."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        now: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._now = now
        self._token: _AccessToken | None = None

    # --- the calls Noble Hunter makes ----------------------------------------------------

    def profile(self) -> tuple[str, str]:
        """The mailbox's own address and its current history id."""
        data = self._get(f"{API_ROOT}/profile")
        return str(data.get("emailAddress", "")), str(data.get("historyId", ""))

    def send(self, raw: str, *, thread_id: str | None = None) -> tuple[str, str]:
        """Send a base64url MIME message. Returns (message id, thread id)."""
        body: dict = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        data = self._post(f"{API_ROOT}/messages/send", body)
        try:
            return str(data["id"]), str(data["threadId"])
        except KeyError:
            # Callers switch on `kind`; a surprise shape must not escape as a bare KeyError.
            raise GmailError("rejected", "Gmail took the message but didn't say which one") from None

    def history_since(self, history_id: str) -> tuple[list[tuple[str, str]], str]:
        """Messages added since `history_id`, as (message id, thread id), and the new history id."""
        data = self._get(
            f"{API_ROOT}/history", params={"startHistoryId": history_id, "historyTypes": "messageAdded"}
        )
        added = [
            (str(item["message"]["id"]), str(item["message"]["threadId"]))
            for record in data.get("history", [])
            for item in record.get("messagesAdded", [])
        ]
        return added, str(data.get("historyId", history_id))

    def message(self, message_id: str) -> dict:
        return self._get(f"{API_ROOT}/messages/{message_id}", params={"format": "full"})

    def thread(self, thread_id: str) -> dict:
        return self._get(f"{API_ROOT}/threads/{thread_id}", params={"format": "full"})

    # --- plumbing -------------------------------------------------------------------------

    def _access_token(self) -> str:
        if self._token is not None and self._now() < self._token.expires_at:
            return self._token.value
        try:
            response = self._client.post(
                ACCESS_TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            raise GmailError("transient", f"Couldn't reach Google ({type(error).__name__})") from None
        if response.status_code != 200:
            # A refused refresh token never fixes itself: the mailbox has to be reconnected.
            raise GmailError("auth", "Google refused the saved Gmail access; reconnect the mailbox")
        payload = response.json()
        expires_in = int(payload.get("expires_in", 3600)) - TOKEN_EARLY_REFRESH_SECONDS
        self._token = _AccessToken(str(payload["access_token"]), self._now() + max(expires_in, 0))
        return self._token.value

    def _get(self, url: str, params: dict | None = None) -> dict:
        return self._call("GET", url, params=params)

    def _post(self, url: str, body: dict) -> dict:
        return self._call("POST", url, json=body)

    def _call(self, method: str, url: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        try:
            response = self._client.request(method, url, headers=headers, timeout=TIMEOUT_SECONDS, **kwargs)
        except httpx.HTTPError as error:
            raise GmailError("transient", f"Couldn't reach Gmail ({type(error).__name__})") from None
        if response.status_code < 400:
            return _payload(response, url)
        kind = _kind(response, url)
        if kind == "auth":
            # Google has refused the token we hold; don't keep presenting it until it expires.
            self._token = None
        raise GmailError(kind, f"Gmail said {response.status_code} to {_what(url)}")


def _payload(response: httpx.Response, url: str) -> dict:
    """Gmail's answer as a dict, or a `rejected` error -- never a raw ValueError past the kinds."""
    try:
        data = response.json()
    except ValueError:
        raise GmailError("rejected", f"Gmail's answer about {_what(url)} wasn't JSON") from None
    if not isinstance(data, dict):
        raise GmailError("rejected", f"Gmail's answer about {_what(url)} wasn't a message")
    return data


def _kind(response: httpx.Response, url: str) -> str:
    if response.status_code in (401, 403):
        return "auth"
    if response.status_code == 404 and "/history" in url:
        return "history-gone"  # Gmail drops history older than about a week
    if response.status_code == 429 or response.status_code >= 500:
        return "transient"
    return "rejected"


def _what(url: str) -> str:
    """The call being made, with no ids or addresses in it."""
    return url.removeprefix(API_ROOT).split("?")[0].strip("/").split("/")[0] or "the mailbox"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_gmail.py -q`

Expected: 16 passed. If `_what` makes an assertion fail, print the message in the failing test and adjust `_what`, not the error kinds.

- [ ] **Step 5: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add core/gmail.py tests/test_gmail.py
git commit -m "feat: a small Gmail client with failure kinds the callers act on

Reconnect, retry and refuse are different problems, so the client sorts Google's answers
into kinds instead of leaking HTTP codes into the routes. Tokens never reach the logs.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Building and reading plain-text mail

**Files:**
- Create: `core/mime.py`, `tests/test_mime.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mime.py`:

```python
"""Building the message Gmail sends, and reading back what Gmail returns."""

import base64
from datetime import UTC, datetime
from email import message_from_bytes, policy

import pytest

from core.mime import HeaderProblem, build_message, parse_message


def decoded(raw: str) -> str:
    return base64.urlsafe_b64decode(raw.encode()).decode()


def gmail_payload(
    *,
    body: str = "Hi there",
    subject: str = "A track for Glitch Garden",
    sender: str = "Synman <synman@gmail.com>",
    to: str = "curator@example.com",
    message_id: str = "m1",
    thread_id: str = "t1",
    internal_ms: int = 1_789_000_000_000,
    html_only: bool = False,
) -> dict:
    part = {
        "mimeType": "text/html" if html_only else "text/plain",
        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
    }
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": str(internal_ms),
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": "<abc@mail.gmail.com>"},
            ],
            "mimeType": "multipart/alternative",
            "parts": [part],
        },
    }


class TestBuilding:
    def test_a_first_pitch_has_the_fields_gmail_needs(self):
        raw = build_message(
            from_address="synman@gmail.com",
            to_address="curator@example.com",
            subject="A track for Glitch Garden",
            body="Hi there\n\nSynman",
        )

        text = decoded(raw)
        assert "To: curator@example.com" in text
        assert "Subject: A track for Glitch Garden" in text
        assert text.rstrip().endswith("Synman")
        assert "Content-Type: text/plain" in text

    def test_a_reply_threads_onto_the_last_message(self):
        raw = build_message(
            from_address="synman@gmail.com",
            to_address="curator@example.com",
            subject="Re: A track for Glitch Garden",
            body="Thanks!",
            in_reply_to="<abc@mail.gmail.com>",
            references=("<first@mail.gmail.com>", "<abc@mail.gmail.com>"),
        )

        text = decoded(raw)
        assert "In-Reply-To: <abc@mail.gmail.com>" in text
        assert "References: <first@mail.gmail.com> <abc@mail.gmail.com>" in text

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("to_address", "curator@example.com\nBcc: sneak@example.com"),
            ("to_address", "curator@example.com\r\nBcc: sneak@example.com"),
            ("subject", "Hello\nBcc: sneak@example.com"),
        ],
    )
    def test_a_header_cannot_carry_a_second_line(self, field, value):
        fields = {
            "from_address": "synman@gmail.com",
            "to_address": "curator@example.com",
            "subject": "Hello",
            "body": "Hi",
            field: value,
        }

        with pytest.raises(HeaderProblem):
            build_message(**fields)

    def test_the_body_may_contain_anything(self):
        raw = build_message(
            from_address="synman@gmail.com",
            to_address="curator@example.com",
            subject="Hello",
            body="Line one\nBcc: this is just text\n\n-- \nSynman",
        )

        assert "Bcc: this is just text" in decoded(raw)


class TestReading:
    def test_it_reads_the_plain_text_part(self):
        parsed = parse_message(gmail_payload(body="Hi there\n\nSynman"))

        assert parsed.gmail_message_id == "m1"
        assert parsed.gmail_thread_id == "t1"
        assert parsed.from_address == "synman@gmail.com"
        assert parsed.to_address == "curator@example.com"
        assert parsed.subject == "A track for Glitch Garden"
        assert parsed.body_text == "Hi there\n\nSynman"
        assert parsed.quoted_text is None
        assert parsed.sent_at == datetime(2026, 9, 10, 0, 26, 40, tzinfo=UTC)
        assert parsed.message_id_header == "<abc@mail.gmail.com>"

    def test_quoted_history_is_split_off(self):
        body = (
            "Yes please, send it over.\n\n"
            "On Tue, 15 Sep 2026 at 09:12, Synman <synman@gmail.com> wrote:\n"
            "> Hi there, I have a track\n"
        )

        parsed = parse_message(gmail_payload(body=body))

        assert parsed.body_text == "Yes please, send it over."
        assert parsed.quoted_text.startswith("On Tue, 15 Sep 2026")

    def test_a_wrapped_attribution_line_still_splits(self):
        # Gmail wraps a long "On ... wrote:" across two lines. Missing it would keep the whole
        # quoted thread in the body, and the Inbox would show it repeated down the page.
        body = (
            "Yes please, send it over.\n\n"
            "On Tue, 15 Sep 2026 at 09:12, Synman <synman@gmail.com>\n"
            "wrote:\n"
            "> Hi there, I have a track\n"
        )

        parsed = parse_message(gmail_payload(body=body))

        assert parsed.body_text == "Yes please, send it over."
        assert parsed.quoted_text.startswith("On Tue, 15 Sep 2026")

    def test_a_reply_quoted_with_chevrons_alone_still_splits(self):
        body = "Sounds good.\n\n> Hi there, I have a track\n> called Glass Weather\n"

        parsed = parse_message(gmail_payload(body=body))

        assert parsed.body_text == "Sounds good."
        assert parsed.quoted_text.startswith("> Hi there")

    def test_the_word_wrote_in_a_sentence_is_not_a_quote(self):
        body = "I wrote: this is still my own message, honestly."

        parsed = parse_message(gmail_payload(body=body))

        assert parsed.body_text == body
        assert parsed.quoted_text is None

    def test_an_html_only_message_becomes_readable_text(self):
        html = "<p>Hi <b>there</b></p><p>Send it</p>"

        parsed = parse_message(gmail_payload(body=html, html_only=True))

        assert "Hi there" in parsed.body_text
        assert "<p>" not in parsed.body_text

    def test_a_display_name_is_kept_out_of_the_address(self):
        parsed = parse_message(gmail_payload(sender="Nik Davies <nik@valleyview.example>"))

        assert parsed.from_address == "nik@valleyview.example"

    def test_a_message_with_no_body_parses_as_empty(self):
        payload = gmail_payload()
        payload["payload"] = {"headers": payload["payload"]["headers"], "mimeType": "text/plain", "body": {}}

        parsed = parse_message(payload)

        assert parsed.body_text == ""

    def test_a_message_we_built_reads_back_the_same(self):
        # Gmail hands a sent message back through sync, so what we build must survive the trip.
        body = "Hi there\n\nThe track is called Glass Weather.\n\nSynman"
        raw = build_message(
            from_address="synman@gmail.com",
            to_address="curator@example.com",
            subject="A track for Glitch Garden",
            body=body,
        )
        # policy.default, or this is a legacy Message with no `get_content()`.
        sent = message_from_bytes(base64.urlsafe_b64decode(raw.encode()), policy=policy.default)

        parsed = parse_message(gmail_payload(body=sent.get_content(), subject=sent["Subject"]))

        assert parsed.body_text == body
        assert parsed.subject == "A track for Glitch Garden"
```

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run pytest tests/test_mime.py -q`

Expected: `ModuleNotFoundError: No module named 'core.mime'`.

- [ ] **Step 3: Write `core/mime.py`**

```python
"""The message we send, and the message Gmail gives back.

Plain text only, which is what a curator's inbox actually wants and what keeps this small: no
attachments, no HTML, no tracking pixel. Header fields are checked for line breaks, because a
newline in an address or subject would otherwise let someone add their own headers (a Bcc, say)
from a form field.

Reading is the other direction: Gmail hands back a nested payload, and callers want the sender,
the time, and the part a person actually wrote, with the quoted history below it split off so
a thread doesn't repeat itself on screen.
"""

import base64
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser

# Where quoted history begins. Three shapes, because clients differ:
#
# - the "On <date> X wrote:" attribution. Gmail *wraps* this when it's long, so the pattern
#   crosses newlines rather than assuming one line -- otherwise a real reply keeps the whole
#   thread in its body and the Inbox shows the same paragraphs over and over;
# - the divider some clients use instead;
# - chevrons alone, from a client that writes no attribution line at all.
ATTRIBUTION = re.compile(r"^On\s[\s\S]{5,200}?\bwrote:[ \t]*$", re.MULTILINE)
DIVIDER = re.compile(r"^(-{2,} ?Original Message ?-{2,}|_{10,})[ \t]*$", re.MULTILINE)
CHEVRON = re.compile(r"^>", re.MULTILINE)
QUOTE_PATTERNS = (ATTRIBUTION, DIVIDER, CHEVRON)


class HeaderProblem(ValueError):
    """A header field that can't be used as given (a line break, or no address at all)."""


@dataclass(frozen=True)
class ParsedMessage:
    gmail_message_id: str
    gmail_thread_id: str
    from_address: str
    to_address: str
    subject: str
    body_text: str
    quoted_text: str | None
    sent_at: datetime
    message_id_header: str | None


def build_message(
    *,
    from_address: str,
    to_address: str,
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: tuple[str, ...] = (),
) -> str:
    """A plain-text message as base64url, ready for Gmail's `raw` field."""
    message = EmailMessage()
    message["From"] = _header("From", from_address)
    message["To"] = _header("To", to_address)
    message["Subject"] = _header("Subject", subject)
    if in_reply_to:
        message["In-Reply-To"] = _header("In-Reply-To", in_reply_to)
    if references:
        message["References"] = _header("References", " ".join(references))
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def parse_message(payload: dict) -> ParsedMessage:
    """One Gmail message (format=full) as the fields Noble Hunter stores."""
    headers = {
        str(header.get("name", "")).lower(): str(header.get("value", ""))
        for header in payload.get("payload", {}).get("headers", [])
    }
    body, is_html = _first_body(payload.get("payload", {}))
    text = _as_text(body, is_html)
    written, quoted = _split_quoted(text)
    sent_ms = int(payload.get("internalDate") or 0)
    return ParsedMessage(
        gmail_message_id=str(payload.get("id", "")),
        gmail_thread_id=str(payload.get("threadId", "")),
        from_address=parseaddr(headers.get("from", ""))[1],
        to_address=parseaddr(headers.get("to", ""))[1],
        subject=headers.get("subject", ""),
        body_text=written,
        quoted_text=quoted,
        sent_at=datetime.fromtimestamp(sent_ms / 1000, tz=UTC),
        message_id_header=headers.get("message-id") or None,
    )


def _header(name: str, value: str) -> str:
    clean = value.strip()
    if not clean:
        raise HeaderProblem(f"{name} is empty")
    if "\n" in clean or "\r" in clean:
        raise HeaderProblem(f"{name} can't contain a line break")
    return clean


def _first_body(part: dict) -> tuple[str, bool]:
    """The best body in a Gmail payload: plain text if there is any, otherwise HTML."""
    plain = _find(part, "text/plain")
    if plain is not None:
        return plain, False
    html = _find(part, "text/html")
    return (html, True) if html is not None else ("", False)


def _find(part: dict, mime_type: str) -> str | None:
    if part.get("mimeType") == mime_type:
        data = part.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data.encode()).decode("utf-8", "replace")
    for child in part.get("parts", []):
        found = _find(child, mime_type)
        if found is not None:
            return found
    return None


def _as_text(body: str, is_html: bool) -> str:
    return _strip_html(body) if is_html else body.replace("\r\n", "\n").strip()


def _split_quoted(text: str) -> tuple[str, str | None]:
    """What the person wrote, and the thread they were replying to.

    Whichever marker comes first wins: a reply often carries an attribution line *and* chevrons,
    and the quote starts at the earliest of them.
    """
    starts = [found.start() for found in (pattern.search(text) for pattern in QUOTE_PATTERNS) if found]
    if not starts:
        return text.strip(), None
    start = min(starts)
    return text[:start].strip(), text[start:].strip() or None


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"p", "br", "div", "tr"}:
            self.parts.append("\n")


def _strip_html(html: str) -> str:
    parser = _TextOnly()
    parser.feed(html)
    lines = [line.strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_mime.py -q`

Expected: 15 passed. The `sent_at` assertion pins the epoch conversion; if it fails, check the test's `internal_ms` against `datetime.fromtimestamp(1_789_000_000, tz=UTC)` rather than loosening the assertion.

The three quote-splitting tests are the ones that matter for real mail: every client marks quoted history differently, and getting this wrong means the Inbox shows the same paragraphs repeated down the page.

- [ ] **Step 5: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add core/mime.py tests/test_mime.py
git commit -m "feat: build and read the plain-text mail Noble Hunter sends

Header fields are refused if they carry a line break, so a form field can't smuggle in a
Bcc. Reading splits off quoted history so a thread doesn't repeat itself on screen.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Stage 2: Connect a mailbox

### Task 5: An artist's mailboxes

**Files:**
- Create: `core/mailboxes.py`, `tests/test_mailboxes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mailboxes.py`:

```python
"""Connecting, sharing and letting go of an artist's Gmail mailboxes."""

from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet

from core.mail_crypto import mail_cipher
from core.mailboxes import (
    MailboxProblem,
    artist_mailboxes,
    attach_mailbox,
    connect_mailbox,
    detach_mailbox,
    mark_needs_reconnect,
    refresh_token_for,
)
from tests.factories import make_artist, make_mailbox, make_profile

NOW = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
TOKEN = "1//0the-refresh-token"


@pytest.fixture
def cipher():
    return mail_cipher(Fernet.generate_key().decode())


def connect(session, artist, cipher, address="synman@gmail.com", **overrides):
    fields = {
        "artist_id": artist.id,
        "address": address,
        "refresh_token": TOKEN,
        "cipher": cipher,
        "history_id": "9000",
        "connected_by": "owner@example.com",
        "now": NOW,
        **overrides,
    }
    return connect_mailbox(session, **fields)


class TestConnecting:
    def test_it_stores_the_token_encrypted(self, session, cipher):
        mailbox = connect(session, make_artist(session), cipher)

        assert mailbox.address == "synman@gmail.com"
        assert TOKEN not in (mailbox.refresh_token_encrypted or "")
        assert refresh_token_for(mailbox, cipher) == TOKEN
        assert mailbox.is_connected

    def test_connecting_the_same_address_again_refreshes_it(self, session, cipher):
        artist = make_artist(session)
        first = connect(session, artist, cipher)
        mark_needs_reconnect(session, first, "Google refused the saved Gmail access")

        again = connect(session, artist, cipher, refresh_token="1//0new-token", history_id="9500")

        assert again.id == first.id
        assert refresh_token_for(again, cipher) == "1//0new-token"
        assert again.history_id == "9500"
        assert not again.needs_reconnect
        assert again.last_error is None
        assert again.disconnected_at is None

    def test_two_artists_keep_separate_mailboxes_for_one_address(self, session, cipher):
        mine = connect(session, make_artist(session), cipher)
        theirs = connect(session, make_artist(session), cipher)

        assert mine.id != theirs.id

    def test_the_same_address_in_different_letters_is_one_mailbox(self, session, cipher):
        artist = make_artist(session)
        first = connect(session, artist, cipher, address="Synman@Gmail.com")

        again = connect(session, artist, cipher, address="synman@gmail.com")

        assert again.id == first.id
        assert again.address == "synman@gmail.com"

    def test_a_disconnected_mailbox_has_no_token_to_read(self, session, cipher):
        artist = make_artist(session)
        profile = make_profile(session, artist=artist)
        mailbox = connect(session, artist, cipher)
        attach_mailbox(session, profile, mailbox)

        detach_mailbox(session, profile, now=NOW)

        assert profile.mail_account_id is None
        assert not mailbox.is_connected
        with pytest.raises(MailboxProblem, match="isn't connected"):
            refresh_token_for(mailbox, cipher)


class TestAttaching:
    def test_a_profile_pitches_from_its_artists_mailbox(self, session, cipher):
        artist = make_artist(session)
        profile = make_profile(session, artist=artist)
        mailbox = connect(session, artist, cipher)

        attach_mailbox(session, profile, mailbox)

        assert profile.mail_account_id == mailbox.id

    def test_another_artists_mailbox_is_refused(self, session, cipher):
        theirs = connect(session, make_artist(session), cipher)
        profile = make_profile(session, artist=make_artist(session))

        with pytest.raises(MailboxProblem, match="another artist"):
            attach_mailbox(session, profile, theirs)

        assert profile.mail_account_id is None

    def test_two_profiles_can_share_one_mailbox(self, session, cipher):
        artist = make_artist(session)
        mailbox = connect(session, artist, cipher)
        first, second = make_profile(session, artist=artist), make_profile(session, artist=artist)

        attach_mailbox(session, first, mailbox)
        attach_mailbox(session, second, mailbox)

        assert (first.mail_account_id, second.mail_account_id) == (mailbox.id, mailbox.id)

    def test_letting_go_keeps_the_mailbox_while_another_profile_uses_it(self, session, cipher):
        artist = make_artist(session)
        mailbox = connect(session, artist, cipher)
        first, second = make_profile(session, artist=artist), make_profile(session, artist=artist)
        attach_mailbox(session, first, mailbox)
        attach_mailbox(session, second, mailbox)

        released = detach_mailbox(session, first, now=NOW)

        assert released is None  # still in use, so the caller must not revoke it at Google
        assert mailbox.is_connected
        assert second.mail_account_id == mailbox.id

    def test_the_last_profile_letting_go_hands_the_mailbox_back(self, session, cipher):
        artist = make_artist(session)
        mailbox = connect(session, artist, cipher)
        profile = make_profile(session, artist=artist)
        attach_mailbox(session, profile, mailbox)

        released = detach_mailbox(session, profile, now=NOW)

        assert released is mailbox
        assert mailbox.refresh_token_encrypted is None
        assert mailbox.disconnected_at == NOW

    def test_a_mailbox_nobody_is_connected_to_cannot_be_attached(self, session, cipher):
        artist = make_artist(session)
        mailbox = connect(session, artist, cipher)
        first = make_profile(session, artist=artist)
        attach_mailbox(session, first, mailbox)
        detach_mailbox(session, first, now=NOW)

        with pytest.raises(MailboxProblem, match="isn't connected"):
            attach_mailbox(session, make_profile(session, artist=artist), mailbox)

    def test_letting_go_clears_a_stale_error(self, session, cipher):
        artist = make_artist(session)
        mailbox = connect(session, artist, cipher)
        profile = make_profile(session, artist=artist)
        attach_mailbox(session, profile, mailbox)
        mark_needs_reconnect(session, mailbox, "Google refused the saved Gmail access")

        detach_mailbox(session, profile, now=NOW)

        assert mailbox.last_error is None

    def test_detaching_a_profile_with_no_mailbox_does_nothing(self, session):
        assert detach_mailbox(session, make_profile(session), now=NOW) is None


class TestListing:
    def test_it_lists_only_this_artists_connected_mailboxes(self, session, cipher):
        artist = make_artist(session)
        connected = connect(session, artist, cipher, address="a@gmail.com")
        connect(session, make_artist(session), cipher, address="b@gmail.com")
        gone = connect(session, artist, cipher, address="c@gmail.com")
        gone.refresh_token_encrypted, gone.disconnected_at = None, NOW
        session.flush()

        assert [mailbox.id for mailbox in artist_mailboxes(session, artist.id)] == [connected.id]

    def test_needing_reconnection_still_lists(self, session, cipher):
        artist = make_artist(session)
        mailbox = connect(session, artist, cipher)
        mark_needs_reconnect(session, mailbox, "Google refused the saved Gmail access")

        listed = artist_mailboxes(session, artist.id)

        assert [m.id for m in listed] == [mailbox.id]
        assert listed[0].needs_reconnect

    def test_a_mailbox_from_the_factory_reads_back(self, session):
        artist = make_artist(session)
        made = make_mailbox(session, artist)

        assert [m.id for m in artist_mailboxes(session, artist.id)] == [made.id]
```

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run pytest tests/test_mailboxes.py -q`

Expected: `ModuleNotFoundError: No module named 'core.mailboxes'`.

- [ ] **Step 3: Write `core/mailboxes.py`**

```python
"""An artist's Gmail mailboxes: connecting one, sharing it between profiles, and letting go.

A mailbox belongs to an artist, not to a profile, so two profiles for the same artist can pitch
from one Gmail. The database enforces that with a composite foreign key (migration 0007); this
module refuses it earlier, with a message a person can read.

Connecting the same address twice refreshes the row rather than making a second one: Google
issues a new refresh token each time someone goes through consent, and the old one may already
have been revoked. Letting go only hands the mailbox back once no profile uses it, because the
caller then revokes it at Google; until then its threads must keep working.
"""

from datetime import datetime

from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.mail_crypto import encrypt_token
from core.mail_crypto import decrypt_token as _decrypt
from core.models import MailAccount, Profile


class MailboxProblem(ValueError):
    """Something a person can fix: the wrong artist's mailbox, or one that isn't connected."""


def connect_mailbox(
    session: Session,
    *,
    artist_id: int,
    address: str,
    refresh_token: str,
    cipher: Fernet,
    history_id: str,
    connected_by: str,
    now: datetime,
) -> MailAccount:
    """Store Gmail access for this artist, replacing anything held for the same address."""
    mailbox = session.scalar(
        select(MailAccount).where(
            MailAccount.artist_id == artist_id, func.lower(MailAccount.address) == address.strip().lower()
        )
    )
    if mailbox is None:
        # Stored lowercased, like every other address in the app (users, contacts, members):
        # the unique constraint is on the raw column, so two spellings would otherwise be two
        # mailboxes for one Gmail, and the lookup above would pick between them arbitrarily.
        mailbox = MailAccount(artist_id=artist_id, address=address.strip().lower())
        session.add(mailbox)
    mailbox.refresh_token_encrypted = encrypt_token(cipher, refresh_token)
    mailbox.history_id = history_id
    mailbox.connected_at = now
    mailbox.connected_by = connected_by
    mailbox.disconnected_at = None
    mailbox.needs_reconnect = False
    mailbox.last_error = None
    session.flush()
    return mailbox


def refresh_token_for(mailbox: MailAccount, cipher: Fernet) -> str:
    """The mailbox's refresh token, or raise if it isn't connected any more."""
    if not mailbox.is_connected or mailbox.refresh_token_encrypted is None:
        raise MailboxProblem(f"{mailbox.address} isn't connected any more. Connect it again to use it.")
    return _decrypt(cipher, mailbox.refresh_token_encrypted)


def attach_mailbox(session: Session, profile: Profile, mailbox: MailAccount) -> None:
    """Pitch this profile's mail from `mailbox`."""
    if mailbox.artist_id != profile.artist_id:
        raise MailboxProblem("That mailbox belongs to another artist.")
    if not mailbox.is_connected:
        raise MailboxProblem(f"{mailbox.address} isn't connected any more. Connect it again to use it.")
    profile.mail_account_id = mailbox.id
    session.flush()


def detach_mailbox(session: Session, profile: Profile, *, now: datetime) -> MailAccount | None:
    """Stop pitching this profile from its mailbox.

    Returns the mailbox only when no profile uses it any more, meaning the caller should revoke
    it at Google. Its stored token is cleared here either way in that case; past messages stay.
    """
    mailbox = session.get(MailAccount, profile.mail_account_id) if profile.mail_account_id else None
    profile.mail_account_id = None
    session.flush()
    if mailbox is None or _still_used(session, mailbox.id):
        return None
    mailbox.refresh_token_encrypted = None
    mailbox.disconnected_at = now
    mailbox.needs_reconnect = False
    mailbox.last_error = None  # whatever went wrong last is over; don't keep showing it
    session.flush()
    return mailbox


def mark_needs_reconnect(session: Session, mailbox: MailAccount, message: str) -> None:
    """Google refused this mailbox's access; nothing will work until someone reconnects it."""
    mailbox.needs_reconnect = True
    mailbox.last_error = message
    session.flush()


def artist_mailboxes(session: Session, artist_id: int) -> list[MailAccount]:
    """This artist's connected mailboxes, including any that need reconnecting."""
    return list(
        session.scalars(
            select(MailAccount)
            .where(
                MailAccount.artist_id == artist_id,
                MailAccount.disconnected_at.is_(None),
                MailAccount.refresh_token_encrypted.is_not(None),
            )
            .order_by(func.lower(MailAccount.address), MailAccount.id)
        )
    )


def _still_used(session: Session, mail_account_id: int) -> bool:
    return (
        session.scalar(
            select(Profile.id).where(Profile.mail_account_id == mail_account_id).limit(1)
        )
        is not None
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_mailboxes.py -q`

Expected: 16 passed.

- [ ] **Step 5: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add core/mailboxes.py tests/test_mailboxes.py
git commit -m "feat: an artist's mailboxes, shared by their profiles

Connecting the same address twice refreshes it, because Google issues a new refresh token
each time. Letting go only hands the mailbox back when no profile still pitches from it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The Pitch mailbox card

**Files:**
- Create: `web/mail.py`, `web/templates/mail/_card.html`, `tests/test_web_mail.py`
- Modify: `web/app.py`, `web/profiles.py`, `web/templates/profiles/detail.html`, `web/static/css/app.css`, `tests/route_walk.py`, `tests/access_world.py`

Four routes, all scoped like every other profile route. Google's consent screen is separate from sign-in: it asks for the two Gmail scopes, and the app already published means no extra test-user step.

- [ ] **Step 1: Add the routes to the walk first**

In `tests/route_walk.py`, add to `ROUTES` (keeping the file's order, after the suggest routes):

```python
    ("GET", "/profiles/{profile_id}/mail/connect"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/mail/attach"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/mail/disconnect"): NOT_FOUND,
    ("GET", "/mail/callback"): NOT_FOUND,
```

and to `FORMS`:

```python
    "/profiles/{profile_id}/mail/attach": {"mail_account_id": "0"},
```

`/mail/callback` carries no ids, so the sweeps skip it naturally. It answers 404 for anyone whose session holds no pending connection, which is what an outsider's walk sends.

Run: `uv run pytest tests/test_web_access.py -q`

Expected: FAIL. `test_every_route_is_covered` now lists four routes the app doesn't have.

- [ ] **Step 2: Write the failing route tests**

Create `tests/test_web_mail.py`:

```python
"""Connecting a Gmail mailbox to a profile, sharing it, and disconnecting it."""

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet

from core.mail_crypto import mail_cipher
from core.mailboxes import artist_mailboxes, refresh_token_for
from core.models import MailAccount, Profile
from tests.factories import make_artist, make_mailbox, make_member, make_profile, make_user
from tests.web_helpers import csrf_token, member_client, web_settings

KEY = Fernet.generate_key().decode()
HTML = {"accept": "text/html"}


class FakeGoogleMail:
    """Stands in for Google's token endpoint and Gmail's profile call during the connect flow."""

    def __init__(self, address: str = "synman@gmail.com", refresh_token: str = "1//0refresh"):
        self.address = address
        self.refresh_token = refresh_token
        self.codes: list[str] = []
        self.error: Exception | None = None

    def exchange(self, code: str) -> tuple[str, str, str]:
        self.codes.append(code)
        if self.error:
            raise self.error
        return self.refresh_token, self.address, "9000"


@pytest.fixture
def mail_settings():
    return web_settings().model_copy(update={"mail_token_key": KEY})


def client_for(session, email="nik@example.com", *, settings=None, exchange=None):
    """A signed-in member's client with mail configured and Google's half faked."""
    from tests.web_helpers import app_client, sign_in, FakeGoogle

    google = FakeGoogle()
    google.userinfo["email"] = email
    client = app_client(session, google, settings=settings, mail_exchange=exchange)
    sign_in(client)
    return client
```

Stop at this point and read `tests/web_helpers.py`: `app_client` doesn't take `settings` or `mail_exchange` yet. Add both in Step 3 before finishing this test file.

- [ ] **Step 3: Let the test helpers carry mail settings and a fake Google**

In `tests/web_helpers.py`:
- give `app_client` a `settings` argument: `def app_client(session, google=None, *, settings=None, **app_options)` and pass `settings or web_settings()` into `create_app`;
- pass `**app_options` straight through, so `mail_exchange=` reaches `create_app`.

In `web/app.py`, give `create_app` one more seam, beside `suggester`:

```python
def create_app(
    settings: WebSettings,
    identity_provider: IdentityProvider | None = None,
    suggester: Suggester | None = None,
    mail_exchange: MailExchange | None = None,
) -> FastAPI:
```

with `app.state.mail_exchange = mail_exchange or google_mail_exchange(settings)` after the suggester line, and `mail_router` added to the `signed_in` loop. `MailExchange` and `google_mail_exchange` come from `web/mail.py` in the next step.

- [ ] **Step 4: Finish the route tests**

Append to `tests/test_web_mail.py`:

```python
def a_member(session, *, settings, exchange=None):
    artist = make_artist(session, "Synman")
    profile = make_profile(session, "IDM Playlists", artist=artist)
    make_member(session, artist, make_user(session, "nik@example.com"))
    client = client_for(session, settings=settings, exchange=exchange)
    return client, artist, profile


class TestTheCard:
    def test_it_offers_to_connect_when_there_is_no_mailbox(self, session, mail_settings):
        client, _artist, profile = a_member(session, settings=mail_settings)

        html = client.get(f"/profiles/{profile.id}", headers=HTML).text

        assert "Pitch mailbox" in html
        assert f'href="/profiles/{profile.id}/mail/connect"' in html

    def test_it_names_the_mailbox_once_connected(self, session, mail_settings):
        client, artist, profile = a_member(session, settings=mail_settings)
        mailbox = make_mailbox(session, artist, address="synman@gmail.com")
        profile.mail_account_id = mailbox.id
        session.flush()

        html = client.get(f"/profiles/{profile.id}", headers=HTML).text

        assert "synman@gmail.com" in html
        assert f'hx-post="/profiles/{profile.id}/mail/disconnect"' in html

    def test_it_offers_the_artists_other_mailbox_to_share(self, session, mail_settings):
        client, artist, profile = a_member(session, settings=mail_settings)
        make_mailbox(session, artist, address="shared@gmail.com")

        html = client.get(f"/profiles/{profile.id}", headers=HTML).text

        assert "shared@gmail.com" in html
        assert f'hx-post="/profiles/{profile.id}/mail/attach"' in html

    def test_without_the_key_it_says_mail_is_not_configured(self, session):
        client, _artist, profile = a_member(session, settings=web_settings())

        html = client.get(f"/profiles/{profile.id}", headers=HTML).text

        assert "MAIL_TOKEN_KEY" in html
        assert f'href="/profiles/{profile.id}/mail/connect"' not in html

    def test_a_mailbox_that_needs_reconnecting_says_so(self, session, mail_settings):
        client, artist, profile = a_member(session, settings=mail_settings)
        mailbox = make_mailbox(session, artist, needs_reconnect=True, last_error="Google refused it")
        profile.mail_account_id = mailbox.id
        session.flush()

        html = client.get(f"/profiles/{profile.id}", headers=HTML).text

        assert "Reconnect" in html


class TestConnecting:
    def test_it_sends_the_viewer_to_google_with_the_gmail_scopes(self, session, mail_settings):
        client, _artist, profile = a_member(session, settings=mail_settings)

        response = client.get(f"/profiles/{profile.id}/mail/connect")

        assert response.status_code == 303
        url = urlsplit(response.headers["location"])
        query = parse_qs(url.query)
        assert url.netloc == "accounts.google.com"
        assert query["access_type"] == ["offline"]
        assert query["prompt"] == ["consent"]
        assert "gmail.send" in query["scope"][0] and "gmail.readonly" in query["scope"][0]
        assert query["redirect_uri"][0].endswith("/mail/callback")

    def test_the_callback_stores_the_mailbox_and_attaches_it(self, session, mail_settings):
        exchange = FakeGoogleMail()
        client, artist, profile = a_member(session, settings=mail_settings, exchange=exchange)
        state = _state_from(client.get(f"/profiles/{profile.id}/mail/connect"))

        response = client.get(f"/mail/callback?state={state}&code=auth-code")

        assert response.status_code == 303
        assert exchange.codes == ["auth-code"]
        mailbox = artist_mailboxes(session, artist.id)[0]
        assert mailbox.address == "synman@gmail.com"
        assert refresh_token_for(mailbox, mail_cipher(KEY)) == "1//0refresh"
        assert session.get(Profile, profile.id).mail_account_id == mailbox.id

    def test_a_callback_with_the_wrong_state_is_refused(self, session, mail_settings):
        exchange = FakeGoogleMail()
        client, _artist, profile = a_member(session, settings=mail_settings, exchange=exchange)
        client.get(f"/profiles/{profile.id}/mail/connect")

        response = client.get("/mail/callback?state=not-the-state&code=auth-code", headers=HTML)

        assert response.status_code == 404
        assert exchange.codes == []

    def test_a_callback_with_no_pending_connection_is_refused(self, session, mail_settings):
        client, _artist, _profile = a_member(session, settings=mail_settings)

        assert client.get("/mail/callback?state=x&code=y", headers=HTML).status_code == 404

    def test_google_refusing_the_exchange_is_explained_on_the_card(self, session, mail_settings):
        exchange = FakeGoogleMail()
        exchange.error = RuntimeError("invalid_grant")
        client, artist, profile = a_member(session, settings=mail_settings, exchange=exchange)
        state = _state_from(client.get(f"/profiles/{profile.id}/mail/connect"))

        response = client.get(f"/mail/callback?state={state}&code=auth-code", headers=HTML)

        assert response.status_code == 200
        assert "couldn't connect" in response.text.lower()
        assert artist_mailboxes(session, artist.id) == []


class TestSharingAndLettingGo:
    def test_attaching_one_of_the_artists_mailboxes(self, session, mail_settings):
        client, artist, profile = a_member(session, settings=mail_settings)
        mailbox = make_mailbox(session, artist, address="shared@gmail.com")

        response = _post(client, f"/profiles/{profile.id}/mail/attach", {"mail_account_id": str(mailbox.id)})

        assert response.status_code == 200
        assert session.get(Profile, profile.id).mail_account_id == mailbox.id

    def test_another_artists_mailbox_cannot_be_attached(self, session, mail_settings):
        client, _artist, profile = a_member(session, settings=mail_settings)
        theirs = make_mailbox(session, make_artist(session), address="theirs@gmail.com")

        response = _post(client, f"/profiles/{profile.id}/mail/attach", {"mail_account_id": str(theirs.id)})

        assert response.status_code == 404
        assert session.get(Profile, profile.id).mail_account_id is None

    def test_disconnecting_the_last_profile_clears_the_token(self, session, mail_settings):
        client, artist, profile = a_member(session, settings=mail_settings)
        mailbox = make_mailbox(session, artist)
        profile.mail_account_id = mailbox.id
        session.flush()

        response = _post(client, f"/profiles/{profile.id}/mail/disconnect", {})

        assert response.status_code == 200
        assert session.get(Profile, profile.id).mail_account_id is None
        assert session.get(MailAccount, mailbox.id).refresh_token_encrypted is None


def _post(client, path, data):
    return client.post(
        path, data=data, headers={"x-csrf-token": csrf_token(client), "hx-request": "true"}
    )


def _state_from(response) -> str:
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return query["state"][0]
```

Add `import re` only if you use it; if the finished file doesn't, drop that import.

- [ ] **Step 5: Write `web/mail.py`**

```python
"""The Pitch mailbox card, and the Google consent flow that fills it.

Connecting a Gmail mailbox is separate from signing in: it asks for the two Gmail scopes rather
than a name and an email, so it has its own consent round trip. The profile whose card started
it, and a random state, are kept in the session, and the callback refuses anything else.

The card and all three actions are ordinary profile routes: `require_profile` first, so another
artist's card answers 404 like everything else.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Protocol
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from core.access import NotVisible, is_storable_id, require_profile
from core.gmail import ACCESS_TOKEN_URL, Gmail
from core.mail_crypto import MailNotConfigured, mail_cipher
from core.mailboxes import MailboxProblem, artist_mailboxes, attach_mailbox, connect_mailbox, detach_mailbox
from core.models import MailAccount, Profile
from web.access import CurrentViewer
from web.db import get_db
from web.forms import form_id
from web.templating import templates

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
)
SESSION_MAIL_CONNECT = "mail_connect"
CONNECT_FAILED = "Google couldn't connect that mailbox. Try again, and pick the account you pitch from."


@dataclass(frozen=True)
class Connected:
    refresh_token: str
    address: str
    history_id: str


class MailExchange(Protocol):
    """Swaps Google's one-time code for a refresh token, the address and a starting history id."""

    def exchange(self, code: str) -> tuple[str, str, str]: ...


class GoogleMailExchange:
    def __init__(self, client_id: str, client_secret: str, redirect_uri_name: str = "mail_callback"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri_name = redirect_uri_name
        self._redirect_uri = ""

    def for_redirect(self, redirect_uri: str) -> "GoogleMailExchange":
        self._redirect_uri = redirect_uri
        return self

    def exchange(self, code: str) -> tuple[str, str, str]:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                ACCESS_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self._redirect_uri,
                },
            )
            if response.status_code != 200 or "refresh_token" not in response.json():
                raise MailboxProblem(CONNECT_FAILED)
            refresh_token = str(response.json()["refresh_token"])
            gmail = Gmail(
                client,
                client_id=self.client_id,
                client_secret=self.client_secret,
                refresh_token=refresh_token,
            )
            address, history_id = gmail.profile()
        return refresh_token, address, history_id


def google_mail_exchange(settings) -> MailExchange:
    return GoogleMailExchange(settings.google_client_id, settings.google_client_secret.get_secret_value())


@router.get("/profiles/{profile_id}/mail/connect")
def start_connect(request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    profile = require_profile(db, viewer, profile_id)
    _cipher_or_404(request)
    state = secrets.token_urlsafe(32)
    request.session[SESSION_MAIL_CONNECT] = {"state": state, "profile_id": profile.id}
    query = urlencode(
        {
            "client_id": request.app.state.settings.google_client_id,
            "redirect_uri": _redirect_uri(request),
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{query}", status_code=303)


@router.get("/mail/callback", name="mail_callback")
def finish_connect(request: Request, db: DbSession, viewer: CurrentViewer, state: str = "", code: str = "") -> Response:
    pending = request.session.get(SESSION_MAIL_CONNECT) or {}
    if not state or not secrets.compare_digest(state, str(pending.get("state", ""))):
        raise NotVisible("No mailbox connection is waiting")
    request.session.pop(SESSION_MAIL_CONNECT, None)
    profile = require_profile(db, viewer, int(pending.get("profile_id", 0)))
    cipher = _cipher_or_404(request)

    exchange = request.app.state.mail_exchange
    if isinstance(exchange, GoogleMailExchange):
        exchange = exchange.for_redirect(_redirect_uri(request))
    try:
        refresh_token, address, history_id = exchange.exchange(code)
    except Exception:
        return _card(request, db, profile, notice=CONNECT_FAILED)

    mailbox = connect_mailbox(
        db,
        artist_id=profile.artist_id,
        address=address,
        refresh_token=refresh_token,
        cipher=cipher,
        history_id=history_id,
        connected_by=viewer.email,
        now=datetime.now(UTC),
    )
    attach_mailbox(db, profile, mailbox)
    db.commit()
    return RedirectResponse(f"/profiles/{profile.id}", status_code=303)


@router.post("/profiles/{profile_id}/mail/attach")
def attach(
    request: Request,
    profile_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    mail_account_id: FormText = "",
) -> Response:
    profile = require_profile(db, viewer, profile_id)
    chosen = form_id(mail_account_id)
    mailbox = db.get(MailAccount, chosen) if is_storable_id(chosen) else None
    if mailbox is None or mailbox.artist_id != profile.artist_id:
        raise NotVisible(f"Mailbox {chosen} not found")
    try:
        attach_mailbox(db, profile, mailbox)
        db.commit()
    except MailboxProblem as problem:
        return _card(request, db, profile, notice=str(problem))
    return _card(request, db, profile, notice=f"Pitching from {mailbox.address}.")


@router.post("/profiles/{profile_id}/mail/disconnect")
def disconnect(request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    profile = require_profile(db, viewer, profile_id)
    detach_mailbox(db, profile, now=datetime.now(UTC))
    db.commit()
    return _card(request, db, profile, notice="This profile isn't pitching from a mailbox now.")


def card_context(request: Request, db: Session, profile: Profile, notice: str | None = None) -> dict:
    """What the Pitch mailbox card needs. Used here and by the profile page."""
    configured = cipher_or_none(request) is not None
    mailbox = db.get(MailAccount, profile.mail_account_id) if profile.mail_account_id else None
    others = [box for box in artist_mailboxes(db, profile.artist_id) if box.id != profile.mail_account_id]
    return {
        "profile": profile,
        "mail_configured": configured,
        "mailbox": mailbox,
        "other_mailboxes": others,
        "mail_notice": notice,
    }


def _card(request: Request, db: Session, profile: Profile, notice: str | None = None) -> Response:
    return templates.TemplateResponse(request, "mail/_card.html", card_context(request, db, profile, notice))


def cipher_or_none(request: Request):
    """This app's mail cipher, or None when MAIL_TOKEN_KEY isn't set. Also used by web/pitches.py."""
    key = getattr(request.app.state.settings, "mail_token_key", None)
    try:
        return mail_cipher(key.get_secret_value() if key is not None else None)
    except MailNotConfigured:
        return None


def _cipher_or_404(request: Request):
    cipher = cipher_or_none(request)
    if cipher is None:
        raise NotVisible("Mail isn't configured")
    return cipher


def _redirect_uri(request: Request) -> str:
    uri = request.url_for("mail_callback")
    if request.app.state.settings.secure_cookies:
        uri = uri.replace(scheme="https")  # behind Vercel's proxy the app sees http
    return str(uri)
```

- [ ] **Step 6: Write the card template**

Create `web/templates/mail/_card.html`:

```html
{# The mailbox this profile pitches from. Swapped in place by attach and disconnect. #}
<section class="card mail-card" id="mail-card" aria-labelledby="mail-card-heading">
  <h2 id="mail-card-heading" class="card__title">Pitch mailbox</h2>
  {% if mail_notice %}<p class="notice" role="status">{{ mail_notice }}</p>{% endif %}

  {% if not mail_configured %}
  <p class="field__hint">
    Email pitching isn't switched on: MAIL_TOKEN_KEY isn't set for this app.
  </p>
  {% elif mailbox %}
  <p class="mail-card__address">Pitching from <strong>{{ mailbox.address }}</strong></p>
  {% if mailbox.needs_reconnect %}
  <p class="notice notice--warning" role="status">
    Google refused this mailbox. Reconnect it to keep sending and reading replies.
  </p>
  <a class="button button--primary" href="/profiles/{{ profile.id }}/mail/connect">Reconnect Gmail</a>
  {% endif %}
  <div class="form__actions">
    <a class="button button--ghost button--small" href="/profiles/{{ profile.id }}/mail/connect">Change</a>
    <button class="button button--ghost button--small" type="button"
            hx-post="/profiles/{{ profile.id }}/mail/disconnect" hx-target="#mail-card" hx-swap="outerHTML"
            hx-disabled-elt="this" hx-confirm="Stop pitching {{ profile.name }} from {{ mailbox.address }}?">
      Disconnect
      <span class="htmx-indicator spinner" aria-hidden="true"></span>
    </button>
  </div>
  {% else %}
  <p class="field__hint">Connect the Gmail you pitch from, then write to curators without leaving Noble Hunter.</p>
  <div class="form__actions">
    <a class="button button--primary" href="/profiles/{{ profile.id }}/mail/connect">Connect Gmail</a>
  </div>
  {% if other_mailboxes %}
  <form class="form form--compact" hx-post="/profiles/{{ profile.id }}/mail/attach" hx-target="#mail-card"
        hx-swap="outerHTML" hx-disabled-elt="find button" novalidate>
    <div class="form__row">
      <label class="field__label" for="mail-account">Or use one this artist already connected</label>
      <select class="input" id="mail-account" name="mail_account_id">
        {% for box in other_mailboxes %}
        <option value="{{ box.id }}">{{ box.address }}</option>
        {% endfor %}
      </select>
      <button class="button button--small" type="submit">Use it <span class="htmx-indicator spinner" aria-hidden="true"></span></button>
    </div>
  </form>
  {% endif %}
  {% endif %}
</section>
```

In `web/templates/profiles/detail.html`, include the card in the same column as the settings form: `{% include "mail/_card.html" %}`. Read the file first and put it after the settings card, before any status panel.

In `web/profiles.py` `profile_page`, add the card's context: import `card_context` from `web.mail` and spread `**card_context(request, db, profile)` into the context dict.

Append to `web/static/css/app.css`, after the People section:

```css
/* ---- Pitch mailbox ------------------------------------------------------------------- */

.mail-card__address {
  margin: 0 0 var(--space-3);
  overflow-wrap: anywhere;
}
```

- [ ] **Step 7: Add the mailbox to the access world**

In `tests/access_world.py`:
- import `make_mailbox`;
- inside `build_world`, after the profile is created, give the other artist a mailbox and attach it, so the walk's attach and disconnect routes have something real to change:

```python
    mailbox = make_mailbox(session, theirs, address="theirs@gmail.com")
    profile.mail_account_id = mailbox.id
    session.flush()
```

- add the mailbox's id to `ids` as `"mail_account_id": mailbox.id`;
- in `their_data`, add two keys so a leaked attach or disconnect shows up:

```python
        "profile_mailbox": profile.mail_account_id,
        "mailboxes": sorted(
            (box.address, box.refresh_token_encrypted is not None, box.needs_reconnect)
            for box in session.scalars(select(MailAccount).where(MailAccount.artist_id == world.artist_id))
        ),
```

(import `MailAccount` from `core.models`), and in `seed_route_state`, make sure the attach route has something to move to:

```python
    elif (method, path) == ("POST", "/profiles/{profile_id}/mail/attach"):
        make_mailbox(session, session.get(Artist, world.artist_id), address="spare@gmail.com")
```

In `tests/route_walk.py`, the attach form must carry a real mailbox id, so change the FORMS entry added in Step 1 to be filled like the artist id is:

```python
    "/profiles/{profile_id}/mail/attach": {"mail_account_id": "{mail_account_id}"},
```

and in `call`, after the form is copied, substitute any `{...}` placeholder in a form value from `ids`:

```python
    form = {key: str(value).format(**ids) if "{" in str(value) else value for key, value in form.items()}
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/test_web_mail.py tests/test_web_access.py tests/test_web_access_controls.py tests/test_web_access_sweeps.py -q`

Expected: all pass. The admin control for attach and disconnect must change the snapshot; if `disconnect` doesn't, check that `their_data` reads `profile.mail_account_id` after `session.expire_all()`.

- [ ] **Step 9: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add web/mail.py web/app.py web/profiles.py web/templates/mail web/templates/profiles/detail.html \
  web/static/css/app.css tests
git commit -m "feat: connect a Gmail mailbox to a profile

Connecting asks Google for the two Gmail scopes in its own consent round trip, keyed to the
profile that started it. Mailboxes belong to the artist, so a second profile can share one.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Stage 3: Writing and sending a pitch

### Task 7: Claude writes the pitch

**Files:**
- Create: `core/pitch_writer.py`, `tests/test_pitch_writer.py`

This is the third Claude caller in the codebase, after `core/suggestions.py` and `pipeline/fit_judge.py`. Follow their shape exactly: one structured-output call, thinking off, a `Protocol` so tests never touch the network, and a plain fallback when Claude can't help.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pitch_writer.py`:

```python
"""Claude drafting a pitch email, and the plain draft used when it can't."""

import json
from decimal import Decimal

import anthropic
import httpx
import pytest

from core.pitch_writer import (
    ClaudePitchWriter,
    PitchRequest,
    PitchWriterError,
    template_pitch,
)

REQUEST = PitchRequest(
    artist_name="Synman",
    profile_name="IDM Playlists",
    curator_name="Nina",
    playlist_name="Broken Machines",
    playlist_url="https://open.spotify.com/playlist/0000000000000000000001",
    brief="Nina runs Broken Machines (4,120 followers, updated 3 days ago). It already features Autechre.",
    angle="Lead with Kelvin and how it sits next to Autechre.",
    reference_artists=("Autechre", "Boards of Canada"),
    tracks=(("Kelvin", "Broken drums under a warm pad"),),
    sender_name="Jarred",
    instruction="Keep it short and mention the Autechre track.",
    previous_body="",
)


class FakeMessages:
    def __init__(self, text: str = "", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return _response(self.text)


class FakeClient:
    def __init__(self, messages: FakeMessages):
        self.messages = messages


def _response(text: str):
    class Block:
        type = "text"

    block = Block()
    block.text = text

    class Usage:
        input_tokens = 1_000
        output_tokens = 500
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0

    class Response:
        content = [block]
        usage = Usage()
        stop_reason = "end_turn"

    return Response()


def api_error() -> anthropic.APIError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=request)


def writer_with(text: str = "", error: Exception | None = None) -> tuple[ClaudePitchWriter, FakeMessages]:
    messages = FakeMessages(text, error)
    return ClaudePitchWriter(FakeClient(messages)), messages


class TestAsking:
    def test_it_returns_the_subject_and_body_claude_wrote(self):
        answer = json.dumps({"subject": "Kelvin for Broken Machines", "body": "Hi Nina,\n\nKelvin..."})
        writer, _messages = writer_with(answer)

        pitch = writer.write(REQUEST)

        assert pitch.subject == "Kelvin for Broken Machines"
        assert pitch.body.startswith("Hi Nina,")
        assert pitch.spend_usd > Decimal(0)

    def test_the_call_is_small_and_structured(self):
        writer, messages = writer_with(json.dumps({"subject": "S", "body": "B"}))

        writer.write(REQUEST)

        sent = messages.calls[0]
        assert sent["thinking"] == {"type": "disabled"}
        assert sent["output_config"]["format"]["type"] == "json_schema"
        assert sent["max_tokens"] <= 2_000

    def test_claude_sees_the_brief_the_angle_and_the_instruction(self):
        writer, messages = writer_with(json.dumps({"subject": "S", "body": "B"}))

        writer.write(REQUEST)

        question = messages.calls[0]["messages"][0]["content"]
        assert "Broken Machines" in question
        assert "Autechre" in question
        assert "Keep it short" in question
        assert "Kelvin" in question

    def test_revising_shows_claude_the_draft_it_is_changing(self):
        writer, messages = writer_with(json.dumps({"subject": "S", "body": "B"}))

        writer.write(
            PitchRequest(**{**REQUEST.__dict__, "previous_body": "Hi Nina, here's my track."})
        )

        question = messages.calls[0]["messages"][0]["content"]
        assert "here's my track" in question
        assert "current draft" in question.lower()

    def test_a_refused_call_raises_something_showable(self):
        writer, _messages = writer_with(error=api_error())

        with pytest.raises(PitchWriterError, match="Claude didn't answer"):
            writer.write(REQUEST)

    def test_an_unreadable_answer_raises_something_showable(self):
        writer, _messages = writer_with("not json")

        with pytest.raises(PitchWriterError, match="couldn't be read"):
            writer.write(REQUEST)

    def test_a_blank_body_is_refused_rather_than_sent(self):
        writer, _messages = writer_with(json.dumps({"subject": "S", "body": "   "}))

        with pytest.raises(PitchWriterError, match="couldn't be read"):
            writer.write(REQUEST)

    def test_a_long_subject_is_trimmed(self):
        writer, _messages = writer_with(json.dumps({"subject": "x" * 500, "body": "B"}))

        assert len(writer.write(REQUEST).subject) <= 200


class TestThePlainDraft:
    def test_it_names_the_playlist_the_curator_and_a_track(self):
        pitch = template_pitch(REQUEST)

        assert "Broken Machines" in pitch.subject
        assert "Nina" in pitch.body
        assert "Kelvin" in pitch.body
        assert "Jarred" in pitch.body
        assert pitch.spend_usd == Decimal(0)

    def test_it_works_with_nothing_but_the_playlist(self):
        bare = PitchRequest(
            artist_name="Synman",
            profile_name="IDM Playlists",
            curator_name="",
            playlist_name="Broken Machines",
            playlist_url="https://open.spotify.com/playlist/0000000000000000000001",
            brief="",
            angle=None,
            reference_artists=(),
            tracks=(),
            sender_name="",
            instruction="",
            previous_body="",
        )

        pitch = template_pitch(bare)

        assert "Broken Machines" in pitch.body
        assert "Hi there" in pitch.body
```

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run pytest tests/test_pitch_writer.py -q`

Expected: `ModuleNotFoundError: No module named 'core.pitch_writer'`.

- [ ] **Step 3: Write `core/pitch_writer.py`**

```python
"""Claude drafts the pitch, and a plain draft stands in when it can't.

The pitch is Jarred's letter, not Claude's: Claude sees the same brief the digest shows, the
angle the pipeline suggested, and whatever Jarred typed into the box ("shorter", "mention the
Autechre track"), and answers with a subject and a body he then edits before anything is sent.
Nothing here sends: `core.pitches` does that, only when he presses Send.

One structured call, thinking off, bounded output -- the same shape as `core.suggestions` and
`pipeline.fit_judge`. A refusal or an unreadable answer raises `PitchWriterError`, whose text is
safe to show, and the page keeps the draft that was already in the box.
"""

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import anthropic

from pipeline.llm_costs import cost_of

logger = logging.getLogger("noble_hunter.pitch_writer")

PITCH_MODEL = "claude-opus-5"
MAX_OUTPUT_TOKENS = 1_500
REQUEST_TIMEOUT_SECONDS = 60
MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 6_000
MAX_INSTRUCTION_LENGTH = 500

SYSTEM_PROMPT = (
    "You help an independent musician write a short email to a Spotify playlist curator, asking them "
    "to listen to one track.\n\n"
    "Write as the musician, in plain English, the way one person writes to another. Be specific about "
    "this playlist: why this track belongs on it, using what the brief says about the playlist and the "
    "artists already on it. No flattery, no hype, no marketing voice, no bullet points, no attachments. "
    "Ask once, politely, and make it easy to say no. Six sentences at most, plus a sign-off.\n\n"
    "Give a subject line under 80 characters that names the track and the playlist. Put the Spotify link "
    "on its own line in the body. Never invent facts about the curator, the playlist or the music."
)

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class PitchRequest:
    artist_name: str
    profile_name: str
    curator_name: str
    playlist_name: str
    playlist_url: str
    brief: str
    angle: str | None
    reference_artists: tuple[str, ...]
    tracks: tuple[tuple[str, str], ...]  # (title, one line on how it sounds)
    sender_name: str
    instruction: str
    previous_body: str


@dataclass(frozen=True)
class Pitch:
    subject: str
    body: str
    spend_usd: Decimal = Decimal(0)


class PitchWriter(Protocol):
    def write(self, request: PitchRequest) -> Pitch: ...


class PitchWriterError(RuntimeError):
    """Claude couldn't draft it this time. The message is safe to show."""


class ClaudePitchWriter:
    def __init__(self, client, model: str = PITCH_MODEL):
        self.client = client
        self.model = model

    @classmethod
    def from_api_key(cls, api_key: str) -> "ClaudePitchWriter":
        return cls(anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1))

    def write(self, request: PitchRequest) -> Pitch:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                thinking={"type": "disabled"},
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
                },
                messages=[{"role": "user", "content": _question(request)}],
            )
        except anthropic.APIError as error:
            logger.warning("Draft a pitch failed: %s", type(error).__name__)
            raise PitchWriterError("Claude didn't answer. Try again in a moment.") from error

        subject, body = _parse(response)
        return Pitch(subject=subject, body=body, spend_usd=cost_of(self.model, response.usage))


def template_pitch(request: PitchRequest) -> Pitch:
    """A plain draft from the facts alone, for when Claude can't write one."""
    greeting = f"Hi {request.curator_name}," if request.curator_name else "Hi there,"
    track_title, track_sound = request.tracks[0] if request.tracks else ("", "")
    track = f"“{track_title}”" if track_title else "a new track"
    lines = [
        greeting,
        "",
        f"I'm {request.sender_name or request.artist_name}, and I make music as {request.artist_name}. "
        f"I came across “{request.playlist_name}” and thought {track} might suit it.",
    ]
    if track_sound:
        lines.append(track_sound.rstrip(".") + ".")
    if request.reference_artists:
        lines.append(f"It sits close to {', '.join(request.reference_artists)}.")
    lines += [
        "",
        request.playlist_url,
        "",
        "No problem at all if it's not right for the playlist -- thanks for listening either way.",
        "",
        request.sender_name or request.artist_name,
    ]
    subject = f"{track_title or request.artist_name} for {request.playlist_name}"
    return Pitch(subject=subject[:MAX_SUBJECT_LENGTH], body="\n".join(lines))


def _question(request: PitchRequest) -> str:
    tracks = "; ".join(f"{title} ({sound})" if sound else title for title, sound in request.tracks)
    parts = [
        f"The musician: {request.artist_name} (profile: {request.profile_name})",
        f"They sign off as: {request.sender_name or request.artist_name}",
        f"Their tracks: {tracks or 'none listed'}",
        f"Artists their music sits next to: {', '.join(request.reference_artists) or 'none listed'}",
        "",
        f"The curator: {request.curator_name or 'name unknown'}",
        f"The playlist: {request.playlist_name}",
        f"Spotify link: {request.playlist_url}",
        f"What we know about it: {request.brief or 'nothing beyond the name'}",
        f"Suggested angle: {request.angle or 'none'}",
    ]
    if request.previous_body.strip():
        parts += [
            "",
            "This is the current draft. Rewrite it, keeping anything the musician clearly wants kept:",
            request.previous_body.strip()[:MAX_BODY_LENGTH],
        ]
    instruction = " ".join(request.instruction.split())[:MAX_INSTRUCTION_LENGTH]
    parts += ["", f"What the musician asked for: {instruction or 'write the first draft'}"]
    return "\n".join(parts)


def _parse(response) -> tuple[str, str]:
    text = next((block.text for block in response.content if block.type == "text"), None)
    try:
        answer = json.loads(text or "")
        subject = " ".join(str(answer["subject"]).split())
        body = str(answer["body"]).strip()
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        logger.warning("The pitch draft was unreadable (stop_reason=%s)", getattr(response, "stop_reason", None))
        raise PitchWriterError("Claude's draft couldn't be read. Try again.") from error
    if not body or not subject:
        raise PitchWriterError("Claude's draft couldn't be read. Try again.")
    return subject[:MAX_SUBJECT_LENGTH], body[:MAX_BODY_LENGTH]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_pitch_writer.py -q`

Expected: 10 passed.

- [ ] **Step 5: Add the live test, skipped by default**

The repo already has this pattern in `tests/test_suggestions_live.py` and `tests/test_spotify_live.py`: one test that talks to the real API, skipped unless a variable is set, so the prompt can be checked by hand without ever running in CI. Read `tests/test_suggestions_live.py` and follow it exactly. Create `tests/test_pitch_writer_live.py`:

```python
"""One real call to Claude, to read what it actually writes. Skipped unless asked for.

Run it with: NOBLE_HUNTER_LIVE=1 uv run pytest tests/test_pitch_writer_live.py -q -s
"""

import os

import pytest

from core.pitch_writer import ClaudePitchWriter
from tests.test_pitch_writer import REQUEST

live = pytest.mark.skipif(
    not os.environ.get("NOBLE_HUNTER_LIVE"), reason="set NOBLE_HUNTER_LIVE=1 to call Claude for real"
)


@live
def test_claude_writes_something_a_person_would_send():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY isn't set")

    pitch = ClaudePitchWriter.from_api_key(key).write(REQUEST)

    print(f"\nSubject: {pitch.subject}\n\n{pitch.body}\n\n(${pitch.spend_usd:.4f})")
    assert "Broken Machines" in pitch.body
    assert len(pitch.body.split()) < 200
```

Check the skip marker matches whatever the existing live tests use — if they key off a different variable, use theirs rather than inventing `NOBLE_HUNTER_LIVE`.

- [ ] **Step 6: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add core/pitch_writer.py tests/test_pitch_writer.py tests/test_pitch_writer_live.py
git commit -m "feat: Claude drafts the pitch email

One structured call, the same shape as Ask Claude and the fit judge. The draft is Jarred's
letter to edit, never something that sends itself; a refusal keeps whatever is in the box.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Drafting, sending and the thread

**Files:**
- Create: `core/pitches.py`, `tests/test_pitches.py`

Everything that happens to a pitch, away from the web: which address to write to, saving a draft, sending it through Gmail, and reading a thread back. `send_pitch` records the `pitched` verdict through `core.exclusion.record_verdict`, so sending an email has exactly the same effect on the 90-day rule as pressing Pitched by hand.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pitches.py`:

```python
"""Drafting a pitch, sending it, and reading the thread back."""

import base64
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from core.gmail import GmailError
from core.models import EmailMessage, MailDirection, OutreachStatus
from core.pitches import (
    PitchProblem,
    mark_thread_read,
    pitch_address,
    save_draft,
    send_pitch,
    thread_messages,
)
from tests.factories import (
    make_contact,
    make_curator,
    make_mailbox,
    make_outreach,
    make_playlist,
    make_profile,
)

NIGHT = date(2026, 9, 16)
NOW = datetime(2026, 9, 16, 9, 30, tzinfo=UTC)


class FakeGmail:
    """Stands in for `core.gmail.Gmail`: records what was sent, or raises.

    `Gmail.send` takes a base64url string and a keyword-only thread id, so this does too.
    """

    def __init__(self, error: GmailError | None = None):
        self.sent: list[tuple[str, str | None]] = []
        self.error = error

    def send(self, raw: str, *, thread_id: str | None = None) -> tuple[str, str]:
        if self.error:
            raise self.error
        self.sent.append((raw, thread_id))
        return f"msg{len(self.sent)}", thread_id or "thread1"

    def decoded(self, index: int = 0) -> str:
        return base64.urlsafe_b64decode(self.sent[index][0].encode()).decode()


def an_outreach(session, *, email: str | None = "nina@broken-machines.com"):
    curator = make_curator(session, display_name="Nina")
    if email:
        make_contact(session, curator, "email", email)
    profile = make_profile(session, "IDM Playlists")
    playlist = make_playlist(session, curator=curator, name="Broken Machines")
    return make_outreach(session, curator, profile, NIGHT, playlist=playlist)


def a_sendable(session):
    outreach = an_outreach(session)
    mailbox = make_mailbox(session, outreach.profile.artist, address="synman@gmail.com")
    outreach.profile.mail_account_id = mailbox.id
    session.flush()
    return outreach, mailbox


class TestWhoWeWriteTo:
    def test_it_is_the_curators_best_email(self, session):
        outreach = an_outreach(session)

        assert pitch_address(session, outreach) == "nina@broken-machines.com"

    def test_a_curator_with_no_email_cannot_be_pitched_by_email(self, session):
        outreach = an_outreach(session, email=None)

        with pytest.raises(PitchProblem, match="no email address"):
            pitch_address(session, outreach)

    def test_an_instagram_only_curator_cannot_be_pitched_by_email(self, session):
        curator = make_curator(session)
        make_contact(session, curator, "instagram", "brokenmachines")
        outreach = make_outreach(session, curator, make_profile(session), NIGHT)

        with pytest.raises(PitchProblem, match="no email address"):
            pitch_address(session, outreach)


class TestDrafts:
    def test_a_draft_is_kept_on_the_entry(self, session):
        outreach = an_outreach(session)

        save_draft(session, outreach, subject="Kelvin for Broken Machines", body="Hi Nina", now=NOW)

        assert outreach.draft_subject == "Kelvin for Broken Machines"
        assert outreach.draft_body == "Hi Nina"
        assert outreach.draft_updated_at == NOW

    def test_saving_again_replaces_it(self, session):
        outreach = an_outreach(session)
        save_draft(session, outreach, subject="First", body="One", now=NOW)

        save_draft(session, outreach, subject="Second", body="Two", now=NOW)

        assert (outreach.draft_subject, outreach.draft_body) == ("Second", "Two")

    def test_a_blank_body_is_refused(self, session):
        outreach = an_outreach(session)

        with pytest.raises(PitchProblem, match="Write something"):
            save_draft(session, outreach, subject="Kelvin", body="   ", now=NOW)

    def test_a_blank_subject_is_refused(self, session):
        outreach = an_outreach(session)

        with pytest.raises(PitchProblem, match="subject"):
            save_draft(session, outreach, subject="  ", body="Hi Nina", now=NOW)


def send(session, outreach, mailbox, gmail, **overrides):
    fields = {"subject": "Kelvin", "body": "Hi Nina", "now": NOW, **overrides}
    return send_pitch(session, outreach, gmail=gmail, mailbox=mailbox, **fields)


class TestSending:
    def test_it_sends_the_draft_and_records_the_message(self, session):
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail()

        message = send(
            session, outreach, mailbox, gmail, subject="Kelvin for Broken Machines", body="Hi Nina,\n\nKelvin..."
        )

        assert gmail.sent[0][1] is None  # a new thread
        assert "nina@broken-machines.com" in gmail.decoded()
        assert "synman@gmail.com" in gmail.decoded()
        assert message.direction == MailDirection.OUT
        assert message.gmail_message_id == "msg1"
        assert message.from_address == "synman@gmail.com"
        assert message.to_address == "nina@broken-machines.com"
        assert message.subject == "Kelvin for Broken Machines"
        assert message.body_text.startswith("Hi Nina,")
        assert message.sent_at == NOW

    def test_sending_marks_the_entry_pitched(self, session):
        outreach, mailbox = a_sendable(session)

        send(session, outreach, mailbox, FakeGmail())

        assert outreach.status == OutreachStatus.PITCHED
        assert outreach.pitched_at == NOW
        assert outreach.gmail_thread_id == "thread1"
        assert outreach.mail_account_id == mailbox.id

    def test_the_draft_is_cleared_once_it_has_been_sent(self, session):
        outreach, mailbox = a_sendable(session)
        save_draft(session, outreach, subject="Kelvin", body="Hi Nina", now=NOW)

        send(session, outreach, mailbox, FakeGmail())

        assert outreach.draft_subject is None
        assert outreach.draft_body is None

    def test_a_reply_of_ours_stays_in_the_same_thread(self, session):
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail()
        send(session, outreach, mailbox, gmail)

        send(session, outreach, mailbox, gmail, subject="Re: Kelvin", body="Thanks Nina")

        assert gmail.sent[1][1] == "thread1"

    def test_it_replies_to_the_curators_last_message(self, session):
        outreach, mailbox = a_sendable(session)
        send(session, outreach, mailbox, FakeGmail())
        session.add(
            EmailMessage(
                outreach_id=outreach.id,
                mail_account_id=mailbox.id,
                direction=MailDirection.IN,
                gmail_message_id="reply1",
                gmail_thread_id="thread1",
                rfc822_message_id="<nina-1@mail.gmail.com>",
                from_address="nina@broken-machines.com",
                to_address="synman@gmail.com",
                subject="Re: Kelvin",
                body_text="Send it over.",
                sent_at=datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
            )
        )
        session.flush()
        gmail = FakeGmail()

        send(session, outreach, mailbox, gmail, subject="Re: Kelvin", body="Here it is")

        assert "In-Reply-To: <nina-1@mail.gmail.com>" in gmail.decoded()

    def test_gmail_refusing_leaves_the_entry_untouched(self, session):
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail(error=GmailError("auth", "Google refused the saved Gmail access"))

        with pytest.raises(GmailError):
            send(session, outreach, mailbox, gmail)

        assert outreach.status == OutreachStatus.NEW
        assert outreach.gmail_thread_id is None
        assert session.scalar(select(func.count()).select_from(EmailMessage)) == 0

    def test_sending_needs_a_mailbox_and_an_address(self, session):
        outreach = an_outreach(session, email=None)
        mailbox = make_mailbox(session, outreach.profile.artist)

        with pytest.raises(PitchProblem, match="no email address"):
            send(session, outreach, mailbox, FakeGmail())

    def test_a_curator_you_ruled_out_is_never_pitched(self, session):
        outreach, mailbox = a_sendable(session)
        outreach.curator.excluded_at = NOW
        outreach.curator.exclusion_reason = OutreachStatus.BAD_FIT
        session.flush()
        gmail = FakeGmail()

        with pytest.raises(PitchProblem, match="ruled this curator out"):
            send(session, outreach, mailbox, gmail)

        assert gmail.sent == []

    def test_an_entry_already_marked_bad_fit_is_not_downgraded(self, session):
        outreach, mailbox = a_sendable(session)
        outreach.status = OutreachStatus.BAD_FIT
        session.flush()

        with pytest.raises(PitchProblem):
            send(session, outreach, mailbox, FakeGmail())

        assert outreach.status == OutreachStatus.BAD_FIT

    def test_the_same_send_key_twice_sends_once(self, session):
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail()
        send(session, outreach, mailbox, gmail, send_key="abc123")

        with pytest.raises(PitchProblem, match="already been sent"):
            send(session, outreach, mailbox, gmail, send_key="abc123")

        assert len(gmail.sent) == 1


class TestTheThread:
    def test_it_reads_back_in_the_order_it_happened(self, session):
        outreach, mailbox = a_sendable(session)
        send(session, outreach, mailbox, FakeGmail())
        session.add(
            EmailMessage(
                outreach_id=outreach.id,
                mail_account_id=mailbox.id,
                direction=MailDirection.IN,
                gmail_message_id="reply1",
                gmail_thread_id="thread1",
                from_address="nina@broken-machines.com",
                to_address="synman@gmail.com",
                subject="Re: Kelvin",
                body_text="Send it over.",
                sent_at=datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
            )
        )
        session.flush()

        messages = thread_messages(session, outreach)

        assert [(m.direction, m.body_text) for m in messages] == [
            (MailDirection.OUT, "Hi Nina"),
            (MailDirection.IN, "Send it over."),
        ]

    def test_an_entry_with_no_mail_has_an_empty_thread(self, session):
        assert thread_messages(session, an_outreach(session)) == []

    def test_opening_a_thread_marks_their_messages_read(self, session):
        outreach, mailbox = a_sendable(session)
        send(session, outreach, mailbox, FakeGmail())
        session.add(
            EmailMessage(
                outreach_id=outreach.id,
                mail_account_id=mailbox.id,
                direction=MailDirection.IN,
                gmail_message_id="reply1",
                gmail_thread_id="thread1",
                from_address="nina@broken-machines.com",
                to_address="synman@gmail.com",
                subject="Re: Kelvin",
                body_text="Send it over.",
                sent_at=NOW,
            )
        )
        session.flush()

        marked = mark_thread_read(session, outreach, now=NOW)

        assert marked == 1
        assert mark_thread_read(session, outreach, now=NOW) == 0  # nothing left to mark
        ours = [m for m in thread_messages(session, outreach) if m.direction == MailDirection.OUT]
        assert all(message.read_at is None for message in ours)  # our own were never unread
```

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run pytest tests/test_pitches.py -q`

Expected: `ModuleNotFoundError: No module named 'core.pitches'`.

- [ ] **Step 3: Write `core/pitches.py`**

```python
"""A pitch: who it goes to, the draft, sending it, and the thread it becomes.

Email pitching sits on top of the digest rather than beside it. Sending records the `pitched`
verdict through `core.exclusion.record_verdict`, so an emailed pitch and a hand-recorded one
have exactly the same effect on the 90-day rule -- there is one way a curator gets used up, not
two. The entry keeps the Gmail thread id, so a reply found later in the mailbox lands back on
the entry it belongs to, and our own follow-ups stay in the same thread.

Nothing is written until Gmail has accepted the message. If the send fails the entry is
untouched, and Jarred still has his draft.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.contact_routes import best_contact
from core.exclusion import PERMANENT_VERDICTS, record_verdict
from core.mime import build_message
from core.models import Contact, EmailMessage, MailAccount, MailDirection, Outreach, OutreachStatus, RouteType

MAX_SUBJECT_LENGTH = 200
NO_EMAIL = "This curator has no email address, so there's nothing to write to."


class PitchProblem(ValueError):
    """Something a person can fix: no address, an empty draft."""


def pitch_address(session: Session, outreach: Outreach) -> str:
    """The curator's best email address, or raise.

    `best_contact` picks by the same rule the digest page shows, but it can pick an Instagram
    handle; email pitching needs an email, so the curator's best *email* is used instead.
    """
    best = best_contact(session, outreach.curator_id)
    if best is not None and best.route_type == RouteType.EMAIL:
        return best.value
    fallback = session.scalar(
        select(Contact)
        .where(Contact.curator_id == outreach.curator_id, Contact.route_type == RouteType.EMAIL)
        .order_by(Contact.confidence, Contact.id)
        .limit(1)
    )
    if fallback is None:
        raise PitchProblem(NO_EMAIL)
    return fallback.value


def save_draft(session: Session, outreach: Outreach, *, subject: str, body: str, now: datetime) -> None:
    """Keep what's in the box, so a closed tab doesn't lose it."""
    outreach.draft_subject, outreach.draft_body = _clean(subject, body)
    outreach.draft_updated_at = now
    session.flush()


def send_pitch(
    session: Session,
    outreach: Outreach,
    *,
    gmail,
    mailbox: MailAccount,
    subject: str,
    body: str,
    now: datetime,
    send_key: str | None = None,
) -> EmailMessage:
    """Send the pitch from `mailbox` and record it. Raises before writing anything if it can't.

    `send_key` comes from the panel that posted the form: the same key twice is a double-click
    or a resubmitted page, and sends nothing the second time.
    """
    clean_subject, clean_body = _clean(subject, body)
    if send_key and _already_sent(session, send_key):
        raise PitchProblem("That pitch has already been sent.")
    _refuse_if_ruled_out(outreach)
    to_address = pitch_address(session, outreach)
    raw = build_message(
        from_address=mailbox.address,
        to_address=to_address,
        subject=clean_subject,
        body=clean_body,
        in_reply_to=_last_incoming_id(session, outreach),
    )
    message_id, thread_id = gmail.send(raw, thread_id=outreach.gmail_thread_id)

    record = EmailMessage(
        outreach_id=outreach.id,
        mail_account_id=mailbox.id,
        direction=MailDirection.OUT,
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        from_address=mailbox.address,
        to_address=to_address,
        subject=clean_subject,
        body_text=clean_body,
        sent_at=now,
        send_key=send_key,
    )
    session.add(record)
    outreach.gmail_thread_id = thread_id
    outreach.mail_account_id = mailbox.id
    outreach.draft_subject = None
    outreach.draft_body = None
    outreach.draft_updated_at = None
    if outreach.status != OutreachStatus.PITCHED:
        record_verdict(session, outreach.id, OutreachStatus.PITCHED, now)
    session.flush()
    return record


def thread_messages(session: Session, outreach: Outreach) -> list[EmailMessage]:
    """Everything sent and received on this entry, oldest first."""
    return list(
        session.scalars(
            select(EmailMessage)
            .where(EmailMessage.outreach_id == outreach.id)
            .order_by(EmailMessage.sent_at, EmailMessage.id)
        )
    )


def mark_thread_read(session: Session, outreach: Outreach, *, now: datetime) -> int:
    """Their unread messages on this entry have now been seen. Returns how many were marked.

    Only incoming messages are ever unread -- our own were read as we wrote them -- and `read_at`
    feeds nothing but the Inbox's unread count. Whether a conversation is *open* is worked out
    from the messages themselves, so a missed call here can never strand an entry.
    """
    unread = list(
        session.scalars(
            select(EmailMessage).where(
                EmailMessage.outreach_id == outreach.id,
                EmailMessage.direction == MailDirection.IN,
                EmailMessage.read_at.is_(None),
            )
        )
    )
    for message in unread:
        message.read_at = now
    session.flush()
    return len(unread)


def _refuse_if_ruled_out(outreach: Outreach) -> None:
    """A curator marked bad-fit or dead is never written to, and their entry is never downgraded.

    The verdict buttons and this module can both change an entry, so the rule lives here rather
    than in the route: whatever order the two happen in, a ruled-out curator stays ruled out.
    """
    if outreach.curator.excluded_at is not None or outreach.status in PERMANENT_VERDICTS:
        raise PitchProblem("You ruled this curator out, so nothing was sent.")


def _clean(subject: str, body: str) -> tuple[str, str]:
    clean_subject = " ".join(str(subject or "").split())[:MAX_SUBJECT_LENGTH]
    clean_body = str(body or "").strip()
    if not clean_subject:
        raise PitchProblem("Give the email a subject.")
    if not clean_body:
        raise PitchProblem("Write something before saving or sending.")
    return clean_subject, clean_body


def _already_sent(session: Session, send_key: str) -> bool:
    return session.scalar(select(EmailMessage.id).where(EmailMessage.send_key == send_key)) is not None


def _last_incoming_id(session: Session, outreach: Outreach) -> str | None:
    """The curator's last Message-ID, so our reply lands in their thread, not a new one."""
    return session.scalar(
        select(EmailMessage.rfc822_message_id)
        .where(
            EmailMessage.outreach_id == outreach.id,
            EmailMessage.direction == MailDirection.IN,
            EmailMessage.rfc822_message_id.is_not(None),
        )
        .order_by(EmailMessage.sent_at.desc(), EmailMessage.id.desc())
        .limit(1)
    )
```

Note for the builder: the first pitch has nothing to reply to, so `in_reply_to` is `None` and Gmail's `threadId` alone keeps our own follow-ups together. `rfc822_message_id` is filled in by the mail sync (Task 10) as messages come back from Gmail, which is what makes a *reply* thread correctly in the curator's client.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_pitches.py -q`

Expected: 20 passed.

- [ ] **Step 5: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add core/pitches.py tests/test_pitches.py
git commit -m "feat: draft, send and thread a pitch

Sending records the pitched verdict through record_verdict, so an emailed pitch uses a
curator up exactly like a hand-recorded one. Nothing is written until Gmail accepts it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: The compose panel on a digest entry

**Files:**
- Create: `web/pitches.py`, `web/templates/pitch/_panel.html`, `tests/test_web_pitches.py`
- Modify: `web/app.py`, `web/templates/digest/_entry.html`, `web/static/css/app.css`, `tests/route_walk.py`, `tests/access_world.py`, `tests/web_helpers.py`

The panel lives inside the digest entry, behind a disclosure, and is fetched the first time it's opened — so the digest page itself runs no extra queries for entries nobody writes to. Four routes: read the panel, ask Claude for a draft, save the draft, send it.

Sending changes the entry's status to Pitched, and the entry card is already on screen, so the send response swaps the panel *and* out-of-band-swaps the status tag. That's why Step 4 gives the tag an id and renders it even when there's nothing to show.

- [ ] **Step 1: Add the routes to the walk first**

In `tests/route_walk.py`, add to `ROUTES`, after the verdict route:

```python
    ("GET", "/outreach/{outreach_id}/pitch"): NOT_FOUND,
    ("POST", "/outreach/{outreach_id}/pitch/write"): NOT_FOUND,
    ("POST", "/outreach/{outreach_id}/pitch/save"): NOT_FOUND,
    ("POST", "/outreach/{outreach_id}/pitch/send"): NOT_FOUND,
```

and to `FORMS`:

```python
    "/outreach/{outreach_id}/pitch/write": {"instruction": "shorter", "subject": "", "body": ""},
    "/outreach/{outreach_id}/pitch/save": {"subject": "Stolen", "body": "Stolen draft"},
    "/outreach/{outreach_id}/pitch/send": {"subject": "Stolen", "body": "Stolen draft", "send_key": "walk"},
```

Run: `uv run pytest tests/test_web_access.py -q`

Expected: FAIL — `test_every_route_is_covered` lists four routes the app doesn't have.

- [ ] **Step 2: Give the test helpers a fake Gmail and a fake pitch writer**

In `tests/web_helpers.py`, add below `FakeSuggester`:

```python
class FakePitchWriter:
    """Stands in for Claude drafting a pitch: returns a canned draft, or raises."""

    def __init__(self, subject: str = "A draft subject", body: str = "A draft body", error=None):
        self.subject = subject
        self.body = body
        self.error = error
        self.calls: list = []

    def write(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        from core.pitch_writer import Pitch

        return Pitch(subject=self.subject, body=self.body)


class FakeGmailSender:
    """Stands in for one mailbox's Gmail access, and remembers every message it was given."""

    def __init__(self, error=None):
        self.sent: list[tuple[str, str | None]] = []
        self.error = error

    def send(self, raw: str, *, thread_id: str | None = None) -> tuple[str, str]:
        if self.error:
            raise self.error
        self.sent.append((raw, thread_id))
        return f"msg{len(self.sent)}", thread_id or "thread1"
```

`app_client` already forwards `**app_options` to `create_app` (Task 6 Step 3 added `settings=`), so `gmail_for=` and `pitch_writer=` reach the app once Step 3 adds them.

- [ ] **Step 3: Give the app the two seams**

In `web/app.py`:
- import `ClaudePitchWriter, PitchWriter` from `core.pitch_writer`, and `gmail_for_mailbox` from `web.mail`;
- add two more arguments to `create_app`, beside `mail_exchange`:

```python
    pitch_writer: PitchWriter | None = None,
    gmail_for: Callable[..., object] | None = None,
```

(`from collections.abc import Callable` at the top), and after the `mail_exchange` line:

```python
    app.state.pitch_writer = pitch_writer or _claude_pitch_writer(settings)
    # One Gmail client per send, built from the mailbox's own token. Tests pass a fake.
    app.state.gmail_for = gmail_for or gmail_for_mailbox
```

with, next to `_claude_suggester`:

```python
def _claude_pitch_writer(settings: WebSettings) -> PitchWriter | None:
    """Drafting a pitch needs ANTHROPIC_API_KEY. Without it the panel still composes by hand."""
    if settings.anthropic_api_key is None:
        return None
    return ClaudePitchWriter.from_api_key(settings.anthropic_api_key.get_secret_value())
```

and `pitches_router` added to the `signed_in` loop, after `digest_router`.

In `web/mail.py`, add the factory the app state points at:

```python
def gmail_for_mailbox(mailbox: MailAccount, cipher, settings, http: httpx.Client) -> Gmail:
    """One mailbox's Gmail access, on the caller's HTTP client so the socket is closed after."""
    return Gmail(
        http,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        refresh_token=refresh_token_for(mailbox, cipher),
    )
```

(import `refresh_token_for` from `core.mailboxes`.)

- [ ] **Step 4: Make room in the entry card**

In `web/templates/digest/_entry.html`, replace the status tag block:

```html
    {% if entry.status != "new" %}
    <span class="tag tag--status">{{ verdict_labels.get(entry.status, entry.status) }}</span>
    {% endif %}
```

with one that always exists, so a send can swap it from anywhere:

```html
    {# Always rendered, empty when there's no verdict yet: sending a pitch swaps this out of band. #}
    <span class="tag tag--status{% if entry.status == 'new' %} tag--empty{% endif %}" id="entry-status-{{ entry.outreach_id }}">
      {% if entry.status != "new" %}{{ verdict_labels.get(entry.status, entry.status) }}{% endif %}
    </span>
```

and add the disclosure at the end of the card, after the verdict buttons `</div>`:

```html
  <details class="digest-entry__pitch">
    <summary class="digest-entry__pitch-summary">Write a pitch</summary>
    <div id="pitch-{{ entry.outreach_id }}" hx-get="/outreach/{{ entry.outreach_id }}/pitch"
         hx-trigger="toggle once from:closest details" hx-swap="innerHTML">
      <p class="field__hint">Loading…</p>
    </div>
  </details>
```

Append to `web/static/css/app.css`, after the Pitch mailbox section:

```css
/* ---- Pitching by email --------------------------------------------------------------- */

.tag--empty {
  display: none;
}

.digest-entry__pitch {
  margin-top: var(--space-3);
  border-top: 1px solid var(--line);
  padding-top: var(--space-3);
}

.digest-entry__pitch-summary {
  cursor: pointer;
  font-weight: 600;
}

.digest-entry__pitch-summary:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}

.pitch__body {
  min-height: 12rem;
  font: inherit;
  line-height: 1.55;
}

.pitch__thread {
  display: grid;
  gap: var(--space-2);
  margin: var(--space-3) 0 0;
}

.pitch__message {
  border-left: 2px solid var(--line);
  padding-left: var(--space-3);
  white-space: pre-wrap;
}

.pitch__message--in {
  border-left-color: var(--accent);
}

.pitch__message-meta {
  color: var(--text-dim);
  font-size: 0.85rem;
}
```

Check the custom property names against the top of `app.css` before pasting — use whatever that file already calls its line, accent, dim-text and focus colours.

- [ ] **Step 5: Write the failing route tests**

Create `tests/test_web_pitches.py`:

```python
"""Writing, saving and sending a pitch from a digest entry."""

import base64
from datetime import UTC, date, datetime

import pytest
from cryptography.fernet import Fernet

from core.gmail import GmailError
from core.models import EmailMessage, MailDirection, Outreach, OutreachStatus
from core.pitch_writer import PitchWriterError
from tests.factories import (
    make_artist,
    make_contact,
    make_curator,
    make_mailbox,
    make_member,
    make_outreach,
    make_playlist,
    make_profile,
    make_user,
)
from tests.web_helpers import FakeGmailSender, FakePitchWriter, csrf_token, member_client, web_settings

KEY = Fernet.generate_key().decode()
NIGHT = date(2026, 9, 16)
HTML = {"accept": "text/html"}
EMAIL = "nina@broken-machines.com"


@pytest.fixture
def mail_settings():
    return web_settings().model_copy(update={"mail_token_key": KEY})


def a_digest_entry(session, *, with_mailbox=True, with_email=True):
    artist = make_artist(session, "Synman")
    profile = make_profile(session, "IDM Playlists", artist=artist)
    make_member(session, artist, make_user(session, "nik@example.com"))
    curator = make_curator(session, display_name="Nina")
    if with_email:
        make_contact(session, curator, "email", EMAIL)
    playlist = make_playlist(session, curator=curator, name="Broken Machines")
    outreach = make_outreach(session, curator, profile, NIGHT, playlist=playlist)
    if with_mailbox:
        profile.mail_account_id = make_mailbox(session, artist, address="synman@gmail.com").id
    session.flush()
    return outreach


def a_client(session, *, settings, gmail=None, writer=None):
    return member_client(
        session,
        "nik@example.com",
        settings=settings,
        pitch_writer=writer or FakePitchWriter(),
        gmail_for=lambda mailbox, cipher, app_settings, http: gmail or FakeGmailSender(),
    )


def post(client, path, data):
    return client.post(
        path, data=data, headers={"x-csrf-token": csrf_token(client), "hx-request": "true"}
    )


class TestOpeningThePanel:
    def test_it_shows_who_the_email_would_go_to(self, session, mail_settings):
        outreach = a_digest_entry(session)
        client = a_client(session, settings=mail_settings)

        html = client.get(f"/outreach/{outreach.id}/pitch", headers=HTML).text

        assert EMAIL in html
        assert "synman@gmail.com" in html
        assert f'hx-post="/outreach/{outreach.id}/pitch/send"' in html

    def test_a_saved_draft_comes_back(self, session, mail_settings):
        outreach = a_digest_entry(session)
        outreach.draft_subject, outreach.draft_body = "Kelvin", "Hi Nina"
        session.flush()
        client = a_client(session, settings=mail_settings)

        html = client.get(f"/outreach/{outreach.id}/pitch", headers=HTML).text

        assert "Kelvin" in html
        assert "Hi Nina" in html

    def test_without_a_mailbox_it_says_where_to_connect_one(self, session, mail_settings):
        outreach = a_digest_entry(session, with_mailbox=False)
        client = a_client(session, settings=mail_settings)

        html = client.get(f"/outreach/{outreach.id}/pitch", headers=HTML).text

        assert f"/profiles/{outreach.profile_id}" in html
        assert "Send" not in html

    def test_a_curator_with_no_email_says_so(self, session, mail_settings):
        outreach = a_digest_entry(session, with_email=False)
        client = a_client(session, settings=mail_settings)

        html = client.get(f"/outreach/{outreach.id}/pitch", headers=HTML).text

        assert "no email address" in html

    def test_another_artists_entry_is_not_found(self, session, mail_settings):
        theirs = make_outreach(session, make_curator(session), make_profile(session), NIGHT)
        a_digest_entry(session)
        client = a_client(session, settings=mail_settings)

        assert client.get(f"/outreach/{theirs.id}/pitch", headers=HTML).status_code == 404


class TestAskingClaude:
    def test_it_fills_the_box_with_claudes_draft(self, session, mail_settings):
        outreach = a_digest_entry(session)
        writer = FakePitchWriter(subject="Kelvin for Broken Machines", body="Hi Nina,\n\nKelvin...")
        client = a_client(session, settings=mail_settings, writer=writer)

        html = post(
            client,
            f"/outreach/{outreach.id}/pitch/write",
            {"instruction": "keep it short", "subject": "", "body": ""},
        ).text

        assert "Kelvin for Broken Machines" in html
        assert writer.calls[0].instruction == "keep it short"
        assert writer.calls[0].playlist_name == "Broken Machines"

    def test_the_draft_is_saved_so_a_closed_tab_keeps_it(self, session, mail_settings):
        outreach = a_digest_entry(session)
        client = a_client(session, settings=mail_settings)

        post(client, f"/outreach/{outreach.id}/pitch/write", {"instruction": "go", "subject": "", "body": ""})

        assert session.get(Outreach, outreach.id).draft_body == "A draft body"

    def test_revising_shows_claude_what_is_in_the_box(self, session, mail_settings):
        outreach = a_digest_entry(session)
        writer = FakePitchWriter()
        client = a_client(session, settings=mail_settings, writer=writer)

        post(
            client,
            f"/outreach/{outreach.id}/pitch/write",
            {"instruction": "shorter", "subject": "Kelvin", "body": "My long draft"},
        )

        assert writer.calls[0].previous_body == "My long draft"

    def test_claude_refusing_keeps_the_draft_and_says_so(self, session, mail_settings):
        outreach = a_digest_entry(session)
        writer = FakePitchWriter(error=PitchWriterError("Claude didn't answer. Try again in a moment."))
        client = a_client(session, settings=mail_settings, writer=writer)

        response = post(
            client,
            f"/outreach/{outreach.id}/pitch/write",
            {"instruction": "shorter", "subject": "Kelvin", "body": "My long draft"},
        )

        assert response.status_code == 200
        assert "Claude didn't answer" in response.text
        assert "My long draft" in response.text

    def test_without_an_api_key_the_button_is_not_offered(self, session, mail_settings):
        outreach = a_digest_entry(session)
        client = member_client(
            session,
            "nik@example.com",
            settings=mail_settings,
            pitch_writer=None,
            gmail_for=lambda mailbox, cipher, app_settings, http: FakeGmailSender(),
        )

        html = client.get(f"/outreach/{outreach.id}/pitch", headers=HTML).text

        assert "ANTHROPIC_API_KEY" in html
        assert 'hx-post="/outreach' in html  # saving and sending still work


class TestSaving:
    def test_it_keeps_what_is_in_the_box(self, session, mail_settings):
        outreach = a_digest_entry(session)
        client = a_client(session, settings=mail_settings)

        response = post(
            client, f"/outreach/{outreach.id}/pitch/save", {"subject": "Kelvin", "body": "Hi Nina"}
        )

        assert response.status_code == 200
        assert "Draft saved" in response.text
        assert session.get(Outreach, outreach.id).draft_subject == "Kelvin"

    def test_an_empty_draft_is_refused_in_place(self, session, mail_settings):
        outreach = a_digest_entry(session)
        client = a_client(session, settings=mail_settings)

        response = post(client, f"/outreach/{outreach.id}/pitch/save", {"subject": "Kelvin", "body": " "})

        assert response.status_code == 422
        assert "Write something" in response.text


class TestSending:
    def test_it_sends_and_records_the_pitch(self, session, mail_settings):
        outreach = a_digest_entry(session)
        gmail = FakeGmailSender()
        client = a_client(session, settings=mail_settings, gmail=gmail)
        send_key = _send_key(client, outreach.id)

        response = post(
            client,
            f"/outreach/{outreach.id}/pitch/send",
            {"subject": "Kelvin", "body": "Hi Nina", "send_key": send_key},
        )

        assert response.status_code == 200
        assert EMAIL in base64.urlsafe_b64decode(gmail.sent[0][0].encode()).decode()
        saved = session.get(Outreach, outreach.id)
        assert saved.status == OutreachStatus.PITCHED
        assert session.scalar(select(EmailMessage.direction)) == MailDirection.OUT

    def test_the_entry_shows_as_pitched_without_a_reload(self, session, mail_settings):
        outreach = a_digest_entry(session)
        client = a_client(session, settings=mail_settings)
        send_key = _send_key(client, outreach.id)

        html = post(
            client,
            f"/outreach/{outreach.id}/pitch/send",
            {"subject": "Kelvin", "body": "Hi Nina", "send_key": send_key},
        ).text

        assert f'id="entry-status-{outreach.id}"' in html
        assert 'hx-swap-oob="true"' in html
        assert "Pitched" in html

    def test_the_sent_message_is_shown_in_the_thread(self, session, mail_settings):
        outreach = a_digest_entry(session)
        client = a_client(session, settings=mail_settings)
        send_key = _send_key(client, outreach.id)

        html = post(
            client,
            f"/outreach/{outreach.id}/pitch/send",
            {"subject": "Kelvin", "body": "Hi Nina", "send_key": send_key},
        ).text

        assert "Hi Nina" in html
        assert "Sent" in html

    def test_gmail_refusing_says_so_and_sends_nothing(self, session, mail_settings):
        outreach = a_digest_entry(session)
        gmail = FakeGmailSender(error=GmailError("auth", "Google refused the saved Gmail access"))
        client = a_client(session, settings=mail_settings, gmail=gmail)
        send_key = _send_key(client, outreach.id)

        response = post(
            client,
            f"/outreach/{outreach.id}/pitch/send",
            {"subject": "Kelvin", "body": "Hi Nina", "send_key": send_key},
        )

        assert response.status_code == 200
        assert "Reconnect" in response.text
        assert session.get(Outreach, outreach.id).status == OutreachStatus.NEW

    def test_pressing_send_twice_sends_once(self, session, mail_settings):
        outreach = a_digest_entry(session)
        gmail = FakeGmailSender()
        client = a_client(session, settings=mail_settings, gmail=gmail)
        send_key = _send_key(client, outreach.id)
        body = {"subject": "Kelvin", "body": "Hi Nina", "send_key": send_key}
        post(client, f"/outreach/{outreach.id}/pitch/send", body)

        second = post(client, f"/outreach/{outreach.id}/pitch/send", body)

        assert second.status_code == 200
        assert len(gmail.sent) == 1

    def test_sending_without_a_mailbox_is_refused(self, session, mail_settings):
        outreach = a_digest_entry(session, with_mailbox=False)
        client = a_client(session, settings=mail_settings)

        response = post(
            client,
            f"/outreach/{outreach.id}/pitch/send",
            {"subject": "Kelvin", "body": "Hi Nina", "send_key": "k"},
        )

        assert response.status_code == 422
        assert "mailbox" in response.text.lower()


def _send_key(client, outreach_id: int) -> str:
    """The key the panel rendered, the way the form would post it back."""
    html = client.get(f"/outreach/{outreach_id}/pitch", headers=HTML).text
    match = re.search(r'name="send_key" value="([^"]+)"', html)
    assert match, "the panel didn't render a send key"
    return match.group(1)
```

Add `import re` and `from sqlalchemy import select` to the imports at the top of this file — both are used above.

- [ ] **Step 6: Write `web/pitches.py`**

```python
"""Writing a pitch on a digest entry: read the panel, ask Claude, save the draft, send it.

The panel is part of the digest entry but loads on its own, the first time the disclosure is
opened, so the digest page stays one query per night. Every route starts with `require_outreach`,
so another artist's entry is not found before anything else happens.

Sending is the only route here that touches the outside world. It needs three things -- a
mailbox on the profile, an email address for the curator, and a draft -- and says plainly which
one is missing rather than failing. A `send_key` rendered into the form makes a double-click or
a resubmitted page harmless.
"""

import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.access import Viewer, require_outreach
from core.gmail import GmailError
from core.mail_crypto import MailNotConfigured
from core.mailboxes import MailboxProblem, mark_needs_reconnect
from core.models import MailAccount, Outreach, ProfileTrack
from core.pitch_writer import PitchRequest, PitchWriterError
from core.pitches import (
    PitchProblem,
    mark_thread_read,
    pitch_address,
    save_draft,
    send_pitch,
    thread_messages,
)
from web.access import CurrentViewer
from web.db import get_db
from web.forms import form_id
from web.mail import cipher_or_none
from web.templating import templates

logger = logging.getLogger("noble_hunter.pitches")

router = APIRouter(prefix="/outreach/{outreach_id}/pitch")
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]

GMAIL_TIMEOUT_SECONDS = 30
NO_MAILBOX = "This profile isn't pitching from a mailbox yet. Connect one on the profile page."
NO_WRITER = "Drafting with Claude needs ANTHROPIC_API_KEY. You can still write the pitch yourself."
RECONNECT = "Google refused this mailbox. Reconnect it on the profile page, then send again."
SENT = "Sent."


@router.get("")
def panel(request: Request, outreach_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    outreach = require_outreach(db, viewer, outreach_id)
    # Opening the thread is what "reading" means; this is the only place `read_at` is set.
    if mark_thread_read(db, outreach, now=datetime.now(UTC)):
        db.commit()
    return _panel(request, db, outreach)


@router.post("/write")
def write(
    request: Request,
    outreach_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    instruction: FormText = "",
    subject: FormText = "",
    body: FormText = "",
    track_id: FormText = "",
) -> Response:
    outreach = require_outreach(db, viewer, outreach_id)
    writer = request.app.state.pitch_writer
    if writer is None:
        return _panel(request, db, outreach, subject=subject, body=body, error=NO_WRITER)

    chosen_track = _track(outreach, form_id(track_id))
    try:
        pitch = writer.write(_pitch_request(db, outreach, instruction, body, chosen_track, viewer))
    except PitchWriterError as error:
        return _panel(request, db, outreach, subject=subject, body=body, error=str(error))

    outreach.draft_track_id = chosen_track.id if chosen_track else None
    save_draft(db, outreach, subject=pitch.subject, body=pitch.body, now=datetime.now(UTC))
    db.commit()
    return _panel(request, db, outreach, notice="Draft written. Edit it before sending.")


@router.post("/save")
def save(
    request: Request,
    outreach_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    subject: FormText = "",
    body: FormText = "",
    track_id: FormText = "",
) -> Response:
    outreach = require_outreach(db, viewer, outreach_id)
    try:
        outreach.draft_track_id = _track_id_or_none(outreach, track_id)
        save_draft(db, outreach, subject=subject, body=body, now=datetime.now(UTC))
        db.commit()
    except PitchProblem as problem:
        db.rollback()
        return _panel(
            request, db, outreach, subject=subject, body=body, error=str(problem), status_code=422
        )
    return _panel(request, db, outreach, notice="Draft saved")


@router.post("/send")
def send(
    request: Request,
    outreach_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    subject: FormText = "",
    body: FormText = "",
    send_key: FormText = "",
    track_id: FormText = "",
) -> Response:
    outreach = require_outreach(db, viewer, outreach_id)
    mailbox = _mailbox(db, outreach)
    cipher = cipher_or_none(request)
    if mailbox is None or cipher is None:
        return _panel(
            request, db, outreach, subject=subject, body=body, error=NO_MAILBOX, status_code=422
        )

    now = datetime.now(UTC)
    try:
        outreach.draft_track_id = _track_id_or_none(outreach, track_id)
        with httpx.Client(timeout=GMAIL_TIMEOUT_SECONDS) as http:
            gmail = request.app.state.gmail_for(mailbox, cipher, request.app.state.settings, http)
            send_pitch(
                db,
                outreach,
                gmail=gmail,
                mailbox=mailbox,
                subject=subject,
                body=body,
                now=now,
                send_key=send_key.strip() or None,
            )
        db.commit()
    except MailNotConfigured:
        # The key can't read this mailbox's stored token (it was rotated, or the row was
        # altered). Reconnecting stores a fresh one, so that's what to say -- and a 500 is
        # exactly what this must not be.
        db.rollback()
        logger.warning("A stored Gmail token couldn't be read")
        return _panel(request, db, outreach, subject=subject, body=body, error=RECONNECT)
    except (PitchProblem, MailboxProblem) as problem:
        db.rollback()
        already = "already been sent" in str(problem)
        return _panel(
            request,
            db,
            outreach,
            subject="" if already else subject,
            body="" if already else body,
            error=None if already else str(problem),
            notice=SENT if already else None,
            status_code=200 if already else 422,
        )
    except GmailError as error:
        db.rollback()
        if error.kind == "auth":
            mark_needs_reconnect(db, mailbox, str(error))
            db.commit()
        logger.warning("Sending a pitch failed (%s)", error.kind)
        message = RECONNECT if error.kind == "auth" else "Gmail couldn't send that. Try again shortly."
        return _panel(request, db, outreach, subject=subject, body=body, error=message)
    return _panel(request, db, outreach, notice=SENT, swap_status=True)


def _panel(
    request: Request,
    db: Session,
    outreach: Outreach,
    *,
    subject: str | None = None,
    body: str | None = None,
    notice: str | None = None,
    error: str | None = None,
    status_code: int = 200,
    swap_status: bool = False,
) -> Response:
    """Render the panel. `subject`/`body` override the saved draft, so a refused post keeps typing."""
    mailbox = _mailbox(db, outreach)
    try:
        address, address_error = pitch_address(db, outreach), None
    except PitchProblem as problem:
        address, address_error = None, str(problem)

    profile = outreach.profile
    context = {
        "outreach": outreach,
        "profile": profile,
        "playlist_name": outreach.playlist.name,
        "curator_name": outreach.curator.display_name,
        "address": address,
        "address_error": address_error,
        "mailbox": mailbox,
        "mail_configured": cipher_or_none(request) is not None,
        "can_write": request.app.state.pitch_writer is not None,
        "no_writer_note": None if request.app.state.pitch_writer is not None else NO_WRITER,
        "subject": outreach.draft_subject or "" if subject is None else subject,
        "body": outreach.draft_body or "" if body is None else body,
        "tracks": sorted(profile.tracks, key=lambda track: track.title.casefold()),
        "draft_track_id": outreach.draft_track_id,
        "messages": thread_messages(db, outreach),
        "send_key": secrets.token_hex(16),
        "notice": notice,
        "error": error,
        "swap_status": swap_status,
        "status_label": "Pitched",
    }
    return templates.TemplateResponse(request, "pitch/_panel.html", context, status_code=status_code)


def _mailbox(db: Session, outreach: Outreach) -> MailAccount | None:
    """The mailbox this entry pitches from: the one it already used, else the profile's."""
    mail_account_id = outreach.mail_account_id or outreach.profile.mail_account_id
    mailbox = db.get(MailAccount, mail_account_id) if mail_account_id else None
    return mailbox if mailbox is not None and mailbox.is_connected else None


def _track(outreach: Outreach, track_id: int) -> ProfileTrack | None:
    """The track this pitch is about: the one chosen, else the entry's saved choice, else the first."""
    tracks = {track.id: track for track in outreach.profile.tracks}
    chosen = tracks.get(track_id) or tracks.get(outreach.draft_track_id or 0)
    if chosen is not None:
        return chosen
    return min(tracks.values(), key=lambda track: track.title.casefold(), default=None)


def _track_id_or_none(outreach: Outreach, raw: str) -> int | None:
    track = _track(outreach, form_id(raw))
    return track.id if track is not None else None


def _pitch_request(
    db: Session, outreach: Outreach, instruction: str, body: str, track: ProfileTrack | None, viewer: Viewer
) -> PitchRequest:
    profile = outreach.profile
    tracks = ((track.title, track.description or ""),) if track else ()
    return PitchRequest(
        artist_name=profile.artist.name,
        profile_name=profile.name,
        curator_name=outreach.curator.display_name,
        playlist_name=outreach.playlist.name,
        playlist_url=f"https://open.spotify.com/playlist/{outreach.playlist_id}",
        brief=outreach.brief_text,
        angle=outreach.suggested_angle,
        reference_artists=tuple(artist.display_name for artist in profile.reference_artists),
        tracks=tracks,
        sender_name=viewer.email.split("@", 1)[0],
        instruction=instruction,
        previous_body=body,
    )
```

One thing to know while writing this: `sender_name` above is a placeholder taken from the signed-in email. Leave it; the artist's own sign-off is a later refinement, and Claude is told to sign off as the musician either way.

- [ ] **Step 7: Write the panel template**

Create `web/templates/pitch/_panel.html`:

```html
{# Writing one pitch. Swapped in place by write, save and send; send also swaps the entry's status tag. #}
<div class="pitch" id="pitch-body-{{ outreach.id }}">
  {% if notice %}<p class="notice" role="status">{{ notice }}</p>{% endif %}
  {% if error %}<p class="field__error" role="alert">{{ error }}</p>{% endif %}

  {% if address_error %}
  <p class="field__hint">{{ address_error }} Use the contact route on the card instead.</p>
  {% elif not mail_configured %}
  <p class="field__hint">Email pitching isn't switched on: MAIL_TOKEN_KEY isn't set for this app.</p>
  {% elif not mailbox %}
  <p class="field__hint">
    {{ profile.name }} isn't pitching from a mailbox yet.
    <a href="/profiles/{{ profile.id }}">Connect one on the profile</a>.
  </p>
  {% else %}
  <p class="pitch__addresses">
    From <strong>{{ mailbox.address }}</strong> to <strong>{{ address }}</strong>
  </p>

  <form class="form" hx-target="#pitch-{{ outreach.id }}" hx-swap="innerHTML" hx-disabled-elt="find button"
        novalidate>
    {% if tracks %}
    <div class="field">
      <label class="field__label" for="pitch-track-{{ outreach.id }}">Track to pitch</label>
      <select class="input" id="pitch-track-{{ outreach.id }}" name="track_id">
        {% for track in tracks %}
        <option value="{{ track.id }}" {% if track.id == draft_track_id %}selected{% endif %}>{{ track.title }}</option>
        {% endfor %}
      </select>
    </div>
    {% endif %}

    {% if can_write %}
    <div class="field">
      <label class="field__label" for="pitch-instruction-{{ outreach.id }}">Ask Claude for a draft</label>
      <input class="input" id="pitch-instruction-{{ outreach.id }}" name="instruction" autocomplete="off"
             placeholder="e.g. shorter, and mention the Autechre track">
      <button class="button button--ghost button--small" type="button"
              hx-post="/outreach/{{ outreach.id }}/pitch/write" hx-include="closest form">
        Draft it
        <span class="htmx-indicator spinner" aria-hidden="true"></span>
      </button>
    </div>
    {% else %}
    <p class="field__hint">{{ no_writer_note }}</p>
    {% endif %}

    <div class="field">
      <label class="field__label" for="pitch-subject-{{ outreach.id }}">Subject</label>
      <input class="input" id="pitch-subject-{{ outreach.id }}" name="subject" value="{{ subject }}"
             maxlength="200" autocomplete="off">
    </div>

    <div class="field">
      <label class="field__label" for="pitch-body-text-{{ outreach.id }}">Message</label>
      <textarea class="input pitch__body" id="pitch-body-text-{{ outreach.id }}" name="body" rows="12">{{ body }}</textarea>
    </div>

    <input type="hidden" name="send_key" value="{{ send_key }}">
    <div class="form__actions">
      <button class="button button--ghost" type="button" hx-post="/outreach/{{ outreach.id }}/pitch/save"
              hx-include="closest form">
        Save draft
        <span class="htmx-indicator spinner" aria-hidden="true"></span>
      </button>
      <button class="button button--primary" type="button" hx-post="/outreach/{{ outreach.id }}/pitch/send"
              hx-include="closest form"
              hx-confirm="Send this to {{ address }}?">
        Send
        <span class="htmx-indicator spinner" aria-hidden="true"></span>
      </button>
    </div>
  </form>
  {% endif %}

  {% if messages %}
  <ol class="pitch__thread" role="list">
    {% for message in messages %}
    <li class="pitch__message pitch__message--{{ message.direction }}">
      <p class="pitch__message-meta">
        {% if message.direction == "out" %}Sent to {{ message.to_address }}{% else %}From {{ message.from_address }}{% endif %}
        · {{ message.sent_at.strftime("%-d %b, %H:%M") }}
      </p>
      <p class="pitch__message-body">{{ message.body_text }}</p>
    </li>
    {% endfor %}
  </ol>
  {% endif %}
</div>

{% if swap_status %}
{# The entry card is already on screen; keep its status tag in step without reloading the digest. #}
<span class="tag tag--status" id="entry-status-{{ outreach.id }}" hx-swap-oob="true">{{ status_label }}</span>
{% endif %}
```

- [ ] **Step 8: Put a pitchable entry in the access world**

In `tests/access_world.py`:
- give their curator an email contact and their profile a mailbox, so a leaked send would really send:

```python
    make_contact(session, outreach.curator, "email", "nina@broken-machines.com")
    profile.mail_account_id = make_mailbox(session, theirs, address="theirs@gmail.com").id
    session.flush()
```

(import `make_contact` and `make_mailbox`), and add `"outreach_draft"` and `"email_messages"` to `their_data`:

```python
        "outreach_draft": (outreach.draft_subject, outreach.draft_body, outreach.gmail_thread_id),
        "email_messages": session.scalar(select(func.count()).select_from(EmailMessage)),
```

(import `EmailMessage` from `core.models`.)

- in `seed_route_state`, give the save and send routes something to overwrite:

```python
    elif (method, path) in {
        ("POST", "/outreach/{outreach_id}/pitch/save"),
        ("POST", "/outreach/{outreach_id}/pitch/send"),
    }:
        entry = session.get(Outreach, world.outreach_id)
        entry.draft_subject, entry.draft_body = "Their subject", "Their draft"
```

The walk's clients are built by `outsider_client` and `admin_client`, which don't pass `gmail_for`, so the app would build a real Gmail client if a send ever got through. That is exactly what should never happen — but to make the test's failure a clean assertion rather than a network call, pass a stub in both:

```python
    gmail_for=lambda mailbox, cipher, settings, http: _RefusingGmail(),
```

with, at the top of the file:

```python
class _RefusingGmail:
    """Any send reaching this in an access walk is a leak; fail loudly rather than over the network."""

    def send(self, raw: str, *, thread_id: str | None = None):
        raise AssertionError("An access walk reached Gmail; a send leaked past require_outreach")
```

- [ ] **Step 9: Run the tests**

Run: `uv run pytest tests/test_web_pitches.py tests/test_web_access.py tests/test_web_access_controls.py tests/test_web_access_sweeps.py tests/test_web_digest.py -q`

Expected: all pass. If `test_web_digest` fails on the status tag, it is asserting on the old markup — update that assertion to the new `id="entry-status-…"` span rather than changing the template back.

- [ ] **Step 10: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add web/pitches.py web/app.py web/mail.py web/templates/pitch web/templates/digest/_entry.html \
  web/static/css/app.css tests
git commit -m "feat: write and send a pitch from the digest entry

The panel loads only when opened, so the digest page costs nothing extra. Claude drafts,
Jarred edits, and sending records the pitch and swaps the entry's status in place.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Stage 4: Reading replies

### Task 10: Bringing replies in from Gmail

**Files:**
- Create: `core/mail_sync.py`, `tests/test_mail_sync.py`

Gmail's history API gives "what changed since history id X" cheaply, which is what the runner asks every few minutes. Two rules matter more than the plumbing:

1. **Only mail on a thread we started is ever stored.** The mailbox is Jarred's own Gmail, full of unrelated mail; a message whose `threadId` doesn't match an outreach entry on *this* mailbox is ignored and never read into the database.
2. **Running it twice stores one copy.** `(mail_account_id, gmail_message_id)` is unique, and the sync checks before inserting, so a retry after a half-finished run is safe.

A history id can expire (Gmail keeps roughly a week). That isn't an error: the sync re-reads the threads it already knows about and takes a fresh history id.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mail_sync.py`:

```python
"""Bringing replies in from Gmail: matching them to entries, and ignoring everything else."""

import base64
from datetime import UTC, date, datetime

import pytest

from core.gmail import GmailError
from core.mail_sync import sync_all, sync_mailbox
from core.models import EmailMessage, MailDirection
from tests.factories import (
    make_artist,
    make_curator,
    make_mailbox,
    make_outreach,
    make_playlist,
    make_profile,
)

NIGHT = date(2026, 9, 16)
NOW = datetime(2026, 9, 17, 6, 0, tzinfo=UTC)
MAILBOX_ADDRESS = "synman@gmail.com"
CURATOR_ADDRESS = "nina@broken-machines.com"


def gmail_message(message_id: str, thread_id: str, *, sender: str, body: str, sent_ms: int = 1_758_000_000_000):
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": str(sent_ms),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": MAILBOX_ADDRESS},
                {"name": "Subject", "value": "Re: Kelvin"},
                {"name": "Message-ID", "value": f"<{message_id}@mail.gmail.com>"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


class FakeGmail:
    """Stands in for one mailbox's Gmail: canned history, messages and threads."""

    def __init__(self, history=(), messages=None, threads=None, history_id="2000", error=None):
        self.history = list(history)
        self.messages = messages or {}
        self.threads = threads or {}
        self.new_history_id = history_id
        self.error = error
        self.history_calls: list[str] = []
        self.fetched: list[str] = []

    def history_since(self, history_id: str):
        self.history_calls.append(history_id)
        if self.error:
            raise self.error
        return list(self.history), self.new_history_id

    def message(self, message_id: str) -> dict:
        self.fetched.append(message_id)
        return self.messages[message_id]

    def thread(self, thread_id: str) -> dict:
        return self.threads[thread_id]

    def profile(self) -> tuple[str, str]:
        return MAILBOX_ADDRESS, self.new_history_id


def a_pitched_entry(session, *, thread_id="thread1"):
    artist = make_artist(session, "Synman")
    profile = make_profile(session, "IDM Playlists", artist=artist)
    mailbox = make_mailbox(session, artist, address=MAILBOX_ADDRESS, history_id="1000")
    profile.mail_account_id = mailbox.id
    curator = make_curator(session, display_name="Nina")
    playlist = make_playlist(session, curator=curator)
    outreach = make_outreach(session, curator, profile, NIGHT, playlist=playlist)
    outreach.mail_account_id = mailbox.id
    outreach.gmail_thread_id = thread_id
    session.flush()
    return outreach, mailbox


def a_reply_gmail(message_id="reply1", thread_id="thread1", sender=CURATOR_ADDRESS, body="Send it over."):
    return FakeGmail(
        history=[(message_id, thread_id)],
        messages={message_id: gmail_message(message_id, thread_id, sender=sender, body=body)},
    )


class TestBringingRepliesIn:
    def test_a_reply_lands_on_the_entry_it_belongs_to(self, session):
        outreach, mailbox = a_pitched_entry(session)
        gmail = a_reply_gmail()

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert outcome.stored == 1
        message = session.scalars(select(EmailMessage)).one()
        assert message.outreach_id == outreach.id
        assert message.direction == MailDirection.IN
        assert message.from_address == CURATOR_ADDRESS
        assert message.body_text == "Send it over."
        assert message.rfc822_message_id == "<reply1@mail.gmail.com>"

    def test_our_own_sent_mail_is_recorded_as_outgoing(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = a_reply_gmail(message_id="mine1", sender=MAILBOX_ADDRESS, body="Hi Nina")

        sync_mailbox(session, mailbox, gmail, now=NOW)

        assert session.scalars(select(EmailMessage)).one().direction == MailDirection.OUT

    def test_mail_on_an_unknown_thread_is_never_stored(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = a_reply_gmail(message_id="bank1", thread_id="someone-elses-thread")

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert (outcome.stored, outcome.ignored) == (0, 1)
        assert session.scalar(select(func.count()).select_from(EmailMessage)) == 0
        assert gmail.fetched == []  # not even read: the thread id alone says it isn't ours

    def test_another_mailboxs_thread_is_not_claimed(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        other = make_mailbox(session, make_artist(session), address="someone@gmail.com", history_id="1000")
        gmail = a_reply_gmail()

        outcome = sync_mailbox(session, other, gmail, now=NOW)

        assert outcome.stored == 0

    def test_syncing_twice_stores_one_copy(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = a_reply_gmail()
        sync_mailbox(session, mailbox, gmail, now=NOW)

        second = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert second.stored == 0
        assert session.scalar(select(func.count()).select_from(EmailMessage)) == 1

    def test_it_moves_the_history_id_on_and_checks_in(self, session):
        _outreach, mailbox = a_pitched_entry(session)

        sync_mailbox(session, mailbox, a_reply_gmail(), now=NOW)

        assert mailbox.history_id == "2000"
        assert mailbox.last_checked_at == NOW

    def test_it_asks_gmail_only_for_what_changed(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = a_reply_gmail()

        sync_mailbox(session, mailbox, gmail, now=NOW)

        assert gmail.history_calls == ["1000"]


class TestWhenGmailSaysNo:
    def test_an_expired_history_id_rereads_the_threads_we_know(self, session):
        outreach, mailbox = a_pitched_entry(session)
        gmail = FakeGmail(error=GmailError("history-gone", "That history id is too old"))
        gmail.threads = {
            "thread1": {"messages": [gmail_message("reply1", "thread1", sender=CURATOR_ADDRESS, body="Yes")]}
        }

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert outcome.reset is True
        assert outcome.stored == 1
        assert session.scalars(select(EmailMessage)).one().outreach_id == outreach.id
        assert mailbox.history_id == "2000"  # taken fresh from the profile call

    def test_refused_access_marks_the_mailbox_for_reconnection(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = FakeGmail(error=GmailError("auth", "Google refused the saved Gmail access"))

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert mailbox.needs_reconnect is True
        assert outcome.error
        assert mailbox.history_id == "1000"  # unchanged, so nothing is skipped after reconnecting

    def test_a_transient_failure_changes_nothing(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = FakeGmail(error=GmailError("transient", "Gmail is busy"))

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert outcome.error
        assert mailbox.needs_reconnect is False
        assert mailbox.history_id == "1000"

    def test_a_message_that_cannot_be_read_is_skipped_not_fatal(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = FakeGmail(
            history=[("bad1", "thread1"), ("reply1", "thread1")],
            messages={
                "bad1": {"id": "bad1", "threadId": "thread1"},  # no payload, no date
                "reply1": gmail_message("reply1", "thread1", sender=CURATOR_ADDRESS, body="Yes"),
            },
        )

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert (outcome.stored, outcome.ignored) == (1, 1)
        assert session.scalars(select(EmailMessage)).one().gmail_message_id == "reply1"
        assert mailbox.history_id == "2000"


class TestAllMailboxes:
    def test_it_syncs_every_connected_mailbox(self, session):
        _first, mailbox = a_pitched_entry(session)
        _second, other = a_pitched_entry(session, thread_id="thread2")
        opened: list[int] = []

        def open_gmail(box):
            opened.append(box.id)
            return FakeGmail()  # nothing new in either mailbox

        sync_all(session, open_gmail=open_gmail, now=NOW)

        assert sorted(opened) == sorted([mailbox.id, other.id])

    def test_a_disconnected_mailbox_is_left_alone(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        mailbox.refresh_token_encrypted = None
        session.flush()
        opened: list[int] = []

        def open_gmail(box):
            opened.append(box.id)
            return FakeGmail()

        sync_all(session, open_gmail=open_gmail, now=NOW)

        assert opened == []

    def test_a_mailbox_awaiting_reconnection_is_skipped(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        mailbox.needs_reconnect = True
        session.flush()
        opened: list[int] = []

        def open_gmail(box):
            opened.append(box.id)
            return FakeGmail()

        sync_all(session, open_gmail=open_gmail, now=NOW)

        assert opened == []

    def test_one_mailbox_failing_doesnt_stop_the_others(self, session):
        _first, mailbox = a_pitched_entry(session)
        _second, other = a_pitched_entry(session, thread_id="thread2")

        def open_gmail(box):
            if box.id == mailbox.id:
                raise RuntimeError("no token")
            return FakeGmail()

        outcomes = sync_all(session, open_gmail=open_gmail, now=NOW)

        assert outcomes[mailbox.id].error
        assert outcomes[other.id].error is None
```

Add `from sqlalchemy import func, select` to the imports.

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run pytest tests/test_mail_sync.py -q`

Expected: `ModuleNotFoundError: No module named 'core.mail_sync'`.

- [ ] **Step 3: Write `core/mail_sync.py`**

```python
"""Reading a mailbox for replies, and putting each one on the entry it belongs to.

The mailbox is the artist's own Gmail, so most of what arrives in it is none of Noble Hunter's
business. Only a message whose Gmail thread matches an outreach entry on *this* mailbox is
fetched and stored; everything else is counted and forgotten, never read into the database.

Gmail's history API answers "what changed since history id X", which is cheap enough to ask
every few minutes. A history id older than about a week expires; that isn't a failure, it just
means re-reading the threads we already know about and taking a fresh id.

Nothing here raises for an ordinary problem: each mailbox returns a `SyncOutcome`, so one
mailbox that needs reconnecting can't stop the others being read.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.gmail import GmailError
from core.mailboxes import artist_mailboxes, mark_needs_reconnect
from core.mime import parse_message
from core.models import EmailMessage, MailAccount, MailDirection, Outreach

logger = logging.getLogger("noble_hunter.mail_sync")

RECONNECT_MESSAGE = "Google refused this mailbox's access. Connect it again to read replies."


@dataclass
class SyncOutcome:
    stored: int = 0
    ignored: int = 0
    reset: bool = False
    error: str | None = None


def sync_mailbox(session: Session, mailbox: MailAccount, gmail, *, now: datetime) -> SyncOutcome:
    """Bring this mailbox up to date. Returns what happened; never raises for an ordinary problem."""
    outcome = SyncOutcome()
    try:
        added, new_history_id = _changes(session, gmail, mailbox, outcome)
    except GmailError as error:
        return _failed(session, mailbox, error, outcome)

    threads = _threads_we_know(session, mailbox)
    for message_id, thread_id in added:
        outreach_id = threads.get(thread_id)
        if outreach_id is None:
            outcome.ignored += 1  # someone else's mail in the same mailbox
            continue
        if _already_stored(session, mailbox.id, message_id):
            continue
        try:
            payload = gmail.message(message_id)
            _store(session, mailbox, outreach_id, payload)
        except GmailError as error:
            if error.kind == "auth":
                return _failed(session, mailbox, error, outcome)
            logger.warning("Couldn't read message %s: %s", message_id, error)
            outcome.ignored += 1
            continue
        except (KeyError, TypeError, ValueError):
            logger.warning("Message %s couldn't be read; skipping", message_id, exc_info=True)
            outcome.ignored += 1
            continue
        outcome.stored += 1

    mailbox.history_id = new_history_id
    mailbox.last_checked_at = now
    if mailbox.needs_reconnect:
        mailbox.needs_reconnect, mailbox.last_error = False, None
    session.flush()
    return outcome


def sync_all(
    session: Session,
    *,
    open_gmail: Callable[[MailAccount], object],
    now: datetime,
) -> dict[int, SyncOutcome]:
    """Sync every connected mailbox, one at a time. One failing doesn't stop the rest."""
    outcomes: dict[int, SyncOutcome] = {}
    for mailbox in _connected_mailboxes(session):
        try:
            outcomes[mailbox.id] = sync_mailbox(session, mailbox, open_gmail(mailbox), now=now)
        except Exception as error:  # opening the mailbox itself failed (no token, no network)
            logger.warning("Couldn't read %s: %s", mailbox.address, type(error).__name__)
            outcomes[mailbox.id] = SyncOutcome(error=f"{type(error).__name__}: {error}")
    return outcomes


def _changes(
    session: Session, gmail, mailbox: MailAccount, outcome: SyncOutcome
) -> tuple[list[tuple[str, str]], str]:
    """What's new since our history id -- or, if that id has expired, every thread we know about."""
    if mailbox.history_id:
        try:
            return gmail.history_since(mailbox.history_id)
        except GmailError as error:
            if error.kind != "history-gone":
                raise
            logger.info("History id for %s has expired; re-reading known threads", mailbox.address)

    # No history to work from: read the threads we started, and take a fresh id to carry on from.
    outcome.reset = True
    _address, history_id = gmail.profile()
    found: list[tuple[str, str]] = []
    for thread_id in _threads_we_know(session, mailbox):
        thread = gmail.thread(thread_id)
        found += [(str(message["id"]), thread_id) for message in thread.get("messages", [])]
    return found, history_id


def _threads_we_know(session: Session, mailbox: MailAccount) -> dict[str, int]:
    """Gmail thread id -> outreach id, for entries pitched from this mailbox."""
    rows = session.execute(
        select(Outreach.gmail_thread_id, Outreach.id).where(
            Outreach.mail_account_id == mailbox.id, Outreach.gmail_thread_id.is_not(None)
        )
    )
    return {thread_id: outreach_id for thread_id, outreach_id in rows}


def _already_stored(session: Session, mail_account_id: int, gmail_message_id: str) -> bool:
    return (
        session.scalar(
            select(EmailMessage.id).where(
                EmailMessage.mail_account_id == mail_account_id,
                EmailMessage.gmail_message_id == gmail_message_id,
            )
        )
        is not None
    )


def _store(session: Session, mailbox: MailAccount, outreach_id: int, payload: dict) -> None:
    parsed = parse_message(payload)
    ours = parsed.from_address.strip().lower() == mailbox.address.strip().lower()
    session.add(
        EmailMessage(
            mail_account_id=mailbox.id,
            outreach_id=outreach_id,
            gmail_message_id=parsed.gmail_message_id,
            gmail_thread_id=parsed.gmail_thread_id,
            rfc822_message_id=parsed.message_id_header,
            direction=MailDirection.OUT if ours else MailDirection.IN,
            from_address=parsed.from_address,
            to_address=parsed.to_address,
            subject=parsed.subject,
            body_text=parsed.body_text,
            quoted_text=parsed.quoted_text,
            sent_at=parsed.sent_at,
        )
    )
    session.flush()


def _failed(session: Session, mailbox: MailAccount, error: GmailError, outcome: SyncOutcome) -> SyncOutcome:
    """Record what went wrong. Auth failures need a person; anything else will be tried again."""
    if error.kind == "auth":
        mark_needs_reconnect(session, mailbox, RECONNECT_MESSAGE)
    outcome.error = str(error)
    return outcome


def _connected_mailboxes(session: Session) -> list[MailAccount]:
    """Every mailbox worth reading. One already needing reconnection is skipped: asking Google
    again would only fail the same way, and the person has already been told."""
    artist_ids = list(session.scalars(select(MailAccount.artist_id).distinct()))
    return [
        mailbox
        for artist_id in artist_ids
        for mailbox in artist_mailboxes(session, artist_id)
        if not mailbox.needs_reconnect
    ]
```

One judgement call is left to you: `_connected_mailboxes` reads every artist's mailboxes one artist at a time, reusing `artist_mailboxes` so there's one definition of "connected". A single query over `MailAccount` with those same two conditions is equally fine — pick whichever reads better, but keep the conditions identical to `artist_mailboxes`, or the two will drift.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_mail_sync.py -q`

Expected: 14 passed.

- [ ] **Step 5: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add core/mail_sync.py tests/test_mail_sync.py
git commit -m "feat: bring replies in from Gmail

Only mail on a thread we started is fetched or stored: the mailbox is Jarred's own, and the
rest of it is none of Noble Hunter's business. An expired history id re-reads known threads.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: The runner reads the mail

**Files:**
- Create: `pipeline/mail.py`, `tests/test_worker_mail.py`
- Modify: `pipeline/settings.py`, `pipeline/worker.py`, `pipeline/cli.py`, `.env.example`

The web app can't poll Gmail: a Vercel function lives for seconds and nobody is looking at the page at 7am. The Mac Mini already runs a loop every minute, so mail reading goes there — on its own thread, every five minutes, entirely separate from the nightly run. A run that takes an hour must not hold up replies, and mail failing must never fail a run.

- [ ] **Step 1: Give the pipeline the mail settings**

`PipelineSettings` doesn't inherit from `core.settings.Settings`, so it needs its own fields. In `pipeline/settings.py`, add to `PipelineSettings`:

```python
    # Reading replies (migration 0007). Without these the runner still does everything else.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    mail_token_key: SecretStr | None = None

    def mail_problem(self) -> str | None:
        """Why mail can't be read, or None when it can."""
        missing = [
            name.upper()
            for name in ("google_client_id", "google_client_secret", "mail_token_key")
            if getattr(self, name) is None
        ]
        return f"{', '.join(missing)} not set" if missing else None
```

In `.env.example`, under the mail section Task 1 added, note that the Mac Mini needs the same three values as the web app.

Watch for the same collision Task 1 hit: if `tests/test_pipeline_settings.py` asserts that the bare word `"secret"` is absent from a repr, the new `google_client_secret` **field name** will trip it. Fix it the same way — give the sentinel value a distinctive string rather than loosening the assertion.

- [ ] **Step 2: Write the failing test**

Create `tests/test_worker_mail.py`:

```python
"""The runner's mail thread: reading replies on a timer, without disturbing the nightly run."""

import threading
import time

from pipeline.worker import keep_reading_mail

SETTLE_SECONDS = 0.1


class CountingSync:
    """Stands in for a round of mail syncing, and can be told to fail."""

    def __init__(self, error: Exception | None = None):
        self.calls = 0
        self.error = error
        self.called = threading.Event()

    def __call__(self) -> None:
        self.calls += 1
        self.called.set()
        if self.error:
            raise self.error


def test_it_reads_the_mail_while_the_work_goes_on():
    sync = CountingSync()

    with keep_reading_mail(sync, every=0.01):
        assert sync.called.wait(timeout=2), "the mail thread never ran"

    assert sync.calls >= 1


def test_it_stops_when_the_work_is_done():
    sync = CountingSync()

    with keep_reading_mail(sync, every=0.01):
        sync.called.wait(timeout=2)
    after = sync.calls

    time.sleep(SETTLE_SECONDS)
    assert sync.calls == after


def test_a_failing_sync_doesnt_stop_the_thread():
    sync = CountingSync(error=RuntimeError("Gmail is down"))

    with keep_reading_mail(sync, every=0.01):
        assert sync.called.wait(timeout=2)
        deadline = time.monotonic() + 2
        while sync.calls < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

    assert sync.calls >= 2  # it kept going after the failure
```

And in `tests/test_cli_pipeline.py` (or a new `tests/test_cli_mail.py`, matching how the other CLI tests are laid out), a test that `mail --check` refuses when the settings are missing:

```python
def test_mail_check_says_what_is_missing(monkeypatch, runner):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("MAIL_TOKEN_KEY", raising=False)

    result = runner.invoke(app, ["mail", "--check"])

    assert result.exit_code != 0
    assert "MAIL_TOKEN_KEY" in result.output
```

Read the neighbouring CLI tests first and follow their fixtures — they already know how to keep a test away from the real `.env` files.

- [ ] **Step 3: Run it to watch it fail**

Run: `uv run pytest tests/test_worker_mail.py -q`

Expected: `ImportError: cannot import name 'keep_reading_mail' from 'pipeline.worker'`.

- [ ] **Step 4: Add the mail thread to the worker**

In `pipeline/worker.py`, next to `keep_checking_in`:

```python
MAIL_EVERY_SECONDS = 300


@contextmanager
def keep_reading_mail(sync: Callable[[], None], every: float = MAIL_EVERY_SECONDS) -> Iterator[None]:
    """Read replies on a background thread for as long as the runner lives.

    Separate from the run loop on purpose: a nightly run can take an hour, and replies shouldn't
    wait for it. A failure here is logged and tried again -- mail must never fail a run.
    """
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(every):
            try:
                sync()
            except Exception:
                logger.warning("Couldn't read the mail; will try again", exc_info=True)

    thread = threading.Thread(target=loop, name="noble-hunter-mail", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=every)
```

and give `run_forever` an optional `sync_mail` argument, wrapping its loop:

```python
def run_forever(
    engine: Engine,
    runner: Runner,
    *,
    stop: threading.Event,
    hostname: str | None = None,
    poll_seconds: float = POLL_SECONDS,
    sync_mail: Callable[[], None] | None = None,
) -> None:
```

with the existing body wrapped in:

```python
    with keep_reading_mail(sync_mail) if sync_mail else nullcontext():
        while not stop.is_set():
            ...
```

(`from contextlib import nullcontext` at the top, beside the other contextlib imports.)

- [ ] **Step 5: Write `pipeline/mail.py`**

```python
"""The runner's side of reading replies: open each mailbox's Gmail and sync it.

The web app sends; this reads. Both use `core.mail_sync`, so there is one definition of what a
reply is and where it belongs. Everything here is built per round and closed afterwards: a
long-lived HTTP client on a Mac that sleeps is a reconnection problem waiting to happen.
"""

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from core.gmail import Gmail
from core.mail_crypto import mail_cipher
from core.mail_sync import SyncOutcome, sync_all
from core.mailboxes import refresh_token_for
from pipeline.settings import PipelineSettings

logger = logging.getLogger("noble_hunter.mail")

TIMEOUT_SECONDS = 30


def sync_round(engine: Engine, settings: PipelineSettings) -> dict[int, SyncOutcome]:
    """One pass over every connected mailbox. Returns each mailbox's outcome, by mailbox id."""
    problem = settings.mail_problem()
    if problem:
        logger.info("Not reading mail: %s", problem)
        return {}

    cipher = mail_cipher(settings.mail_token_key.get_secret_value())
    with httpx.Client(timeout=TIMEOUT_SECONDS) as http, Session(engine) as session:
        def open_gmail(mailbox) -> Gmail:
            return Gmail(
                http,
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret.get_secret_value(),
                refresh_token=refresh_token_for(mailbox, cipher),
            )

        outcomes = sync_all(session, open_gmail=open_gmail, now=datetime.now(UTC))
        session.commit()
    return outcomes


def describe(outcomes: dict[int, SyncOutcome]) -> str:
    """One line for the log or the terminal."""
    if not outcomes:
        return "No mailboxes to read."
    stored = sum(outcome.stored for outcome in outcomes.values())
    problems = [outcome.error for outcome in outcomes.values() if outcome.error]
    parts = [f"Read {len(outcomes)} mailbox(es); {stored} new message(s)."]
    if problems:
        parts.append(f"{len(problems)} problem(s): {problems[0]}")
    return " ".join(parts)
```

- [ ] **Step 6: Wire it into the CLI**

In `pipeline/cli.py`, pass the mail round into the runner loop in `worker_command`:

```python
        run_forever(engine, runner, stop=stop, sync_mail=lambda: sync_round(engine, settings))
```

and add the `--check` line for mail to the existing `check` branch, after the Anthropic key check:

```python
            problem = settings.mail_problem()
            typer.echo("Mail: not configured." if problem else "Mail: ready.")
```

Then add a command of its own, after `worker_command`:

```python
@app.command("mail")
def mail_command(
    check: Annotated[bool, typer.Option(help="Only check the mail settings, then exit.")] = False,
) -> None:
    """Read replies from every connected mailbox once, and put them on their digest entries."""
    try:
        settings = load_pipeline_settings()
        database_url = settings.sqlalchemy_url()
    except MissingSettingError as error:
        raise fail(str(error)) from None

    problem = settings.mail_problem()
    if problem:
        raise fail(f"Mail isn't configured: {problem}. See .env.example.")
    if check:
        typer.echo("Mail settings look good.")
        return

    engine = create_engine(database_url)
    try:
        typer.echo(describe(sync_round(engine, settings)))
    except SQLAlchemyError as error:
        raise fail(f"Database error while reading mail: {describe_db_error(error)}") from None
    finally:
        engine.dispose()
```

(import `sync_round` and `describe` from `pipeline.mail`.)

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_worker_mail.py tests/test_worker.py tests/test_cli_pipeline.py -q`

Expected: all pass. `tests/test_worker.py` must still pass untouched — `sync_mail` defaults to `None`, so a runner without mail behaves exactly as before.

- [ ] **Step 8: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add pipeline/mail.py pipeline/worker.py pipeline/settings.py pipeline/cli.py .env.example tests
git commit -m "feat: the runner reads replies every five minutes

On its own thread, separate from the nightly run: a run that takes an hour mustn't hold up
replies, and mail failing must never fail a run. `pipeline.cli mail` does one round by hand.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Mail state on the entry, and mailboxes in the runner panel

**Files:**
- Create: `tests/test_digest_view_mail.py`, `tests/test_run_status_mail.py`
- Modify: `core/digest_view.py`, `core/run_status.py`, `web/runs.py`, `web/templates/digest/_entry.html`, `web/templates/runs/_panel.html`, `web/static/css/app.css`, `tests/test_web_digest.py`

Two small things that make the feature usable rather than merely working:

1. A digest entry shows whether a reply is waiting, so working down the list doesn't mean opening every panel.
2. The runner panel says when a mailbox needs reconnecting, or when replies haven't been read for hours — the two failures that are silent otherwise.

The mail state is read **once for the whole night**, not per entry: an N+1 query here would run 20 extra statements on every digest page load.

Warnings about mailboxes are scoped with `visible_to`, like everything else. A member must never see another artist's mailbox address — which is exactly why this doesn't go through `run_status`'s admin-written warnings.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_digest_view_mail.py`:

```python
"""What the digest knows about each entry's mail."""

from datetime import UTC, date, datetime

from sqlalchemy import select

from core.digest_view import digest_view, entry_view
from core.models import EmailMessage, MailDirection
from tests.factories import (
    admin_viewer,
    make_artist,
    make_curator,
    make_mailbox,
    make_outreach,
    make_playlist,
    make_profile,
)

NIGHT = date(2026, 9, 16)
TODAY = date(2026, 9, 17)
SENT_AT = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
REPLIED_AT = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)


def an_entry(session):
    artist = make_artist(session)
    profile = make_profile(session, artist=artist)
    mailbox = make_mailbox(session, artist)
    profile.mail_account_id = mailbox.id
    curator = make_curator(session)
    outreach = make_outreach(session, curator, profile, NIGHT, playlist=make_playlist(session, curator=curator))
    outreach.mail_account_id, outreach.gmail_thread_id = mailbox.id, "thread1"
    session.flush()
    return outreach, mailbox


def add_message(session, outreach, mailbox, direction, sent_at, message_id):
    session.add(
        EmailMessage(
            outreach_id=outreach.id,
            mail_account_id=mailbox.id,
            direction=direction,
            gmail_message_id=message_id,
            gmail_thread_id="thread1",
            from_address="a@b.com",
            to_address="c@d.com",
            subject="Kelvin",
            body_text="…",
            sent_at=sent_at,
        )
    )
    session.flush()


def only_entry(session):
    view = digest_view(session, NIGHT, today=TODAY, viewer=admin_viewer())
    return view.profiles[0].entries[0]


def test_an_entry_with_no_mail_is_waiting_on_nobody(session):
    an_entry(session)

    entry = only_entry(session)

    assert (entry.mail.sent, entry.mail.received) == (0, 0)
    assert entry.mail.waiting_on_you is False


def test_a_pitch_we_sent_is_counted(session):
    outreach, mailbox = an_entry(session)
    add_message(session, outreach, mailbox, MailDirection.OUT, SENT_AT, "m1")

    entry = only_entry(session)

    assert (entry.mail.sent, entry.mail.received) == (1, 0)
    assert entry.mail.waiting_on_you is False
    assert entry.mail.last_at == SENT_AT


def test_a_reply_means_it_is_waiting_on_you(session):
    outreach, mailbox = an_entry(session)
    add_message(session, outreach, mailbox, MailDirection.OUT, SENT_AT, "m1")
    add_message(session, outreach, mailbox, MailDirection.IN, REPLIED_AT, "m2")

    entry = only_entry(session)

    assert (entry.mail.sent, entry.mail.received) == (1, 1)
    assert entry.mail.waiting_on_you is True
    assert entry.mail.last_at == REPLIED_AT


def test_answering_the_reply_hands_it_back(session):
    outreach, mailbox = an_entry(session)
    add_message(session, outreach, mailbox, MailDirection.IN, REPLIED_AT, "m2")
    add_message(session, outreach, mailbox, MailDirection.OUT, datetime(2026, 9, 17, 9, 0, tzinfo=UTC), "m3")

    assert only_entry(session).mail.waiting_on_you is False


def test_one_entrys_mail_doesnt_leak_onto_another(session):
    first, mailbox = an_entry(session)
    second, _ = an_entry(session)
    add_message(session, first, mailbox, MailDirection.IN, REPLIED_AT, "m2")

    waiting = {
        entry.outreach_id: entry.mail.waiting_on_you
        for group in digest_view(session, NIGHT, today=TODAY, viewer=admin_viewer()).profiles
        for entry in group.entries
    }

    assert waiting[first.id] is True
    assert waiting[second.id] is False


def test_the_single_entry_view_reads_the_same_state(session):
    outreach, mailbox = an_entry(session)
    add_message(session, outreach, mailbox, MailDirection.IN, REPLIED_AT, "m2")

    assert entry_view(session, outreach.id, today=TODAY).mail.waiting_on_you is True


def test_the_night_reads_mail_in_a_fixed_number_of_queries(session):
    outreach, mailbox = an_entry(session)
    an_entry(session)
    an_entry(session)
    add_message(session, outreach, mailbox, MailDirection.IN, REPLIED_AT, "m2")
    session.commit()

    statements: list[str] = []
    with _record_statements(session, statements):
        digest_view(session, NIGHT, today=TODAY, viewer=admin_viewer())

    # Two: one for the counts, one for who spoke last. Three entries, still two -- never per entry.
    assert sum("email_messages" in statement for statement in statements) == 2
```

For `_record_statements`, use SQLAlchemy's `before_cursor_execute` event on `session.bind`:

```python
import contextlib

from sqlalchemy import event


@contextlib.contextmanager
def _record_statements(session, into: list[str]):
    def record(conn, cursor, statement, parameters, context, executemany):
        into.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", record)
```

Create `tests/test_run_status_mail.py`:

```python
"""What the runner panel says about mailboxes."""

from datetime import UTC, datetime, timedelta

from core.run_status import mail_warnings
from tests.factories import admin_viewer, make_artist, make_mailbox, member_viewer

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def test_nothing_to_say_when_there_are_no_mailboxes(session):
    assert mail_warnings(session, admin_viewer(), NOW) == ()


def test_a_freshly_read_mailbox_says_nothing(session):
    make_mailbox(session, make_artist(session), last_checked_at=NOW - timedelta(minutes=5))

    assert mail_warnings(session, admin_viewer(), NOW) == ()


def test_a_mailbox_needing_reconnection_is_named(session):
    artist = make_artist(session)
    make_mailbox(session, artist, address="synman@gmail.com", needs_reconnect=True)

    warnings = mail_warnings(session, admin_viewer(), NOW)

    assert any("synman@gmail.com" in warning and "needs reconnecting" in warning for warning in warnings)


def test_replies_going_unread_is_said_once(session):
    artist = make_artist(session)
    make_mailbox(session, artist, address="a@gmail.com", last_checked_at=NOW - timedelta(hours=9))
    make_mailbox(session, artist, address="b@gmail.com", last_checked_at=NOW - timedelta(hours=9))

    warnings = mail_warnings(session, admin_viewer(), NOW)

    assert sum("replies" in warning.lower() for warning in warnings) == 1


def test_a_mailbox_never_read_counts_as_unread(session):
    make_mailbox(session, make_artist(session), last_checked_at=None)

    assert any("replies" in warning.lower() for warning in mail_warnings(session, admin_viewer(), NOW))


def test_a_member_never_hears_about_another_artists_mailbox(session):
    mine = make_artist(session)
    theirs = make_artist(session)
    make_mailbox(session, theirs, address="theirs@gmail.com", needs_reconnect=True)

    warnings = mail_warnings(session, member_viewer(mine), NOW)

    assert warnings == ()


def test_a_member_hears_about_their_own(session):
    mine = make_artist(session)
    make_mailbox(session, mine, address="mine@gmail.com", needs_reconnect=True)

    assert any("mine@gmail.com" in warning for warning in mail_warnings(session, member_viewer(mine), NOW))


def test_a_disconnected_mailbox_is_not_warned_about(session):
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist, last_checked_at=None)
    mailbox.refresh_token_encrypted, mailbox.disconnected_at = None, NOW
    session.flush()

    assert mail_warnings(session, admin_viewer(), NOW) == ()
```

- [ ] **Step 2: Run them to watch them fail**

Run: `uv run pytest tests/test_digest_view_mail.py tests/test_run_status_mail.py -q`

Expected: `AttributeError: 'EntryView' object has no attribute 'mail'`, and `ImportError: cannot import name 'mail_warnings'`.

- [ ] **Step 3: Add the mail state to `core/digest_view.py`**

Add the dataclass, above `EntryView`:

```python
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
```

Add `mail: EntryMail = NO_MAIL` as the last field of `EntryView`, and import `EmailMessage` and `MailDirection` from `core.models`.

Read the night's mail in one query:

```python
def _mail_by_entry(session: Session, outreach_ids: list[int]) -> dict[int, EntryMail]:
    """Every entry's mail state, in one query. Entries with no mail simply aren't in the result."""
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
```

(`from dataclasses import dataclass, replace`.)

That is two queries: one for the counts, one for who spoke last. They could be folded into one with `DISTINCT ON` and a window function, and deliberately aren't — two plain queries per page read better than one clever one, and the thing worth preventing (a query *per entry*) is prevented either way. The test counts two for exactly this reason.

Then pass the state in: in `_profiles`, collect the rows first, call `_mail_by_entry` once with every outreach id, and hand each `_entry` its state:

```python
def _entry(session, outreach, profile, playlist, curator, *, today, mail=NO_MAIL) -> EntryView:
```

with `mail=mail` set on the returned `EntryView`, and `entry_view` calling `_mail_by_entry(session, [outreach_id])` for its one entry.

- [ ] **Step 4: Add the mailbox warnings to `core/run_status.py`**

```python
MAIL_UNREAD_AFTER = timedelta(hours=6)


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
```

(import `Viewer` and `visible_to` from `core.access`, and `MailAccount` from `core.models`.)

In `web/runs.py` `panel_context`, add `"mail_warnings": mail_warnings(db, viewer, now),` and import it. In `web/templates/runs/_panel.html`, after the existing warnings loop:

```html
  {% for warning in mail_warnings %}
  <p class="notice notice--warning">{{ warning }}</p>
  {% endfor %}
```

- [ ] **Step 5: Show it on the entry**

In `web/templates/digest/_entry.html`, add to the meta line, after the reference artists span:

```html
    {% if entry.mail.waiting_on_you %}<span class="tag tag--reply">Reply waiting</span>
    {% elif entry.mail.sent %}<span>pitched by email</span>{% endif %}
```

and make the disclosure say what's inside:

```html
    <summary class="digest-entry__pitch-summary">
      {% if entry.mail.waiting_on_you %}Read the reply{% elif entry.mail.started %}The conversation{% else %}Write a pitch{% endif %}
    </summary>
```

Append to `web/static/css/app.css`:

```css
.tag--reply {
  background: var(--accent-soft);
  color: var(--accent-strong);
}
```

(again: use the names this file already defines.)

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_digest_view_mail.py tests/test_run_status_mail.py tests/test_web_digest.py tests/test_digest_view.py tests/test_run_status.py -q`

Expected: all pass. `test_digest_view.py` and `test_run_status.py` must pass untouched — `EntryMail` defaults to empty and `mail_warnings` is additive.

- [ ] **Step 7: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add core/digest_view.py core/run_status.py web/runs.py web/templates web/static/css/app.css tests
git commit -m "feat: show mail state on entries and mailbox trouble on the panel

A waiting reply is visible without opening anything, and the two silent failures -- a mailbox
needing reconnection, replies going unread -- now say so. Mail state is derived, not a column.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: The Inbox

**Files:**
- Create: `core/inbox_view.py`, `web/inbox.py`, `web/templates/inbox/page.html`, `web/templates/inbox/_list.html`, `tests/test_inbox_view.py`, `tests/test_web_inbox.py`
- Modify: `web/app.py`, `web/templates/base.html`, `web/static/css/app.css`, `tests/route_walk.py`, `tests/access_world.py`

The digest answers "who should I write to tonight". The Inbox answers the other question: "who is waiting on me". One page, every conversation the viewer can see, the ones waiting on them first.

"Check now" reads the viewer's own mailboxes there and then, for the moment when someone is waiting on a reply and doesn't want to wait five minutes for the runner. It is best-effort: a Vercel function has seconds, so it syncs the viewer's mailboxes and says what it found.

- [ ] **Step 1: Add the routes to the walk**

In `tests/route_walk.py`, add to `ROUTES`:

```python
    ("GET", "/inbox"): OK,
    ("POST", "/inbox/check"): OK,
```

Both are `OK` because neither takes an id: an outsider gets *their own* inbox, which the sweeps then check is empty of another artist's conversations. There's no FORMS entry: `/inbox/check` posts nothing.

In `tests/access_world.py`, make the stub from Task 9 refuse only sends, so a legitimate "Check now" in the control walk doesn't blow up:

```python
class _RefusingGmail:
    """Reading is allowed and finds nothing; a send reaching this in a walk is a leak."""

    def history_since(self, history_id: str):
        return [], history_id

    def profile(self):
        return "walk@example.com", "1"

    def message(self, message_id: str) -> dict:
        raise AssertionError(f"An access walk fetched message {message_id}")

    def thread(self, thread_id: str) -> dict:
        return {"messages": []}

    def send(self, raw: str, *, thread_id: str | None = None):
        raise AssertionError("An access walk reached Gmail; a send leaked past require_outreach")
```

- [ ] **Step 2: Write the failing view test**

Create `tests/test_inbox_view.py`:

```python
"""Every conversation the viewer can see, the ones waiting on them first."""

from datetime import UTC, date, datetime, timedelta

from core.inbox_view import inbox_view
from core.models import EmailMessage, MailDirection
from core.pitches import mark_thread_read
from tests.factories import (
    admin_viewer,
    make_artist,
    make_curator,
    make_mailbox,
    make_outreach,
    make_playlist,
    make_profile,
    member_viewer,
)

NIGHT = date(2026, 9, 16)
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def a_conversation(session, *, artist=None, curator_name="Nina", messages=()):
    artist = artist or make_artist(session)
    profile = make_profile(session, artist=artist)
    mailbox = make_mailbox(session, artist)
    profile.mail_account_id = mailbox.id
    curator = make_curator(session, display_name=curator_name)
    outreach = make_outreach(session, curator, profile, NIGHT, playlist=make_playlist(session, curator=curator))
    outreach.mail_account_id, outreach.gmail_thread_id = mailbox.id, f"thread{outreach.id}"
    session.flush()
    for index, (direction, sent_at, body) in enumerate(messages):
        session.add(
            EmailMessage(
                outreach_id=outreach.id,
                mail_account_id=mailbox.id,
                direction=direction,
                gmail_message_id=f"{outreach.id}-{index}",
                gmail_thread_id=outreach.gmail_thread_id,
                from_address="a@b.com",
                to_address="c@d.com",
                subject="Kelvin",
                body_text=body,
                sent_at=sent_at,
            )
        )
    session.flush()
    return outreach


def days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def test_an_entry_never_pitched_is_not_a_conversation(session):
    a_conversation(session)

    assert inbox_view(session, admin_viewer(), now=NOW).conversations == ()


def test_a_sent_pitch_is_a_conversation_waiting_on_them(session):
    a_conversation(session, messages=[(MailDirection.OUT, days_ago(2), "Hi Nina")])

    conversation = inbox_view(session, admin_viewer(), now=NOW).conversations[0]

    assert conversation.waiting_on_you is False
    assert conversation.curator_name == "Nina"
    assert conversation.last_snippet == "Hi Nina"
    assert conversation.days_since_last == 2


def test_a_reply_puts_it_at_the_top(session):
    a_conversation(session, curator_name="Quiet", messages=[(MailDirection.OUT, days_ago(1), "Hi")])
    a_conversation(
        session,
        curator_name="Replied",
        messages=[(MailDirection.OUT, days_ago(5), "Hi"), (MailDirection.IN, days_ago(4), "Send it")],
    )

    names = [c.curator_name for c in inbox_view(session, admin_viewer(), now=NOW).conversations]

    assert names == ["Replied", "Quiet"]


def test_within_a_group_the_most_recent_comes_first(session):
    a_conversation(session, curator_name="Older", messages=[(MailDirection.OUT, days_ago(9), "Hi")])
    a_conversation(session, curator_name="Newer", messages=[(MailDirection.OUT, days_ago(1), "Hi")])

    names = [c.curator_name for c in inbox_view(session, admin_viewer(), now=NOW).conversations]

    assert names == ["Newer", "Older"]


def test_a_member_sees_only_their_own_artists_conversations(session):
    mine = make_artist(session)
    theirs = make_artist(session)
    a_conversation(session, artist=mine, curator_name="Mine", messages=[(MailDirection.IN, days_ago(1), "Hi")])
    a_conversation(session, artist=theirs, curator_name="Theirs", messages=[(MailDirection.IN, days_ago(1), "Hi")])

    view = inbox_view(session, member_viewer(mine), now=NOW)

    assert [c.curator_name for c in view.conversations] == ["Mine"]


def test_it_counts_how_many_are_waiting_on_you(session):
    a_conversation(session, messages=[(MailDirection.IN, days_ago(1), "Send it")])
    a_conversation(session, messages=[(MailDirection.OUT, days_ago(1), "Hi")])

    view = inbox_view(session, admin_viewer(), now=NOW)

    assert view.waiting == 1
    assert view.total == 2


def test_unread_counts_what_they_sent_and_you_havent_opened(session):
    a_conversation(
        session,
        messages=[(MailDirection.OUT, days_ago(2), "Hi"), (MailDirection.IN, days_ago(1), "Send it")],
    )

    view = inbox_view(session, admin_viewer(), now=NOW)

    assert view.conversations[0].unread == 1
    assert view.unread == 1


def test_opening_the_thread_clears_the_unread_count(session):
    outreach = a_conversation(session, messages=[(MailDirection.IN, days_ago(1), "Send it")])
    mark_thread_read(session, outreach, now=NOW)

    assert inbox_view(session, admin_viewer(), now=NOW).unread == 0


def test_the_filter_narrows_to_one_profile(session):
    artist = make_artist(session)
    mine = a_conversation(
        session, artist=artist, curator_name="Mine", messages=[(MailDirection.OUT, days_ago(1), "Hi")]
    )
    a_conversation(
        session, artist=artist, curator_name="Other", messages=[(MailDirection.OUT, days_ago(1), "Hi")]
    )

    view = inbox_view(session, admin_viewer(), now=NOW, profile_id=mine.profile_id)

    assert [c.curator_name for c in view.conversations] == ["Mine"]
    assert len(view.profiles) == 2  # the chips still offer both
    assert view.chosen_profile_id == mine.profile_id


def test_a_long_reply_is_shown_as_a_snippet(session):
    a_conversation(session, messages=[(MailDirection.IN, days_ago(1), "y" * 400)])

    snippet = inbox_view(session, admin_viewer(), now=NOW).conversations[0].last_snippet

    assert len(snippet) < 200
    assert snippet.endswith("…")


def test_the_artist_is_named_when_the_viewer_has_more_than_one(session):
    first, second = make_artist(session, "First"), make_artist(session, "Second")
    a_conversation(session, artist=first, messages=[(MailDirection.OUT, days_ago(1), "Hi")])
    a_conversation(session, artist=second, messages=[(MailDirection.OUT, days_ago(1), "Hi")])

    assert inbox_view(session, member_viewer(first, second), now=NOW).show_artists is True
    assert inbox_view(session, member_viewer(first), now=NOW).show_artists is False
```

- [ ] **Step 3: Write `core/inbox_view.py`**

```python
"""The Inbox: every conversation the viewer can see, the ones waiting on them first.

The digest asks "who should I write to tonight". This asks the other question -- "who is waiting
on me" -- and it's the page Jarred lives in once pitching is under way.

A conversation is an outreach entry with at least one message. Whose turn it is comes from the
direction of the last message, exactly as on the digest entry: derived, never a status column,
so a reply arriving overnight needs nothing to have been updated in the right order.

Every query is scoped with `visible_to`, so a member's Inbox can only ever hold their own.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.access import Viewer, visible_to
from core.models import Artist, Curator, EmailMessage, MailDirection, Outreach, Playlist, Profile

SNIPPET_LENGTH = 160


@dataclass(frozen=True)
class ConversationView:
    outreach_id: int
    profile_id: int
    artist_name: str
    profile_name: str
    curator_name: str
    playlist_name: str
    waiting_on_you: bool
    last_at: datetime
    days_since_last: int
    last_snippet: str
    sent: int
    received: int
    unread: int


@dataclass(frozen=True)
class InboxView:
    conversations: tuple[ConversationView, ...]
    profiles: tuple[tuple[int, str], ...] = ()  # (id, name) of every profile with a conversation
    chosen_profile_id: int | None = None
    show_artists: bool = False

    @property
    def total(self) -> int:
        return len(self.conversations)

    @property
    def waiting(self) -> int:
        return sum(1 for conversation in self.conversations if conversation.waiting_on_you)

    @property
    def unread(self) -> int:
        return sum(conversation.unread for conversation in self.conversations)


def inbox_view(
    session: Session, viewer: Viewer, *, now: datetime, profile_id: int | None = None
) -> InboxView:
    """Every conversation this viewer can see, waiting-on-you first, then most recent.

    `profile_id` narrows the list to one profile. The caller checks it with `require_profile`
    first, so another artist's id is a 404 long before it reaches this query.
    """
    last = _last_message_per_entry(session, viewer)
    counts = _counts_per_entry(session, list(last))
    rows = session.execute(
        select(Outreach.id, Profile.id, Artist.name, Profile.name, Curator.display_name, Playlist.name)
        .join(Profile, Profile.id == Outreach.profile_id)
        .join(Artist, Artist.id == Profile.artist_id)
        .join(Curator, Curator.id == Outreach.curator_id)
        .join(Playlist, Playlist.spotify_id == Outreach.playlist_id)
        .where(Outreach.id.in_(last), visible_to(viewer, Profile.artist_id))
    )
    everything = [
        ConversationView(
            outreach_id=outreach_id,
            profile_id=owning_profile_id,
            artist_name=artist_name,
            profile_name=profile_name,
            curator_name=curator_name,
            playlist_name=playlist_name,
            waiting_on_you=last[outreach_id].direction == MailDirection.IN,
            last_at=last[outreach_id].sent_at,
            days_since_last=max(0, (now - last[outreach_id].sent_at).days),
            last_snippet=_snippet(last[outreach_id].body_text),
            sent=counts[outreach_id][0],
            received=counts[outreach_id][1],
            unread=counts[outreach_id][2],
        )
        for outreach_id, owning_profile_id, artist_name, profile_name, curator_name, playlist_name in rows
    ]
    # The filter chips list every profile that has a conversation, even the one being filtered out.
    profiles = sorted({(c.profile_id, c.profile_name) for c in everything}, key=lambda p: p[1].casefold())
    chosen = [c for c in everything if profile_id is None or c.profile_id == profile_id]
    chosen.sort(key=lambda c: (not c.waiting_on_you, -c.last_at.timestamp(), c.outreach_id))
    return InboxView(
        conversations=tuple(chosen),
        profiles=tuple(profiles),
        chosen_profile_id=profile_id,
        show_artists=viewer.is_admin or len(viewer.artist_ids) > 1,
    )


@dataclass(frozen=True)
class _LastMessage:
    direction: str
    sent_at: datetime
    body_text: str


def _last_message_per_entry(session: Session, viewer: Viewer) -> dict[int, _LastMessage]:
    """The newest message on each visible conversation, one row per entry."""
    rows = session.execute(
        select(EmailMessage.outreach_id, EmailMessage.direction, EmailMessage.sent_at, EmailMessage.body_text)
        .join(Outreach, Outreach.id == EmailMessage.outreach_id)
        .join(Profile, Profile.id == Outreach.profile_id)
        .where(visible_to(viewer, Profile.artist_id))
        .order_by(EmailMessage.outreach_id, EmailMessage.sent_at.desc(), EmailMessage.id.desc())
        .distinct(EmailMessage.outreach_id)
    )
    return {
        outreach_id: _LastMessage(direction, sent_at, body_text)
        for outreach_id, direction, sent_at, body_text in rows
    }


def _counts_per_entry(session: Session, outreach_ids: list[int]) -> dict[int, tuple[int, int, int]]:
    """Sent, received and still-unread counts per conversation, in one query."""
    if not outreach_ids:
        return {}
    rows = session.execute(
        select(
            EmailMessage.outreach_id,
            func.count().filter(EmailMessage.direction == MailDirection.OUT),
            func.count().filter(EmailMessage.direction == MailDirection.IN),
            func.count().filter(
                EmailMessage.direction == MailDirection.IN, EmailMessage.read_at.is_(None)
            ),
        )
        .where(EmailMessage.outreach_id.in_(outreach_ids))
        .group_by(EmailMessage.outreach_id)
    )
    return {outreach_id: (sent, received, unread) for outreach_id, sent, received, unread in rows}


def _snippet(body: str) -> str:
    text = " ".join((body or "").split())
    return text if len(text) <= SNIPPET_LENGTH else text[: SNIPPET_LENGTH - 1].rstrip() + "…"
```

- [ ] **Step 4: Write the failing page test**

Create `tests/test_web_inbox.py`:

```python
"""The Inbox page, and reading the mail from it."""

from datetime import date

import pytest
from cryptography.fernet import Fernet

from core.models import EmailMessage, MailDirection
from tests.factories import (
    make_artist,
    make_curator,
    make_mailbox,
    make_member,
    make_outreach,
    make_playlist,
    make_profile,
    make_user,
)
from tests.web_helpers import csrf_token, member_client, web_settings

KEY = Fernet.generate_key().decode()
HTML = {"accept": "text/html"}
NIGHT = date(2026, 9, 16)


@pytest.fixture
def mail_settings():
    return web_settings().model_copy(update={"mail_token_key": KEY})


class FakeReadingGmail:
    """Stands in for a mailbox being read: one new message, or nothing."""

    def __init__(self, history=()):
        self.history = list(history)

    def history_since(self, history_id: str):
        return list(self.history), "2000"

    def profile(self):
        return "synman@gmail.com", "2000"

    def message(self, message_id: str) -> dict:
        raise AssertionError("this test shouldn't need to fetch a message")

    def thread(self, thread_id: str) -> dict:
        return {"messages": []}


def a_pitched_conversation(session, *, replied: bool):
    artist = make_artist(session, "Synman")
    profile = make_profile(session, "IDM Playlists", artist=artist)
    make_member(session, artist, make_user(session, "nik@example.com"))
    mailbox = make_mailbox(session, artist, address="synman@gmail.com")
    profile.mail_account_id = mailbox.id
    curator = make_curator(session, display_name="Nina")
    outreach = make_outreach(session, curator, profile, NIGHT, playlist=make_playlist(session, curator=curator))
    outreach.mail_account_id, outreach.gmail_thread_id = mailbox.id, "thread1"
    session.flush()
    directions = [MailDirection.OUT] + ([MailDirection.IN] if replied else [])
    for index, direction in enumerate(directions):
        session.add(
            EmailMessage(
                outreach_id=outreach.id,
                mail_account_id=mailbox.id,
                direction=direction,
                gmail_message_id=f"m{index}",
                gmail_thread_id="thread1",
                from_address="a@b.com",
                to_address="c@d.com",
                subject="Kelvin",
                body_text="Send it over." if direction == MailDirection.IN else "Hi Nina",
                sent_at=outreach.created_at,
            )
        )
    session.flush()
    return outreach


def a_client(session, settings, gmail=None):
    return member_client(
        session,
        "nik@example.com",
        settings=settings,
        gmail_for=lambda mailbox, cipher, app_settings, http: gmail or FakeReadingGmail(),
    )


def test_it_lists_the_conversations_waiting_on_you(session, mail_settings):
    a_pitched_conversation(session, replied=True)
    client = a_client(session, mail_settings)

    html = client.get("/inbox", headers=HTML).text

    assert "Nina" in html
    assert "Send it over." in html
    assert "Waiting on you" in html


def test_an_empty_inbox_says_so(session, mail_settings):
    artist = make_artist(session, "Synman")
    make_member(session, artist, make_user(session, "nik@example.com"))
    client = a_client(session, mail_settings)

    html = client.get("/inbox", headers=HTML).text

    assert "No conversations yet" in html


def test_another_artists_conversation_is_not_listed(session, mail_settings):
    a_pitched_conversation(session, replied=True)
    theirs = make_artist(session, "Someone Else")
    other_profile = make_profile(session, artist=theirs)
    make_outreach(session, make_curator(session, display_name="Hidden"), other_profile, NIGHT)
    client = a_client(session, mail_settings)

    assert "Hidden" not in client.get("/inbox", headers=HTML).text


def test_another_artists_profile_filter_is_not_found(session, mail_settings):
    a_pitched_conversation(session, replied=True)
    theirs = make_profile(session, artist=make_artist(session, "Someone Else"))
    client = a_client(session, mail_settings)

    assert client.get(f"/inbox?profile={theirs.id}", headers=HTML).status_code == 404


def test_the_filter_keeps_your_own_profile(session, mail_settings):
    outreach = a_pitched_conversation(session, replied=True)
    client = a_client(session, mail_settings)

    html = client.get(f"/inbox?profile={outreach.profile_id}", headers=HTML).text

    assert "Nina" in html


def test_check_now_reads_the_mailbox_and_reports(session, mail_settings):
    a_pitched_conversation(session, replied=False)
    client = a_client(session, mail_settings)

    response = client.post(
        "/inbox/check", headers={"x-csrf-token": csrf_token(client), "hx-request": "true"}
    )

    assert response.status_code == 200
    assert "No new replies" in response.text


def test_check_now_without_a_mailbox_says_so(session, mail_settings):
    artist = make_artist(session, "Synman")
    make_member(session, artist, make_user(session, "nik@example.com"))
    client = a_client(session, mail_settings)

    response = client.post(
        "/inbox/check", headers={"x-csrf-token": csrf_token(client), "hx-request": "true"}
    )

    assert "no mailbox" in response.text.lower()
```

- [ ] **Step 5: Write `web/inbox.py`**

```python
"""The Inbox page, and reading the mail on demand.

The runner reads every five minutes, which is right for the background but wrong for the moment
someone is waiting on a reply. "Check now" reads the viewer's own mailboxes there and then. It
is deliberately best-effort: a Vercel function has seconds, so it reads what it can, says what
it found, and leaves the rest to the runner.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.access import Viewer, require_profile, visible_to
from core.inbox_view import inbox_view
from core.mail_sync import sync_mailbox
from core.models import MailAccount
from web.access import CurrentViewer
from web.db import get_db
from web.forms import form_id
from web.mail import cipher_or_none
from web.templating import templates

logger = logging.getLogger("noble_hunter.inbox")

router = APIRouter(prefix="/inbox")
DbSession = Annotated[Session, Depends(get_db)]

CHECK_TIMEOUT_SECONDS = 15
NO_MAILBOX = "There's no mailbox to check yet. Connect one on a profile first."


@router.get("")
def page(request: Request, db: DbSession, viewer: CurrentViewer, profile: str = "") -> Response:
    chosen = form_id(profile)
    if chosen:
        require_profile(db, viewer, chosen)  # another artist's id is not found, as everywhere else
    return templates.TemplateResponse(request, "inbox/page.html", _context(db, viewer, profile_id=chosen or None))


@router.post("/check")
def check_now(request: Request, db: DbSession, viewer: CurrentViewer) -> Response:
    """Read the viewer's own mailboxes now, then re-render the list with whatever arrived."""
    mailboxes = _their_mailboxes(db, viewer)
    cipher = cipher_or_none(request)
    if not mailboxes or cipher is None:
        return templates.TemplateResponse(request, "inbox/_list.html", _context(db, viewer, notice=NO_MAILBOX))

    stored, problems = 0, 0
    now = datetime.now(UTC)
    with httpx.Client(timeout=CHECK_TIMEOUT_SECONDS) as http:
        for mailbox in mailboxes:
            try:
                gmail = request.app.state.gmail_for(mailbox, cipher, request.app.state.settings, http)
                outcome = sync_mailbox(db, mailbox, gmail, now=now)
            except Exception:
                logger.warning("Couldn't read %s from the Inbox", mailbox.address, exc_info=True)
                problems += 1
                continue
            stored += outcome.stored
            problems += 1 if outcome.error else 0
    db.commit()

    notice = f"{stored} new {'reply' if stored == 1 else 'replies'}." if stored else "No new replies."
    if problems:
        notice += " Some mailboxes couldn't be read; the runner will try again."
    return templates.TemplateResponse(request, "inbox/_list.html", _context(db, viewer, notice=notice))


def _context(
    db: Session, viewer: Viewer, notice: str | None = None, profile_id: int | None = None
) -> dict:
    view = inbox_view(db, viewer, now=datetime.now(UTC), profile_id=profile_id)
    return {"view": view, "inbox_notice": notice}


def _their_mailboxes(db: Session, viewer: Viewer) -> list[MailAccount]:
    return list(
        db.scalars(
            select(MailAccount).where(
                visible_to(viewer, MailAccount.artist_id),
                MailAccount.disconnected_at.is_(None),
                MailAccount.refresh_token_encrypted.is_not(None),
                MailAccount.needs_reconnect.is_(False),
            )
        )
    )
```

In `web/app.py`, include `inbox_router` in the `signed_in` loop. In `web/templates/base.html`, add the nav link after Digest:

```html
      <a class="site-nav__link" href="/inbox">Inbox</a>
```

- [ ] **Step 6: Write the templates**

Create `web/templates/inbox/page.html`:

```html
{% extends "base.html" %}

{% block title %}Inbox · Noble Hunter{% endblock %}

{% block content %}
<section class="page-heading rise">
  <p class="eyebrow">Inbox</p>
  <h1 class="page-heading__title">Conversations</h1>
  {% if view.total %}
  <p class="page-heading__lede">
    {{ view.waiting }} waiting on you, {{ view.total }} open in all{% if view.unread %}, {{ view.unread }} unread{% endif %}.
  </p>
  {% endif %}

  {% if view.profiles | length > 1 %}
  <nav class="inbox-filter" aria-label="Filter by profile">
    <a class="button button--small {{ 'button--primary' if not view.chosen_profile_id else 'button--ghost' }}"
       href="/inbox"{% if not view.chosen_profile_id %} aria-current="page"{% endif %}>All</a>
    {% for profile_id, profile_name in view.profiles %}
    <a class="button button--small {{ 'button--primary' if view.chosen_profile_id == profile_id else 'button--ghost' }}"
       href="/inbox?profile={{ profile_id }}"{% if view.chosen_profile_id == profile_id %} aria-current="page"{% endif %}>{{ profile_name }}</a>
    {% endfor %}
  </nav>
  {% endif %}

  <div class="form__actions">
    <button class="button button--ghost button--small" type="button" hx-post="/inbox/check"
            hx-target="#inbox-list" hx-swap="outerHTML" hx-disabled-elt="this">
      Check now
      <span class="htmx-indicator spinner" aria-hidden="true"></span>
    </button>
  </div>
</section>

{% include "inbox/_list.html" %}
{% endblock %}
```

Create `web/templates/inbox/_list.html`:

```html
{# The conversations themselves. Swapped on its own by Check now. #}
<div id="inbox-list">
  {% if inbox_notice %}<p class="notice" role="status">{{ inbox_notice }}</p>{% endif %}
  {% if not view.conversations %}
  <div class="card empty-state rise">
    <h2 class="empty-state__title">No conversations yet</h2>
    <p class="empty-state__body">Send a pitch from tonight's digest and it will appear here.</p>
  </div>
  {% else %}
  <ol class="inbox-list" role="list">
    {% for conversation in view.conversations %}
    <li class="card inbox-item{% if conversation.waiting_on_you %} inbox-item--waiting{% endif %}">
      <div class="inbox-item__header">
        <h2 class="inbox-item__title">{{ conversation.curator_name }}</h2>
        {% if conversation.unread %}
        <span class="tag tag--reply">{{ conversation.unread }} unread</span>
        {% elif conversation.waiting_on_you %}
        <span class="tag tag--reply">Waiting on you</span>
        {% endif %}
      </div>
      <p class="inbox-item__meta">
        {% if view.show_artists %}<span>{{ conversation.artist_name }}</span>{% endif %}
        <span>{{ conversation.profile_name }}</span>
        <span>{{ conversation.playlist_name }}</span>
        <span>
          {% if conversation.days_since_last == 0 %}today{% else %}{{ conversation.days_since_last }} {{ "day" if conversation.days_since_last == 1 else "days" }} ago{% endif %}
        </span>
      </p>
      <p class="inbox-item__snippet">{{ conversation.last_snippet }}</p>
      <details class="digest-entry__pitch">
        <summary class="digest-entry__pitch-summary">
          {% if conversation.waiting_on_you %}Read and reply{% else %}The conversation{% endif %}
        </summary>
        <div id="pitch-{{ conversation.outreach_id }}" hx-get="/outreach/{{ conversation.outreach_id }}/pitch"
             hx-trigger="toggle once from:closest details" hx-swap="innerHTML">
          <p class="field__hint">Loading…</p>
        </div>
      </details>
    </li>
    {% endfor %}
  </ol>
  {% endif %}
</div>
```

The panel is the same one the digest uses, which is the point: one place answers, one place sends.

Append to `web/static/css/app.css`:

```css
/* ---- Inbox --------------------------------------------------------------------------- */

.inbox-list {
  display: grid;
  gap: var(--space-4);
  margin: 0;
  padding: 0;
  list-style: none;
}

.inbox-filter {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: var(--space-3) 0;
}

.inbox-item--waiting {
  border-left: 3px solid var(--accent);
}

.inbox-item__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.inbox-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  color: var(--text-dim);
  margin: 0 0 var(--space-2);
}

.inbox-item__snippet {
  margin: 0;
}
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_inbox_view.py tests/test_web_inbox.py tests/test_web_access.py tests/test_web_access_controls.py tests/test_web_access_sweeps.py -q`

Expected: all pass. If a sweep fails because the outsider's Inbox shows another artist's conversation, the bug is a missing `visible_to` in `core/inbox_view.py`, not a wrong expectation.

- [ ] **Step 8: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add core/inbox_view.py web/inbox.py web/app.py web/templates tests web/static/css/app.css
git commit -m "feat: an Inbox for conversations waiting on you

The digest asks who to write to; this asks who is waiting. Same compose panel underneath, so
there's one place a reply is read and one place an answer is sent.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Stage 5: Conversation load

### Task 14: How many conversations are open

**Files:**
- Create: `core/conversations.py`, `tests/test_conversations.py`

Jarred, 2026-09-16: *"I'm less worried about writing 20 emails and more about having to sustain 20 conversations."* So the digest stops working to a flat nightly number and starts working to a ceiling: top the open conversations up to `open_conversation_limit`, and never hand over more than `digest_target` in one night.

Two decisions this module makes, both worth stating plainly because they're judgement calls, not facts:

- **A conversation is an emailed pitch.** An entry marked Pitched by hand, with no message on it, isn't a conversation to sustain — nobody is waiting on a reply to it. Only entries with at least one sent email count.
- **Quiet pitches stop counting.** A pitch nobody answered within `quiet_after_days` (14 by default) is over in practice, so it stops taking up a slot. A conversation where *they* spoke last never goes quiet: it's waiting on you no matter how long you leave it.

- [ ] **Step 1: Write the failing test**

Three test files need to build a conversation (this one, and the digest and research allowance tests in Task 16), so the builder goes in one place. Create `tests/conversation_helpers.py`:

```python
"""Building emailed conversations in tests: an entry with messages on it.

Shared by tests/test_conversations.py and the allowance tests in test_digest.py and
test_research.py, so all three agree about what a conversation looks like.
"""

from datetime import datetime, timedelta

from core.models import EmailMessage, MailAccount, MailDirection
from tests.factories import make_curator, make_mailbox, make_outreach, make_playlist


def pitched(session, profile, *, messages, now: datetime):
    """An outreach entry with `messages`, each given as (direction, days before `now`).

    The entry's digest date comes from `now`, so this suits any test's clock -- the digest and
    research suites each have their own, and a conversation dated in their future would be odd.
    """
    mailbox_id = profile.mail_account_id or _a_mailbox(session, profile).id
    curator = make_curator(session)
    outreach = make_outreach(
        session, curator, profile, now.date(), playlist=make_playlist(session, curator=curator)
    )
    outreach.mail_account_id = mailbox_id
    outreach.gmail_thread_id = f"thread{outreach.id}"
    session.flush()
    for index, (direction, days) in enumerate(messages):
        session.add(
            EmailMessage(
                outreach_id=outreach.id,
                mail_account_id=mailbox_id,
                direction=direction,
                gmail_message_id=f"{outreach.id}-{index}",
                gmail_thread_id=outreach.gmail_thread_id,
                from_address="a@b.com",
                to_address="c@d.com",
                subject="Kelvin",
                body_text="…",
                sent_at=now - timedelta(days=days),
            )
        )
    session.flush()
    return outreach


def open_conversation(session, profile, *, now: datetime, days_ago: int = 1):
    """The simple case: one pitch we sent, still within the quiet window."""
    return pitched(session, profile, messages=[(MailDirection.OUT, days_ago)], now=now)


def _a_mailbox(session, profile) -> MailAccount:
    mailbox = make_mailbox(session, profile.artist)
    profile.mail_account_id = mailbox.id
    session.flush()
    return mailbox
```

Then create `tests/test_conversations.py`:

```python
"""Which conversations are open, and how many new pitches tonight can take."""

from datetime import UTC, datetime

from core.conversations import conversation_load, loads_for, open_conversations
from core.models import MailDirection
from tests.conversation_helpers import pitched as _pitched
from tests.factories import make_artist, make_mailbox, make_profile

NOW = datetime(2026, 9, 20, 2, 0, tzinfo=UTC)


def a_profile(session, **settings):
    artist = make_artist(session)
    profile = make_profile(session, artist=artist)
    profile.mail_account_id = make_mailbox(session, artist).id
    for field, value in settings.items():
        setattr(profile, field, value)
    session.flush()
    return profile


def pitched(session, profile, *, messages):
    return _pitched(session, profile, messages=messages, now=NOW)


class TestWhatCounts:
    def test_a_recent_pitch_is_open(self, session):
        profile = a_profile(session)
        pitched(session, profile, messages=[(MailDirection.OUT, 2)])

        assert open_conversations(session, profile, now=NOW) == 1

    def test_an_unanswered_pitch_goes_quiet(self, session):
        profile = a_profile(session, quiet_after_days=14)
        pitched(session, profile, messages=[(MailDirection.OUT, 15)])

        assert open_conversations(session, profile, now=NOW) == 0

    def test_their_reply_keeps_it_open_however_old(self, session):
        profile = a_profile(session, quiet_after_days=14)
        pitched(session, profile, messages=[(MailDirection.OUT, 60), (MailDirection.IN, 50)])

        assert open_conversations(session, profile, now=NOW) == 1

    def test_answering_them_starts_the_clock_again(self, session):
        profile = a_profile(session, quiet_after_days=14)
        pitched(
            session,
            profile,
            messages=[(MailDirection.OUT, 60), (MailDirection.IN, 50), (MailDirection.OUT, 2)],
        )

        assert open_conversations(session, profile, now=NOW) == 1

    def test_an_entry_pitched_by_hand_is_not_a_conversation(self, session):
        profile = a_profile(session)
        pitched(session, profile, messages=[])  # marked pitched, but nothing was emailed

        assert open_conversations(session, profile, now=NOW) == 0

    def test_another_profiles_conversations_dont_count(self, session):
        mine = a_profile(session)
        theirs = a_profile(session)
        pitched(session, theirs, messages=[(MailDirection.OUT, 1)])

        assert open_conversations(session, mine, now=NOW) == 0

    def test_the_profiles_own_quiet_setting_is_used(self, session):
        patient = a_profile(session, quiet_after_days=90)
        pitched(session, patient, messages=[(MailDirection.OUT, 30)])

        assert open_conversations(session, patient, now=NOW) == 1


class TestTonightsAllowance:
    def test_an_empty_profile_may_take_its_whole_target(self, session):
        profile = a_profile(session, digest_target=10, open_conversation_limit=20)

        load = conversation_load(session, profile, now=NOW)

        assert (load.open_now, load.limit, load.allowance) == (0, 20, 10)

    def test_the_ceiling_wins_when_it_is_lower_than_the_target(self, session):
        profile = a_profile(session, digest_target=10, open_conversation_limit=12)
        for _ in range(8):
            pitched(session, profile, messages=[(MailDirection.OUT, 1)])

        assert conversation_load(session, profile, now=NOW).allowance == 4

    def test_a_full_profile_takes_nothing(self, session):
        profile = a_profile(session, digest_target=10, open_conversation_limit=2)
        for _ in range(2):
            pitched(session, profile, messages=[(MailDirection.OUT, 1)])

        assert conversation_load(session, profile, now=NOW).allowance == 0

    def test_being_over_the_ceiling_never_goes_negative(self, session):
        profile = a_profile(session, digest_target=10, open_conversation_limit=1)
        for _ in range(4):
            pitched(session, profile, messages=[(MailDirection.OUT, 1)])

        load = conversation_load(session, profile, now=NOW)

        assert (load.open_now, load.allowance) == (4, 0)

    def test_quiet_conversations_free_up_room(self, session):
        profile = a_profile(session, digest_target=10, open_conversation_limit=3, quiet_after_days=14)
        pitched(session, profile, messages=[(MailDirection.OUT, 1)])
        pitched(session, profile, messages=[(MailDirection.OUT, 40)])
        pitched(session, profile, messages=[(MailDirection.OUT, 40)])

        assert conversation_load(session, profile, now=NOW).allowance == 2


class TestEveryProfileAtOnce:
    def test_it_answers_for_several_profiles_in_one_go(self, session):
        busy = a_profile(session, digest_target=10, open_conversation_limit=5)
        quiet = a_profile(session, digest_target=10, open_conversation_limit=5)
        for _ in range(5):
            pitched(session, busy, messages=[(MailDirection.OUT, 1)])

        loads = loads_for(session, [busy, quiet], now=NOW)

        assert loads[busy.id].allowance == 0
        assert loads[quiet.id].allowance == 5

    def test_a_profile_with_no_conversations_still_gets_an_answer(self, session):
        profile = a_profile(session, digest_target=7, open_conversation_limit=20)

        assert loads_for(session, [profile], now=NOW)[profile.id].allowance == 7

    def test_it_reads_the_messages_once_for_all_profiles(self, session):
        first, second = a_profile(session), a_profile(session)
        pitched(session, first, messages=[(MailDirection.OUT, 1)])
        pitched(session, second, messages=[(MailDirection.OUT, 1)])
        session.commit()

        statements: list[str] = []
        with _record_statements(session, statements):
            loads_for(session, [first, second], now=NOW)

        assert sum("email_messages" in statement for statement in statements) == 1
```

Reuse `_record_statements` from `tests/test_digest_view_mail.py` (Task 12) — move it to `tests/query_counting.py` and import it from both, rather than copying it.

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run pytest tests/test_conversations.py -q`

Expected: `ModuleNotFoundError: No module named 'core.conversations'`.

- [ ] **Step 3: Write `core/conversations.py`**

```python
"""How many conversations a profile has open, and how many new ones tonight can start.

Jarred, 2026-09-16: writing twenty emails is easy; sustaining twenty conversations is not. So
the digest works to a ceiling rather than a flat nightly number -- top the open conversations up
to `open_conversation_limit`, and never hand over more than `digest_target` in one night.

Two judgement calls, made here so the digest, research and the page all agree:

- a conversation is an *emailed* pitch. An entry marked Pitched by hand has nobody waiting on a
  reply, so it doesn't take up a slot;
- a pitch nobody answered within `quiet_after_days` is over in practice and stops counting. A
  conversation where the curator spoke last never goes quiet -- it's waiting on you, however
  long it sits there.

Nothing here is stored: it's all derived from the messages, so a reply arriving overnight
changes the answer without anything having to be updated in the right order.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import EmailMessage, MailDirection, Outreach, Profile


@dataclass(frozen=True)
class ConversationLoad:
    """What one profile's conversation load allows tonight."""

    open_now: int
    limit: int
    target: int

    @property
    def allowance(self) -> int:
        """How many new pitches tonight may hand over: room under the ceiling, capped by the target."""
        return max(0, min(self.target, self.limit - self.open_now))

    @property
    def full(self) -> bool:
        return self.allowance == 0


def open_conversations(session: Session, profile: Profile, *, now: datetime) -> int:
    """How many of this profile's emailed pitches are still live."""
    return _open_counts(session, [profile.id], now=now).get(profile.id, 0)


def conversation_load(session: Session, profile: Profile, *, now: datetime) -> ConversationLoad:
    return loads_for(session, [profile], now=now)[profile.id]


def loads_for(
    session: Session, profiles: Sequence[Profile], *, now: datetime
) -> dict[int, ConversationLoad]:
    """Every profile's load, reading the messages once. Profiles with no mail still get an answer."""
    counts = _open_counts(session, [profile.id for profile in profiles], now=now)
    return {
        profile.id: ConversationLoad(
            open_now=counts.get(profile.id, 0),
            limit=profile.open_conversation_limit,
            target=profile.digest_target,
        )
        for profile in profiles
    }


def _open_counts(session: Session, profile_ids: Sequence[int], *, now: datetime) -> dict[int, int]:
    """Open conversations per profile, from the last message on each emailed entry.

    One query: the newest message on every entry of these profiles, with the profile's own quiet
    window applied in Python -- the window differs per profile, and there are tens of rows, not
    millions.
    """
    if not profile_ids:
        return {}
    rows = session.execute(
        select(
            Outreach.profile_id,
            Profile.quiet_after_days,
            EmailMessage.direction,
            EmailMessage.sent_at,
        )
        .join(Outreach, Outreach.id == EmailMessage.outreach_id)
        .join(Profile, Profile.id == Outreach.profile_id)
        .where(Outreach.profile_id.in_(profile_ids))
        .order_by(EmailMessage.outreach_id, EmailMessage.sent_at.desc(), EmailMessage.id.desc())
        .distinct(EmailMessage.outreach_id)
    )
    counts: dict[int, int] = {}
    for profile_id, quiet_after_days, direction, sent_at in rows:
        if _is_open(direction, sent_at, quiet_after_days, now):
            counts[profile_id] = counts.get(profile_id, 0) + 1
    return counts


def _is_open(direction: str, sent_at: datetime, quiet_after_days: int, now: datetime) -> bool:
    if direction == MailDirection.IN:
        return True  # they spoke last: waiting on you, however old
    return now - sent_at <= timedelta(days=quiet_after_days)
```

Note the `DISTINCT ON` with `ORDER BY email_messages.outreach_id, …`: Postgres requires the distinct column to lead the ordering, which is why the query selects `Outreach.profile_id` but orders by the message's `outreach_id`. `tests/test_conversations.py::TestEveryProfileAtOnce::test_it_reads_the_messages_once_for_all_profiles` is what keeps this one query.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_conversations.py -q`

Expected: 15 passed.

- [ ] **Step 5: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add core/conversations.py tests/test_conversations.py tests/conversation_helpers.py \
  tests/query_counting.py tests/test_digest_view_mail.py
git commit -m "feat: work out how many conversations are open

The ceiling Jarred asked for: sustaining twenty conversations is the real limit, not writing
twenty emails. Derived from the messages, so an overnight reply changes the answer by itself.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: The two conversation settings

**Files:**
- Create: `tests/test_profile_conversation_settings.py`
- Modify: `core/profile_rules.py`, `core/profiles.py`, `web/profiles.py`, `web/templates/profiles/_settings_form.html`, `tests/route_walk.py`, `tests/access_world.py`, `tests/test_web_profiles.py`

The migration gave every profile `open_conversation_limit` (20) and `quiet_after_days` (14). Now they become editable, through the same validated path as every other profile setting — so the YAML importer, the web form and any future caller can't disagree about what's allowed.

`_validated_settings` currently returns a three-tuple, and two more settings would make it a five-tuple nobody can read at the call site. Replace it with a small frozen dataclass in the same commit: this is exactly the "a file you're modifying has grown unwieldy" case, and it stays a ten-line change.

- [ ] **Step 1: Add the numbers**

In `core/profile_rules.py`, after the digest-target block:

```python
# Conversation load (Jarred, 2026-09-16): sustaining conversations is the real limit, not writing
# emails. A profile tops up to this many open conversations, and an unanswered pitch stops
# counting after this many days. The bounds match migration 0007's check constraints.
MIN_OPEN_CONVERSATIONS = 1
MAX_OPEN_CONVERSATIONS = 200
DEFAULT_OPEN_CONVERSATIONS = 20
MIN_QUIET_AFTER_DAYS = 1
MAX_QUIET_AFTER_DAYS = 365
DEFAULT_QUIET_AFTER_DAYS = 14
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_profile_conversation_settings.py`:

```python
"""Editing the conversation ceiling and the quiet window."""

import pytest

from core.profiles import ProfileValidationError, update_profile_settings
from tests.factories import make_profile


def settings_for(session, **overrides):
    profile = make_profile(session, "IDM Playlists")
    fields = {
        "name": profile.name,
        "digest_target": "10",
        "min_followers": "50",
        "open_conversation_limit": "20",
        "quiet_after_days": "14",
        **overrides,
    }
    return profile, fields


def test_the_defaults_are_the_ones_the_migration_set(session):
    profile = make_profile(session)

    assert (profile.open_conversation_limit, profile.quiet_after_days) == (20, 14)


def test_both_settings_can_be_changed(session):
    profile, fields = settings_for(session, open_conversation_limit="12", quiet_after_days="21")

    update_profile_settings(session, profile.id, **fields)

    assert (profile.open_conversation_limit, profile.quiet_after_days) == (12, 21)


def test_leaving_them_out_keeps_what_is_there(session):
    profile, fields = settings_for(session, open_conversation_limit="12")
    update_profile_settings(session, profile.id, **fields)

    update_profile_settings(session, profile.id, name=profile.name, digest_target="10")

    assert profile.open_conversation_limit == 12


@pytest.mark.parametrize("value", ["0", "201", "-1", "ten", "", "1.5"])
def test_an_impossible_ceiling_is_refused(session, value):
    profile, fields = settings_for(session, open_conversation_limit=value)

    with pytest.raises(ProfileValidationError) as refused:
        update_profile_settings(session, profile.id, **fields)

    assert "open_conversation_limit" in refused.value.errors


@pytest.mark.parametrize("value", ["0", "366", "-1", "forever"])
def test_an_impossible_quiet_window_is_refused(session, value):
    profile, fields = settings_for(session, quiet_after_days=value)

    with pytest.raises(ProfileValidationError) as refused:
        update_profile_settings(session, profile.id, **fields)

    assert "quiet_after_days" in refused.value.errors


def test_every_problem_is_reported_at_once(session):
    profile, fields = settings_for(session, name="", open_conversation_limit="0", quiet_after_days="0")

    with pytest.raises(ProfileValidationError) as refused:
        update_profile_settings(session, profile.id, **fields)

    assert set(refused.value.errors) == {"name", "open_conversation_limit", "quiet_after_days"}


def test_a_refused_change_leaves_the_profile_alone(session):
    profile, fields = settings_for(session, open_conversation_limit="500")

    with pytest.raises(ProfileValidationError):
        update_profile_settings(session, profile.id, **fields)

    assert profile.open_conversation_limit == 20
```

- [ ] **Step 3: Run it to watch it fail**

Run: `uv run pytest tests/test_profile_conversation_settings.py -q`

Expected: `TypeError: update_profile_settings() got an unexpected keyword argument 'open_conversation_limit'`.

- [ ] **Step 4: Widen `core/profiles.py`**

Replace the three-tuple with a dataclass, above `ProfileSummary`:

```python
@dataclass(frozen=True)
class ValidatedSettings:
    """A profile's settings, checked. `None` means "leave what's there"."""

    name: str
    digest_target: int
    min_followers: int | None = None
    open_conversation_limit: int | None = None
    quiet_after_days: int | None = None
```

`_validated_settings` becomes:

```python
def _validated_settings(
    session: Session,
    artist_id: int,
    name: str,
    digest_target: int | str,
    profile_id: int | None,
    min_followers: int | str | None = None,
    open_conversation_limit: int | str | None = None,
    quiet_after_days: int | str | None = None,
) -> ValidatedSettings:
    errors: dict[str, str] = {}
    clean_name = " ".join(str(name).split())
    if not clean_name:
        errors["name"] = "Give the profile a name"
    elif len(clean_name) > MAX_NAME_LENGTH:
        errors["name"] = f"Keep the name to {MAX_NAME_LENGTH} characters or fewer"
    elif _name_taken(session, artist_id, clean_name, profile_id):
        errors["name"] = f"A profile called “{clean_name}” already exists for this artist"

    target = _parse_digest_target(digest_target)
    if target is None:
        errors["digest_target"] = f"Choose a whole number between {MIN_DIGEST_TARGET} and {MAX_DIGEST_TARGET}"

    floor = _optional(min_followers, 0, MAX_MIN_FOLLOWERS, errors, "min_followers",
                      f"Choose a whole number of followers from 0 to {MAX_MIN_FOLLOWERS:,}")
    ceiling = _optional(open_conversation_limit, MIN_OPEN_CONVERSATIONS, MAX_OPEN_CONVERSATIONS, errors,
                        "open_conversation_limit",
                        f"Choose a whole number between {MIN_OPEN_CONVERSATIONS} and {MAX_OPEN_CONVERSATIONS}")
    quiet = _optional(quiet_after_days, MIN_QUIET_AFTER_DAYS, MAX_QUIET_AFTER_DAYS, errors,
                      "quiet_after_days",
                      f"Choose a whole number of days between {MIN_QUIET_AFTER_DAYS} and {MAX_QUIET_AFTER_DAYS}")

    if errors:
        raise ProfileValidationError(errors)
    return ValidatedSettings(clean_name, target, floor, ceiling, quiet)


def _optional(
    value: int | str | None, lowest: int, highest: int, errors: dict[str, str], field: str, message: str
) -> int | None:
    """A setting the caller may leave out entirely; `None` in, `None` out, no error."""
    if value is None:
        return None
    text = str(value).strip()
    number = int(text) if text.isdigit() else None
    if number is None or not lowest <= number <= highest:
        errors[field] = message
        return None
    return number
```

(`_parse_min_followers` is now `_optional`; delete it. Keep `_parse_digest_target`: the target is never optional. Import the six new constants. Format the calls above the way `ruff format` wants — the layout here is squeezed to fit this page.)

`update_profile_settings` becomes:

```python
def update_profile_settings(
    session: Session,
    profile_id: int,
    name: str,
    digest_target: int | str,
    min_followers: int | str | None = None,
    open_conversation_limit: int | str | None = None,
    quiet_after_days: int | str | None = None,
) -> Profile:
    """Rename and retune a profile. A setting left out keeps its current value."""
    profile = get_profile(session, profile_id)
    settings = _validated_settings(
        session,
        profile.artist_id,
        name,
        digest_target,
        profile_id,
        min_followers,
        open_conversation_limit,
        quiet_after_days,
    )
    profile.name, profile.digest_target = settings.name, settings.digest_target
    for field in ("min_followers", "open_conversation_limit", "quiet_after_days"):
        value = getattr(settings, field)
        if value is not None:
            setattr(profile, field, value)
    session.flush()
    return profile
```

and in `create_profile`, the unpacking becomes `settings = _validated_settings(...)` with `name=settings.name, digest_target=settings.digest_target`.

- [ ] **Step 5: Put them on the form**

In `web/profiles.py`, `save_settings` takes two more fields and passes them through:

```python
    open_conversation_limit: FormText = "",
    quiet_after_days: FormText = "",
```

```python
        update_profile_settings(
            db,
            profile_id,
            name,
            digest_target,
            min_followers.strip() or None,
            open_conversation_limit.strip() or None,
            quiet_after_days.strip() or None,
        )
```

with the error context's `typed` dict carrying them too, and `_settings_form` returning them:

```python
        "open_conversation_limit": profile.open_conversation_limit,
        "quiet_after_days": profile.quiet_after_days,
```

In `web/templates/profiles/_settings_form.html`, after the minimum-followers field:

```html
  <div class="field">
    <label class="field__label" for="profile-open-conversations">Conversations at once</label>
    <input class="input input--narrow" id="profile-open-conversations" name="open_conversation_limit"
           type="number" inputmode="numeric" min="1" max="200" value="{{ form.open_conversation_limit }}"
           aria-describedby="profile-open-conversations-hint{% if errors.open_conversation_limit %} profile-open-conversations-error{% endif %}"
           {% if errors.open_conversation_limit %}aria-invalid="true"{% endif %}>
    <p class="field__hint" id="profile-open-conversations-hint">
      The digest tops up to this many open email conversations. Writing the emails is easy; keeping up with the replies isn't.
    </p>
    {% if errors.open_conversation_limit %}<p class="field__error" id="profile-open-conversations-error">{{ errors.open_conversation_limit }}</p>{% endif %}
  </div>

  <div class="field">
    <label class="field__label" for="profile-quiet-days">Give up after</label>
    <input class="input input--narrow" id="profile-quiet-days" name="quiet_after_days" type="number"
           inputmode="numeric" min="1" max="365" value="{{ form.quiet_after_days }}"
           aria-describedby="profile-quiet-days-hint{% if errors.quiet_after_days %} profile-quiet-days-error{% endif %}"
           {% if errors.quiet_after_days %}aria-invalid="true"{% endif %}>
    <p class="field__hint" id="profile-quiet-days-hint">
      Days. A pitch nobody answered in this long stops taking up a slot. A conversation they replied to always counts.
    </p>
    {% if errors.quiet_after_days %}<p class="field__error" id="profile-quiet-days-error">{{ errors.quiet_after_days }}</p>{% endif %}
  </div>
```

- [ ] **Step 6: Let the access walk change them**

In `tests/route_walk.py`, extend the settings form so a leaked write really moves these too:

```python
    "/profiles/{profile_id}/settings": {
        "name": "Renamed",
        "digest_target": "10",
        "min_followers": "0",
        "open_conversation_limit": "3",
        "quiet_after_days": "3",
    },
```

In `tests/access_world.py`, add them to the profile tuple in `their_data`:

```python
        "profile": (
            profile.name,
            profile.is_active,
            profile.digest_target,
            profile.min_followers,
            profile.open_conversation_limit,
            profile.quiet_after_days,
        ),
```

In `tests/test_web_profiles.py`, add one page test beside the existing settings tests:

```python
def test_the_conversation_settings_save(session):
    profile = make_profile(session, "IDM Playlists")
    client = app_client(session)
    sign_in(client)

    response = client.post(
        f"/profiles/{profile.id}/settings",
        data={
            "name": "IDM Playlists",
            "digest_target": "10",
            "min_followers": "50",
            "open_conversation_limit": "12",
            "quiet_after_days": "21",
        },
        headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
    )

    assert response.status_code == 200
    assert (profile.open_conversation_limit, profile.quiet_after_days) == (12, 21)
```

Follow the fixtures the neighbouring tests in that file already use rather than these imports if they differ.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_profile_conversation_settings.py tests/test_profiles_core.py tests/test_web_profiles.py tests/test_profile_import.py tests/test_web_access.py -q`

Expected: all pass. `test_profiles_core.py` and `test_profile_import.py` exercise the old call shape — if either fails, it's because `_validated_settings` changed shape, and the fix is in the caller, not in the test's expectations.

- [ ] **Step 8: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add core/profile_rules.py core/profiles.py web/profiles.py web/templates/profiles/_settings_form.html tests
git commit -m "feat: make the conversation ceiling and quiet window editable

Through the same validated path as every other profile setting, so the importer and the web
form can't disagree. The five-tuple of settings became a dataclass while it was small.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Tonight's allowance

**Files:**
- Modify: `pipeline/digest.py`, `pipeline/research.py`, `core/digest_view.py`, `web/templates/digest/page.html`, `tests/test_digest.py`, `tests/test_research.py`, `tests/test_digest_view_mail.py`

This is the task the whole ceiling exists for. Two one-line gates change:

- `pipeline/digest.py`: `taken >= profile.digest_target` becomes `taken >= allowance`;
- `pipeline/research.py`: `ready >= profile.digest_target` becomes `ready >= allowance`.

Research matters as much as the digest here. Researching a curator costs about five cents; a profile with no room tonight shouldn't pay for leads it can't use. That is the difference between a $2 night and a $2 night that wasted half of it.

The allowance is worked out **once per run**, before the loop: it depends on conversations that existed when the run started, and recomputing it per candidate would be both slower and harder to reason about.

- [ ] **Step 1: Write the failing tests**

Both suites already have everything these tests need, so they go in the existing files rather than new ones: `tests/test_digest.py` has `active_profile`, `ready_lead`, `digest_tonight` and `entries_today`; `tests/test_research.py` has `active_profile`, `qualified_lead`, `research_tonight` and `FakeAgent`. The conversations come from `tests/conversation_helpers.py` (Task 14).

In `tests/test_digest.py`, add to the imports and then a class at the end of the file:

```python
from tests.conversation_helpers import open_conversation
```

```python
class TestConversationAllowance:
    """The ceiling, not the target, decides how many leads a profile gets (Jarred, 2026-09-16)."""

    def test_an_empty_profile_takes_its_whole_target(self, session):
        profile = active_profile(session, digest_target=3)
        for _ in range(5):
            ready_lead(session, profile)

        digest_tonight(session)

        assert len(entries_today(session)) == 3

    def test_open_conversations_take_room_away(self, session):
        profile = active_profile(session, digest_target=5)
        profile.open_conversation_limit = 6
        for _ in range(4):
            open_conversation(session, profile, now=NOW)
        for _ in range(5):
            ready_lead(session, profile)

        digest_tonight(session)

        assert len(entries_today(session)) == 2

    def test_a_full_profile_gets_nothing_tonight(self, session):
        profile = active_profile(session, digest_target=5)
        profile.open_conversation_limit = 2
        for _ in range(2):
            open_conversation(session, profile, now=NOW)
        ready_lead(session, profile)

        digest_tonight(session)

        assert entries_today(session) == []

    def test_quiet_conversations_give_the_room_back(self, session):
        profile = active_profile(session, digest_target=5)
        profile.open_conversation_limit, profile.quiet_after_days = 2, 14
        open_conversation(session, profile, now=NOW, days_ago=40)
        open_conversation(session, profile, now=NOW, days_ago=40)
        for _ in range(3):
            ready_lead(session, profile)

        digest_tonight(session)

        assert len(entries_today(session)) == 2

    def test_a_reply_keeps_taking_up_room_however_old(self, session):
        profile = active_profile(session, digest_target=5)
        profile.open_conversation_limit, profile.quiet_after_days = 1, 14
        pitched(
            session,
            profile,
            messages=[(MailDirection.OUT, 60), (MailDirection.IN, 50)],
            now=NOW,
        )
        ready_lead(session, profile)

        digest_tonight(session)

        assert entries_today(session) == []

    def test_one_profile_being_full_doesnt_starve_another(self, session):
        full = active_profile(session, name="Full", digest_target=5)
        full.open_conversation_limit = 1
        open_conversation(session, full, now=NOW)
        ready_lead(session, full)
        free = active_profile(session, name="Free", digest_target=2)
        for _ in range(3):
            ready_lead(session, free)

        digest_tonight(session)

        assert [entry.profile_id for entry in entries_today(session)] == [free.id, free.id]
```

(that class also needs `pitched` from `tests.conversation_helpers` and `MailDirection` from `core.models`.)

In `tests/test_research.py`, the two cases that matter — each lead needs its **own** email in the description, because `contacts.contact_key` is unique and a repeated address would be left with the first curator, quietly making later leads unreachable and the counts wrong:

```python
class TestConversationAllowance:
    def test_a_full_profile_is_not_researched(self, session):
        """A profile with no room tonight shouldn't pay five cents a lead for leads it can't use."""
        profile = active_profile(session, digest_target=5)
        profile.open_conversation_limit = 2
        for _ in range(2):
            open_conversation(session, profile, now=NOW)
        qualified_lead(session, profile, fit=0.9, description="demos@first.net")
        agent = FakeAgent()

        _, summary = research_tonight(session, agent=agent)

        assert agent.leads == []
        assert summary.researched == 0

    def test_research_stops_at_the_allowance_not_the_target(self, session):
        """Target 10, ceiling 12, 8 open: research readies 4, not 10."""
        profile = active_profile(session, digest_target=10)
        profile.open_conversation_limit = 12
        for _ in range(8):
            open_conversation(session, profile, now=NOW)
        for number in range(6):
            qualified_lead(session, profile, fit=0.9, description=f"demos{number}@first.net")

        _, summary = research_tonight(session, agent=FakeAgent())

        assert summary.reachable == 4
```

- [ ] **Step 2: Run them to watch them fail**

Run: `uv run pytest tests/test_digest.py -k Allowance -q`

Expected: FAIL — `test_open_conversations_take_room_away` hands over 5, not 2: the digest is still working to `digest_target`.

- [ ] **Step 3: Change the two gates**

In `pipeline/digest.py`, inside `build_digest`, before the loop:

```python
    allowances = {
        profile_id: load.allowance
        for profile_id, load in loads_for(session, _active_profiles(session), now=now).items()
    }
```

and the gate becomes:

```python
        if playlist.curator_id in used_curators or taken.get(profile.id, 0) >= allowances.get(profile.id, 0):
            continue
```

with, near `_entries_already_today`:

```python
def _active_profiles(session: Session) -> list[Profile]:
    return list(session.scalars(select(Profile).where(Profile.is_active.is_(True))))
```

(`from core.conversations import loads_for`.)

In `pipeline/research.py`, the same shape: build `allowances` from `loads_for(session, _active_profiles(session), now=now)` before the loop, and change the gate to `ready[profile.id] >= allowances.get(profile.id, 0)`. `run_research` already takes `now`.

Both modules now import `core.conversations`, which is deliberate: the digest, research and the page must agree about what "open" means, and there's one place that decides.

- [ ] **Step 4: Show it on the digest page**

In `core/digest_view.py`, give `ProfileDigest` two more fields:

```python
    open_conversations: int = 0
    conversation_limit: int = 0
```

In `_profiles`, once the groups are built, look the loads up in one go (`loads_for(session, profiles_in_view, now=datetime.now(UTC))` — or pass `now` down from `digest_view` if you'd rather not read the clock in two places; prefer passing it) and set both fields per group.

In `web/templates/digest/page.html`, in the group heading, after the count:

```html
      {% if group.conversation_limit %}
      <span class="digest-group__load">{{ group.open_conversations }} of {{ group.conversation_limit }} conversations open</span>
      {% endif %}
```

Add one test to `tests/test_digest_view_mail.py`:

```python
def test_the_group_says_how_many_conversations_are_open(session):
    outreach, mailbox = an_entry(session)
    add_message(session, outreach, mailbox, MailDirection.OUT, SENT_AT, "m1")

    group = digest_view(session, NIGHT, today=TODAY, viewer=admin_viewer()).profiles[0]

    assert (group.open_conversations, group.conversation_limit) == (1, 20)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_digest.py tests/test_research.py tests/test_digest_view_mail.py tests/test_web_digest.py -q`

Expected: all pass. `tests/test_digest.py` and `tests/test_research.py` exercise profiles with no conversations at all, where the allowance equals the target — they should pass untouched. If one fails because a profile is now getting nothing, check that its profile is active and that `loads_for` was given every active profile, not just the ones with mail.

- [ ] **Step 6: Run the suite, lint and commit**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
git add pipeline/digest.py pipeline/research.py core/digest_view.py web/templates/digest/page.html tests
git commit -m "feat: hand over only as many leads as there's room to talk to

The digest and research both work to the open-conversation ceiling now. Research checks it too,
so a profile with no room tonight doesn't pay five cents a lead for leads it can't use.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: Documentation, and shipping it

**Files:**
- Modify: `.env.example`, `.claude/DEVELOPER_LOGS.md`, `IMPLEMENTATION_PLAN.md`

Environment variables live in `.env.example` only — there's no README table to keep in step. The two documents that matter are the developer log (what changed and why) and the implementation plan's stage status.

> **Read this before promising Jarred a date.** `gmail.readonly` is a **restricted** scope and `gmail.send` is a **sensitive** one. The Google Cloud app is already "In production" (published 2026-09-16), but publishing is not the same as *verification*: an app using restricted scopes must pass Google's verification, which for restricted scopes can include a security assessment and takes weeks. Until it does, Google may show an "unverified app" screen or refuse the restricted scope outright.
>
> Two ways through, and the choice is Jarred's, not the builder's:
> - **Stay unverified and use it anyway.** An app's own owner can usually consent to their own project's scopes and carry on past the warning screen. Fine for Jarred and his second account; it is *not* fine for the members he wants to invite.
> - **Drop `gmail.readonly`.** Sending alone is only a sensitive scope, which is a much shorter road. Replies would then have to be read in Gmail, and the Inbox would show only what we sent — half the feature.
>
> Find out which applies **before Stage 4 is built**, by connecting one mailbox at the end of Stage 2 and watching what Google's consent screen says. That is why connecting comes first in this plan.

- [ ] **Step 1: Check `.env.example` reads as one piece**

Tasks 1 and 11 each added part of the mail section. Read the whole file and make sure it says, in one block, what these three are and who needs them:

```bash
# --- Pitching by email (migration 0007) ---------------------------------------------
# The Google Cloud OAuth client, the same one sign-in uses. Both the web app (Vercel) and
# the runner on the Mac Mini need all three: the web app sends, the runner reads replies.
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
# Encrypts the Gmail refresh tokens at rest. Generate one with:
#   uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Losing it means every mailbox must be connected again; changing it does the same.
MAIL_TOKEN_KEY=
```

Nothing else in this task touches secrets: Jarred puts the real values in `.env.local` and in Vercel himself.

- [ ] **Step 2: Write the developer log entry**

Builders add a log entry as they finish each task, so by now there are several small ones for this feature. Consolidate them into a single dated entry at the top of `.claude/DEVELOPER_LOGS.md`, in the voice the existing entries use — what changed, what problem it solved, what was decided and why — and delete the per-task fragments. Cover:

- pitching by email from inside the digest: a Claude draft Jarred edits, sent from the artist's own Gmail;
- **why a mailbox belongs to an artist, not to the app**: so a second artist can use Noble Hunter without sharing an inbox, and so two profiles for one artist can share one;
- **why sending records the verdict through `record_verdict`**: an emailed pitch and a hand-recorded one use a curator up identically, so the 90-day rule has one definition;
- **why only mail on a thread we started is ever stored**: it's Jarred's own mailbox and the rest of it is none of the app's business;
- **the conversation ceiling**, in his words — writing twenty emails is easy, sustaining twenty conversations isn't — and that research checks it too, so a full profile doesn't pay for leads it can't use;
- the Google verification question above, and which way it went.

- [ ] **Step 3: Update `IMPLEMENTATION_PLAN.md`**

Add a stage for this work with its status, in the file's existing format, and note that migration 0007 is applied. Leave the Artists-and-access stage 3 entry alone: it's still not started, and it becomes migration 0008.

- [ ] **Step 4: Commit the documentation**

```bash
uv run ruff check . && uv run ruff format --check .
git add .env.example .claude/DEVELOPER_LOGS.md IMPLEMENTATION_PLAN.md
git commit -m "docs: record what email pitching does and why

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: The ship checklist — for Jarred, not for a subagent**

**A subagent must not do any of this.** No Neon, no pushing, no `db grant` against production. Hand the branch over and stop.

The order that worked for Artists and access on 2026-09-16, adapted:

1. **Before anything**, confirm the Google consent question from the top of this task is settled. If `gmail.readonly` is refused, stop and re-plan Stage 4 rather than shipping half a feature.
2. Vercel environment variables first, and a redeploy: `MAIL_TOKEN_KEY`, and `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` if they aren't already there. **Vercel env changes need a redeploy to take effect** — that is what made the member sign-in fail last time.
3. The same three on the Mac Mini, in `.env.local`.
4. Stop the runner: `launchctl bootout gui/$(id -u)/com.noblehunter.worker` (check the label in `scripts/install-worker.sh`).
5. Note the Neon restore point: the timestamp before the migration, written down where you can find it.
6. Fast-forward merge the branch into `main` locally. Don't push yet.
7. Migrate Neon: `PGOPTIONS="-c lock_timeout=5s" uv run alembic upgrade head` (0006 → 0007), then `uv run python -m pipeline.cli db grant`. **Migrate before pushing**: Vercel deploys on push, and a deployed app against an unmigrated database breaks every page.
8. Push `main`.
9. Reinstall and start the runner; check `pipeline.cli worker --check` says the settings and database look good, and `pipeline.cli mail --check` says the mail settings do.
10. Smoke tests, signed in as Jarred:
    - `/profiles/<id>` shows the Pitch mailbox card; connect the Synman Gmail and watch Google's consent screen;
    - the digest entry opens a compose panel naming the right from- and to-addresses;
    - ask Claude for a draft, edit it, send one real pitch to **your own second address**, not a curator;
    - reply to it from that address, wait five minutes, and check it appears on the entry and in `/inbox`;
    - `/inbox` "Check now" returns without error;
    - the runs panel shows no mail warnings.
11. Only then pitch a real curator.

If anything in 10 goes wrong, the feature is inert without a connected mailbox: disconnect it on the profile and everything else in Noble Hunter carries on working.

<!-- PLAN COMPLETE -->

