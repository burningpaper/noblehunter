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
