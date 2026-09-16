"""Which conversations are open, and how many new pitches tonight can take."""

from datetime import UTC, datetime

from core.conversations import conversation_load, loads_for, open_conversations
from core.models import MailDirection, OutreachStatus
from tests.conversation_helpers import pitched as _pitched
from tests.factories import make_artist, make_mailbox, make_profile
from tests.query_counting import record_statements

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

    def test_a_verdict_closes_it_even_though_they_spoke_last(self, session):
        # "Not for us" is the likeliest reply to a cold pitch. Without this, marking the entry
        # bad-fit leaves an inbound last message that never ages out, and the slot is held for
        # ever -- enough of them and the ceiling silently starves the digest to nothing.
        profile = a_profile(session)
        outreach = pitched(session, profile, messages=[(MailDirection.OUT, 30), (MailDirection.IN, 29)])
        assert open_conversations(session, profile, now=NOW) == 1

        outreach.status = OutreachStatus.BAD_FIT
        session.flush()

        assert open_conversations(session, profile, now=NOW) == 0

    def test_a_placed_track_is_finished_with_too(self, session):
        profile = a_profile(session)
        outreach = pitched(session, profile, messages=[(MailDirection.OUT, 1), (MailDirection.IN, 1)])

        outreach.status = OutreachStatus.PLACED
        session.flush()

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
        with record_statements(session, statements):
            loads_for(session, [first, second], now=NOW)

        assert sum("email_messages" in statement for statement in statements) == 1
