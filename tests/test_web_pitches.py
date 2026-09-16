"""Writing, saving and sending a pitch from a digest entry."""

import base64
import re
from datetime import date

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from core.gmail import GmailError
from core.models import EmailMessage, MailDirection, Outreach, OutreachStatus
from core.pitch_writer import PitchWriterError, template_pitch
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
from tests.web_helpers import (
    FakeGmailSender,
    FakeGoogle,
    FakePitchWriter,
    app_client,
    csrf_token,
    member_client,
    sign_in,
    web_settings,
)

KEY = Fernet.generate_key().decode()
NIGHT = date(2026, 9, 16)
HTML = {"accept": "text/html"}
EMAIL = "nina@broken-machines.com"
SENDER = "burningpaper@gmail.com"
LOCAL_PART = "burningpaper"


@pytest.fixture
def mail_settings():
    # SecretStr, not a bare string: model_copy skips validation, so a str would reach the routes
    # and every one of them would raise on .get_secret_value().
    return web_settings().model_copy(update={"mail_token_key": SecretStr(KEY)})


def a_digest_entry(session, *, with_mailbox=True, with_email=True, member_email="nik@example.com"):
    artist = make_artist(session, "Synman")
    profile = make_profile(session, "IDM Playlists", artist=artist)
    make_member(session, artist, make_user(session, member_email))
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


def _client_signed_in_as(session, name, *, settings, writer):
    """A client signed in as SENDER, whom Google calls `name`.

    Not `member_client`: signing in is what writes `users.name`, so the name Google reports is
    the only way a test can choose whether the signed-in person has a name on file at all.
    """
    google = FakeGoogle()
    google.userinfo["email"] = SENDER
    google.userinfo["name"] = name
    client = app_client(
        session,
        google,
        settings=settings,
        pitch_writer=writer,
        gmail_for=lambda mailbox, cipher, app_settings, http: FakeGmailSender(),
    )
    sign_in(client)
    return client


def post(client, path, data):
    return client.post(path, data=data, headers={"x-csrf-token": csrf_token(client), "hx-request": "true"})


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
        assert "Claude didn" in response.text  # the apostrophe is escaped by Jinja
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


class TestWhoTheDraftSignsAs:
    """What the route tells Claude to sign the letter as.

    These assert on the request the *route* built, not on a hand-made one. That gap is what let
    the route send an email address's local part -- "burningpaper" -- to a real curator while
    `tests/test_pitch_writer.py`, which passes a human name, stayed green.
    """

    def test_the_signed_in_persons_name_is_who_it_signs_as(self, session, mail_settings):
        outreach = a_digest_entry(session, member_email=SENDER)
        writer = FakePitchWriter()
        client = _client_signed_in_as(session, "Jarred Cinman", settings=mail_settings, writer=writer)

        post(client, f"/outreach/{outreach.id}/pitch/write", {"instruction": "go"})

        assert writer.calls[0].sender_name == "Jarred Cinman"

    def test_with_no_name_on_file_the_pitch_signs_as_the_artist(self, session, mail_settings):
        # Google doesn't always send a name, so `users.name` can be null. Nothing is the right
        # answer here: it's what lets pitch_writer's `or artist` sign as Synman, which is a true
        # thing for a musician's cold pitch to say -- and better than a guess at his real name.
        outreach = a_digest_entry(session, member_email=SENDER)
        writer = FakePitchWriter()
        client = _client_signed_in_as(session, "", settings=mail_settings, writer=writer)

        post(client, f"/outreach/{outreach.id}/pitch/write", {"instruction": "go"})

        assert writer.calls[0].sender_name is None
        assert template_pitch(writer.calls[0]).body.strip().endswith("Synman")

    def test_an_email_fragment_never_reaches_the_curator(self, session, mail_settings):
        # The fallback draft is the least supervised text this feature can produce: it is used
        # precisely when Claude has already failed, and it goes out over Jarred's own name.
        outreach = a_digest_entry(session, member_email=SENDER)
        writer = FakePitchWriter()
        client = _client_signed_in_as(session, "", settings=mail_settings, writer=writer)

        post(client, f"/outreach/{outreach.id}/pitch/write", {"instruction": "go"})

        fallback = template_pitch(writer.calls[0])
        assert LOCAL_PART not in repr(writer.calls[0])  # nowhere in what the writer was handed
        assert LOCAL_PART not in fallback.body
        assert LOCAL_PART not in fallback.subject


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
        # The disclosure moves with the tag: a card saying "Pitched" above "Write a pitch"
        # disagrees with itself until the page is reloaded.
        assert f'id="entry-summary-{outreach.id}"' in html
        assert "The conversation" in html
        assert "Write a pitch" not in html

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
