"""Connecting a Gmail mailbox to a profile, sharing it, and disconnecting it."""

from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from core.mail_crypto import mail_cipher
from core.mailboxes import artist_mailboxes, refresh_token_for
from core.models import MailAccount, Profile
from tests.factories import make_artist, make_mailbox, make_member, make_profile, make_user
from tests.web_helpers import FakeGoogle, app_client, csrf_token, sign_in, web_settings

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
    # SecretStr, not a bare string: model_copy skips validation, and the app reads the key the
    # way pydantic would have stored it.
    return web_settings().model_copy(update={"mail_token_key": SecretStr(KEY)})


def client_for(session, email="nik@example.com", *, settings=None, exchange=None):
    """A signed-in member's client with mail configured and Google's half faked."""
    google = FakeGoogle()
    google.userinfo["email"] = email
    client = app_client(session, google, settings=settings, mail_exchange=exchange)
    sign_in(client)
    return client


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
    return client.post(path, data=data, headers={"x-csrf-token": csrf_token(client), "hx-request": "true"})


def _state_from(response) -> str:
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return query["state"][0]
