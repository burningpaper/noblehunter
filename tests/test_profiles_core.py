"""Profile rules shared by the web app: creating, renaming, and when a profile may go live."""

import pytest

from core.profiles import (
    ProfileValidationError,
    activation_problems,
    create_profile,
    get_profile,
    list_profiles,
    set_profile_active,
    update_profile_settings,
)
from tests.factories import admin_viewer, default_artist_id
from tests.profile_helpers import add_contents


class TestCreateProfile:
    def test_new_profile_is_trimmed_inactive_with_default_target(self, session):
        profile = create_profile(session, default_artist_id(session), "  Synman  ")

        assert profile.id is not None
        assert profile.name == "Synman"
        assert profile.digest_target == 20
        assert profile.is_active is False

    def test_digest_target_accepts_form_strings(self, session):
        assert create_profile(session, default_artist_id(session), "Synman", "12").digest_target == 12

    @pytest.mark.parametrize("name", ["", "   ", "x" * 81])
    def test_invalid_names_are_rejected(self, session, name):
        with pytest.raises(ProfileValidationError) as error:
            create_profile(session, default_artist_id(session), name)

        assert "name" in error.value.errors

    @pytest.mark.parametrize("target", ["0", "51", "abc", "", "12.5"])
    def test_invalid_digest_targets_are_rejected(self, session, target):
        with pytest.raises(ProfileValidationError) as error:
            create_profile(session, default_artist_id(session), "Synman", target)

        assert "between 1 and 50" in error.value.errors["digest_target"]

    def test_duplicate_names_are_rejected_regardless_of_case(self, session):
        create_profile(session, default_artist_id(session), "Synman")

        with pytest.raises(ProfileValidationError) as error:
            create_profile(session, default_artist_id(session), "  SYNMAN ")

        assert "already" in error.value.errors["name"]

    def test_every_problem_is_reported_at_once(self, session):
        with pytest.raises(ProfileValidationError) as error:
            create_profile(session, default_artist_id(session), "", "0")

        assert set(error.value.errors) == {"name", "digest_target"}


class TestActivationProblems:
    def test_empty_profile_lists_every_missing_piece(self, session):
        problems = " | ".join(
            activation_problems(create_profile(session, default_artist_id(session), "Synman"))
        )

        assert "genre" in problems
        assert "3 reference artists" in problems
        assert "track" in problems
        assert "5 active search terms" in problems

    def test_problems_show_how_far_along_the_profile_is(self, session):
        profile = add_contents(
            session, create_profile(session, default_artist_id(session), "Synman"), artists=2, terms=1
        )

        problems = " | ".join(activation_problems(profile))

        assert "(2 so far)" in problems
        assert "(1 so far)" in problems

    def test_paused_search_terms_do_not_count(self, session):
        profile = add_contents(session, create_profile(session, default_artist_id(session), "Synman"))
        profile.search_terms[0].status = "paused"
        session.flush()

        assert any("search terms" in problem for problem in activation_problems(profile))

    def test_complete_profile_has_no_problems(self, session):
        profile = add_contents(session, create_profile(session, default_artist_id(session), "Synman"))

        assert activation_problems(profile) == []


class TestSetActive:
    def test_incomplete_profile_cannot_be_activated(self, session):
        profile = create_profile(session, default_artist_id(session), "Synman")

        with pytest.raises(ProfileValidationError) as error:
            set_profile_active(session, profile.id, True)

        assert "reference artists" in error.value.errors["active"]
        assert profile.is_active is False

    def test_complete_profile_can_be_activated(self, session):
        profile = add_contents(session, create_profile(session, default_artist_id(session), "Synman"))

        assert set_profile_active(session, profile.id, True).is_active is True

    def test_pausing_is_always_allowed(self, session):
        complete = add_contents(session, create_profile(session, default_artist_id(session), "Complete"))
        set_profile_active(session, complete.id, True)
        incomplete = create_profile(session, default_artist_id(session), "Incomplete")

        assert set_profile_active(session, complete.id, False).is_active is False
        assert set_profile_active(session, incomplete.id, False).is_active is False


class TestUpdateSettings:
    def test_rename_and_change_digest_target(self, session):
        profile = create_profile(session, default_artist_id(session), "Synman")

        updated = update_profile_settings(session, profile.id, "Synman Live", "8")

        assert (updated.name, updated.digest_target) == ("Synman Live", 8)

    def test_cannot_rename_to_another_profiles_name(self, session):
        create_profile(session, default_artist_id(session), "Synman")
        other = create_profile(session, default_artist_id(session), "Side Project")

        with pytest.raises(ProfileValidationError) as error:
            update_profile_settings(session, other.id, "synman", "20")

        assert "already" in error.value.errors["name"]

    def test_changing_only_the_case_of_its_own_name_is_fine(self, session):
        profile = create_profile(session, default_artist_id(session), "Synman")

        assert update_profile_settings(session, profile.id, "SYNMAN", "20").name == "SYNMAN"

    def test_missing_profile_raises_lookup_error(self, session):
        with pytest.raises(LookupError, match="999999"):
            update_profile_settings(session, 999999, "Nope", "20")

    def test_get_missing_profile_raises_lookup_error(self, session):
        with pytest.raises(LookupError):
            get_profile(session, 999999)


class TestListProfiles:
    def test_summaries_are_ordered_by_name_with_counts_and_readiness(self, session):
        ready = add_contents(session, create_profile(session, default_artist_id(session), "Beta"), terms=6)
        create_profile(session, default_artist_id(session), "alpha")

        summaries = list_profiles(session, admin_viewer())

        assert [s.name for s in summaries] == ["alpha", "Beta"]
        beta = summaries[1]
        assert beta.id == ready.id
        assert (beta.genre_count, beta.reference_artist_count, beta.track_count, beta.active_term_count) == (
            1,
            3,
            1,
            6,
        )
        assert beta.ready is True
        assert summaries[0].ready is False
