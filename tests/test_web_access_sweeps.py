"""The route walk again, from two more angles: someone who just lost access, and ids too big to exist.

A removed member must be stopped on every route, even one that never mentions the viewer: that
catches a router included without the signed-in dependency. And an id bigger than Postgres's
`integer` must come back as not found (or a validation error) on every route, never a 500 from
psycopg's integer-out-of-range error.
"""

from datetime import date

import pytest

from core.people import remove_member
from core.profile_contents import add_anti_signal
from tests.factories import make_artist, make_curator, make_member, make_outreach, make_profile, make_user
from tests.profile_helpers import add_contents
from tests.route_walk import (
    CHILD_ID_PARAMS,
    CHILD_ID_PATH_PARTS,
    FORBIDDEN,
    ID_PARAMS,
    NOT_FOUND,
    OUT_OF_RANGE,
    ROUTES,
    call,
    takes_an_id,
)
from tests.web_helpers import FakeSuggester, app_client, csrf_token, member_client, sign_in

NIGHT = date(2026, 9, 15)
ACCESS_DENIED = "This account is not allowed in"


def their_ids(session) -> tuple[dict, object, object]:
    """An artist with a full profile, a digest entry and a member, as the ids the walk fills in."""
    artist = make_artist(session, "Band")
    profile = add_contents(session, make_profile(session, "Band Profile", artist=artist))
    add_anti_signal(session, profile.id, "term", "lofi")
    member = make_member(session, artist, make_user(session, "band@example.com"))
    outreach = make_outreach(session, make_curator(session), profile, NIGHT)
    ids = {
        "profile_id": profile.id,
        "genre_id": profile.genres[0].id,
        "reference_artist_id": profile.reference_artists[0].id,
        "signal_id": profile.anti_signals[0].id,
        "track_id": profile.tracks[0].id,
        "term_id": profile.search_terms[0].id,
        "section": "genres",
        "day": NIGHT.isoformat(),
        "outreach_id": outreach.id,
        "artist_id": artist.id,
        "user_id": member.id,
    }
    return ids, artist, member


class TestARemovedMember:
    """Signed in, then taken off their only artist: every route turns them away."""

    @pytest.mark.parametrize(("method", "path"), sorted(ROUTES))
    def test_a_browser_request_gets_the_access_denied_page(self, session, method, path):
        ids, artist, member = their_ids(session)
        client = member_client(session, "band@example.com")
        token = csrf_token(client)  # fetched before removal, or the token page is refused too

        remove_member(session, artist.id, member.id)
        response = call(client, method, path, ids, token=token, htmx=False)

        assert response.status_code == FORBIDDEN, response.text[:300]
        assert ACCESS_DENIED in response.text

    @pytest.mark.parametrize(("method", "path"), sorted(ROUTES))
    def test_an_htmx_request_is_sent_back_to_login(self, session, method, path):
        ids, artist, member = their_ids(session)
        client = member_client(session, "band@example.com")
        token = csrf_token(client)

        remove_member(session, artist.id, member.id)
        response = call(client, method, path, ids, token=token)

        assert response.status_code == 401, response.text[:300]
        assert response.headers["hx-redirect"] == "/login"


ROUTES_WITH_IDS = sorted(route for route in ROUTES if takes_an_id(*route))
ROUTES_WITH_CHILD_IDS = sorted(
    (method, path)
    for method, path in ROUTES
    if path.startswith("/profiles/") and any(part in path for part in CHILD_ID_PATH_PARTS)
)


def admin(session):
    client = app_client(session, suggester=FakeSuggester())
    sign_in(client)
    return client


def member(session):
    make_member(session, make_artist(session, "My Artist"), make_user(session, "me@example.com"))
    return member_client(session, "me@example.com", suggester=FakeSuggester())


class TestIdsTooBigToExist:
    @pytest.mark.parametrize(("method", "path"), ROUTES_WITH_IDS)
    def test_as_an_admin_every_id_out_of_range_is_not_found_or_invalid(self, session, method, path):
        ids, *_ = their_ids(session)
        client = admin(session)
        spoiled = {**ids, **dict.fromkeys(ID_PARAMS, OUT_OF_RANGE)}

        response = call(client, method, path, spoiled, token=csrf_token(client), form_artist_id=OUT_OF_RANGE)

        assert response.status_code in (404, 422), response.text[:300]

    @pytest.mark.parametrize(("method", "path"), ROUTES_WITH_IDS)
    def test_as_a_member_every_id_out_of_range_is_not_found_invalid_or_admins_only(
        self, session, method, path
    ):
        ids, *_ = their_ids(session)
        client = member(session)
        spoiled = {**ids, **dict.fromkeys(ID_PARAMS, OUT_OF_RANGE)}

        response = call(client, method, path, spoiled, token=csrf_token(client), form_artist_id=OUT_OF_RANGE)

        expected = (FORBIDDEN,) if ROUTES[(method, path)] == FORBIDDEN else (404, 422)
        assert response.status_code in expected, response.text[:300]

    @pytest.mark.parametrize(("method", "path"), ROUTES_WITH_CHILD_IDS)
    def test_an_out_of_range_child_id_on_a_real_profile_is_not_found(self, session, method, path):
        # With the profile id spoiled too, require_profile answers first and the child lookup
        # never runs. Here the profile is real and visible, so the child id's own guard is tested.
        ids, *_ = their_ids(session)
        client = admin(session)
        spoiled = {**ids, **dict.fromkeys(CHILD_ID_PARAMS, OUT_OF_RANGE)}

        response = call(client, method, path, spoiled, token=csrf_token(client))

        assert response.status_code == NOT_FOUND, response.text[:300]
