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
