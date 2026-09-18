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
    make_member,
    make_outreach,
    make_playlist,
    make_profile,
    make_user,
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
    outreach = make_outreach(
        session, curator, profile, NIGHT, playlist=make_playlist(session, curator=curator)
    )
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
    a_conversation(
        session, artist=mine, curator_name="Mine", messages=[(MailDirection.IN, days_ago(1), "Hi")]
    )
    a_conversation(
        session, artist=theirs, curator_name="Theirs", messages=[(MailDirection.IN, days_ago(1), "Hi")]
    )

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


def test_the_filter_narrows_to_the_conversations_on_that_persons_artists(session):
    mine, theirs = make_artist(session), make_artist(session)
    me = make_member(session, mine)
    make_member(session, theirs, me)  # I work on both
    them = make_member(session, theirs)  # they work on only the second
    a_conversation(
        session, artist=mine, curator_name="Mine", messages=[(MailDirection.OUT, days_ago(1), "Hi")]
    )
    a_conversation(
        session, artist=theirs, curator_name="Theirs", messages=[(MailDirection.OUT, days_ago(1), "Hi")]
    )

    view = inbox_view(session, member_viewer(mine, theirs), now=NOW, person_id=them.id)

    assert [c.curator_name for c in view.conversations] == ["Theirs"]
    assert len(view.people) == 2  # the chips still offer both
    assert view.chosen_person_id == them.id


def test_the_people_offered_are_only_those_who_share_an_artist(session):
    mine, theirs = make_artist(session), make_artist(session)
    make_member(session, mine, make_user(session, "colleague@example.com"))
    make_member(session, theirs, make_user(session, "stranger@example.com"))

    view = inbox_view(session, member_viewer(mine), now=NOW)

    assert [label for _, label in view.people] == ["colleague@example.com"]


def test_an_admin_can_filter_by_anyone(session):
    first, second = make_artist(session), make_artist(session)
    make_member(session, first, make_user(session, "one@example.com"))
    make_member(session, second, make_user(session, "two@example.com"))

    view = inbox_view(session, admin_viewer(), now=NOW)

    assert [label for _, label in view.people] == ["one@example.com", "two@example.com"]


def test_a_person_is_named_by_their_google_name_and_falls_back_to_their_email(session):
    artist = make_artist(session)
    named = make_member(session, artist, make_user(session, "kim@example.com"))
    named.name = "Kim Deal"
    make_member(session, artist, make_user(session, "nameless@example.com"))
    session.flush()

    labels = [label for _, label in inbox_view(session, member_viewer(artist), now=NOW).people]

    assert labels == ["Kim Deal", "nameless@example.com"]


def test_the_counts_follow_the_filter(session):
    mine, theirs = make_artist(session), make_artist(session)
    make_member(session, theirs, make_member(session, mine))
    them = make_member(session, theirs)
    a_conversation(session, artist=mine, messages=[(MailDirection.IN, days_ago(1), "Send it")])
    a_conversation(session, artist=theirs, messages=[(MailDirection.OUT, days_ago(1), "Hi")])

    view = inbox_view(session, member_viewer(mine, theirs), now=NOW, person_id=them.id)

    assert (view.total, view.waiting, view.unread) == (1, 0, 0)  # unfiltered it would be 2, 1, 1


def test_filtering_to_a_person_cant_widen_past_what_the_viewer_can_see(session):
    mine, hidden = make_artist(session), make_artist(session)
    both = make_member(session, mine)
    make_member(session, hidden, both)  # they also work on an artist the viewer isn't on
    a_conversation(
        session, artist=mine, curator_name="Mine", messages=[(MailDirection.OUT, days_ago(1), "Hi")]
    )
    a_conversation(
        session, artist=hidden, curator_name="Hidden", messages=[(MailDirection.OUT, days_ago(1), "Hi")]
    )

    view = inbox_view(session, member_viewer(mine), now=NOW, person_id=both.id)

    assert [c.curator_name for c in view.conversations] == ["Mine"]


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
