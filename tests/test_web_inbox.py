"""The Inbox page, and reading the mail from it."""

from datetime import date

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from core.models import Artist, EmailMessage, MailDirection, User
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
VIEWER_EMAIL = "nik@example.com"
COLLEAGUE = "kim@example.com"
STRANGER = "stranger@example.com"
FILTER_ROW = 'aria-label="Filter by person"'


@pytest.fixture
def mail_settings():
    # SecretStr, not a bare string: model_copy skips validation, so a str would reach the routes
    # and every one of them would raise on .get_secret_value().
    return web_settings().model_copy(update={"mail_token_key": SecretStr(KEY)})


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


def the_viewer(session) -> User:
    """The signed-in member's row, made once: these tests put them on every artist they build."""
    existing = session.scalar(select(User).where(User.email == VIEWER_EMAIL))
    return existing if existing is not None else make_user(session, VIEWER_EMAIL)


def person_id(session, email: str) -> int:
    return session.scalar(select(User.id).where(User.email == email))


def a_colleague(session, outreach, email: str) -> User:
    """Someone else on the same artist, so there is more than one person to filter between."""
    return make_member(session, session.get(Artist, outreach.profile.artist_id), make_user(session, email))


def a_pitched_conversation(session, *, replied: bool, artist_name="Synman", curator_name="Nina"):
    artist = make_artist(session, artist_name)
    profile = make_profile(session, "IDM Playlists", artist=artist)
    make_member(session, artist, the_viewer(session))
    mailbox = make_mailbox(session, artist, address=f"{''.join(artist_name.lower().split())}@gmail.com")
    profile.mail_account_id = mailbox.id
    curator = make_curator(session, display_name=curator_name)
    outreach = make_outreach(
        session, curator, profile, NIGHT, playlist=make_playlist(session, curator=curator)
    )
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
                body_text="Send it over." if direction == MailDirection.IN else f"Hi {curator_name}",
                sent_at=outreach.created_at,
            )
        )
    session.flush()
    return outreach


def a_client(session, settings, gmail=None):
    return member_client(
        session,
        VIEWER_EMAIL,
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
    make_member(session, artist, the_viewer(session))
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


def test_another_artists_person_filter_is_not_found(session, mail_settings):
    """An id the viewer can't see is not found, never an empty list -- that would confirm it exists."""
    a_pitched_conversation(session, replied=True)
    stranger = make_member(session, make_artist(session, "Someone Else"), make_user(session, STRANGER))
    client = a_client(session, mail_settings)

    assert client.get(f"/inbox?person={stranger.id}", headers=HTML).status_code == 404


def test_a_member_is_not_told_who_is_on_another_artist(session, mail_settings):
    a_pitched_conversation(session, replied=True)
    make_member(session, make_artist(session, "Someone Else"), make_user(session, STRANGER))
    client = a_client(session, mail_settings)

    assert STRANGER not in client.get("/inbox", headers=HTML).text


def test_the_filter_keeps_your_own_conversations(session, mail_settings):
    a_pitched_conversation(session, replied=True)
    client = a_client(session, mail_settings)

    html = client.get(f"/inbox?person={person_id(session, VIEWER_EMAIL)}", headers=HTML).text

    assert "Nina" in html


def test_the_filter_row_is_hidden_when_you_are_the_only_person(session, mail_settings):
    a_pitched_conversation(session, replied=True)
    client = a_client(session, mail_settings)

    assert FILTER_ROW not in client.get("/inbox", headers=HTML).text


def test_the_filter_row_appears_once_someone_shares_an_artist(session, mail_settings):
    outreach = a_pitched_conversation(session, replied=True)
    a_colleague(session, outreach, COLLEAGUE)
    client = a_client(session, mail_settings)

    html = client.get("/inbox", headers=HTML).text

    assert FILTER_ROW in html
    assert COLLEAGUE in html


def test_a_person_with_no_conversations_reads_as_a_fact(session, mail_settings):
    a_pitched_conversation(session, replied=True)
    quiet = make_artist(session, "Quiet Act")  # an artist that has never been pitched from
    make_member(session, quiet, the_viewer(session))
    theirs = make_member(session, quiet, make_user(session, COLLEAGUE))
    client = a_client(session, mail_settings)

    html = client.get(f"/inbox?person={theirs.id}", headers=HTML).text

    assert "No conversations for" in html
    assert "Send a pitch from tonight's digest" not in html


def test_the_counts_follow_the_filter(session, mail_settings):
    a_pitched_conversation(session, replied=True)
    other = a_pitched_conversation(session, replied=False, artist_name="Other Act", curator_name="Zed")
    theirs = a_colleague(session, other, COLLEAGUE)
    client = a_client(session, mail_settings)

    filtered = client.get(f"/inbox?person={theirs.id}", headers=HTML).text

    assert "1 waiting on you, 2 open in all" in client.get("/inbox", headers=HTML).text
    assert "0 waiting on you, 1 open in all" in filtered


def test_check_now_reads_the_mailbox_and_reports(session, mail_settings):
    a_pitched_conversation(session, replied=False)
    client = a_client(session, mail_settings)

    response = client.post("/inbox/check", headers={"x-csrf-token": csrf_token(client), "hx-request": "true"})

    assert response.status_code == 200
    assert "No new replies" in response.text
    # The check swaps only the list, so the heading's counts come back out of band with it --
    # otherwise they keep describing the Inbox as it was before the check.
    assert 'id="inbox-counts"' in response.text
    assert 'hx-swap-oob="true"' in response.text


def test_check_now_keeps_the_filter(session, mail_settings):
    """The check re-renders the list; losing the filter would jump back to All mid-task."""
    a_pitched_conversation(session, replied=True)
    other = a_pitched_conversation(session, replied=True, artist_name="Other Act", curator_name="Zed")
    theirs = a_colleague(session, other, COLLEAGUE)
    client = a_client(session, mail_settings)

    response = client.post(
        f"/inbox/check?person={theirs.id}",
        headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
    )

    assert "Zed" in response.text
    assert "Nina" not in response.text


def test_check_now_on_a_person_you_cant_see_is_not_found(session, mail_settings):
    a_pitched_conversation(session, replied=True)
    stranger = make_member(session, make_artist(session, "Someone Else"), make_user(session, STRANGER))
    client = a_client(session, mail_settings)

    response = client.post(
        f"/inbox/check?person={stranger.id}",
        headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
    )

    assert response.status_code == 404


def test_check_now_without_a_mailbox_says_so(session, mail_settings):
    artist = make_artist(session, "Synman")
    make_member(session, artist, the_viewer(session))
    client = a_client(session, mail_settings)

    response = client.post("/inbox/check", headers={"x-csrf-token": csrf_token(client), "hx-request": "true"})

    assert "no mailbox" in response.text.lower()
