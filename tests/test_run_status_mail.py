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
