"""The Inbox page, and reading the mail from it."""

from datetime import date

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

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


def a_pitched_conversation(session, *, replied: bool):
    artist = make_artist(session, "Synman")
    profile = make_profile(session, "IDM Playlists", artist=artist)
    make_member(session, artist, make_user(session, "nik@example.com"))
    mailbox = make_mailbox(session, artist, address="synman@gmail.com")
    profile.mail_account_id = mailbox.id
    curator = make_curator(session, display_name="Nina")
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

    response = client.post("/inbox/check", headers={"x-csrf-token": csrf_token(client), "hx-request": "true"})

    assert response.status_code == 200
    assert "No new replies" in response.text


def test_check_now_without_a_mailbox_says_so(session, mail_settings):
    artist = make_artist(session, "Synman")
    make_member(session, artist, make_user(session, "nik@example.com"))
    client = a_client(session, mail_settings)

    response = client.post("/inbox/check", headers={"x-csrf-token": csrf_token(client), "hx-request": "true"})

    assert "no mailbox" in response.text.lower()
