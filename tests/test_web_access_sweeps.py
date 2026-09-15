"""The route walk again, from two more angles: someone who just lost access, and ids too big to exist.

A removed member must be stopped on every route, even one that never mentions the viewer: that
catches a router included without the signed-in dependency. And an id bigger than Postgres's
`integer` must come back as not found (or a validation error) on every route, never a 500 from
psycopg's integer-out-of-range error. Spoiling every id at once only tests whichever guard runs
first, so each id is also spoiled on its own, with the others real.
"""

import pytest

from core.people import remove_member
from tests.access_world import admin_client, build_world, outsider_client
from tests.route_walk import FORBIDDEN, FORM_ARTIST_ID, ID_PARAMS, OUT_OF_RANGE, ROUTES, call, id_positions
from tests.web_helpers import FakeSuggester, csrf_token

ACCESS_DENIED = "This account is not allowed in"
NOT_FOUND_OR_INVALID = (404, 422)


@pytest.fixture
def world(session):
    return build_world(session)


class TestARemovedMember:
    """Signed in, then taken off their only artist: every route turns them away."""

    @pytest.mark.parametrize(("method", "path"), sorted(ROUTES))
    def test_a_browser_request_gets_the_access_denied_page(self, session, world, method, path):
        client = outsider_client(session, FakeSuggester())
        token = csrf_token(client)  # fetched before removal, or the token page is refused too

        remove_member(session, world.outsider_artist_id, world.outsider_user_id)
        response = call(client, method, path, world.ids, token=token, htmx=False)

        assert response.status_code == FORBIDDEN, response.text[:300]
        assert ACCESS_DENIED in response.text

    @pytest.mark.parametrize(("method", "path"), sorted(ROUTES))
    def test_an_htmx_request_is_sent_back_to_login(self, session, world, method, path):
        client = outsider_client(session, FakeSuggester())
        token = csrf_token(client)

        remove_member(session, world.outsider_artist_id, world.outsider_user_id)
        response = call(client, method, path, world.ids, token=token)

        assert response.status_code == 401, response.text[:300]
        assert response.headers["hx-redirect"] == "/login"


ROUTES_WITH_IDS = sorted(route for route in ROUTES if id_positions(*route))
ONE_ID_AT_A_TIME = [
    (method, path, position) for method, path in ROUTES_WITH_IDS for position in id_positions(method, path)
]


def spoil_every_id(world) -> dict:
    return {**world.ids, **dict.fromkeys(ID_PARAMS, OUT_OF_RANGE)}


class TestIdsTooBigToExist:
    @pytest.mark.parametrize(("method", "path"), ROUTES_WITH_IDS)
    def test_as_an_admin_every_id_out_of_range_is_not_found_or_invalid(self, session, world, method, path):
        client = admin_client(session)

        response = call(
            client, method, path, spoil_every_id(world), token=csrf_token(client), form_artist_id=OUT_OF_RANGE
        )

        assert response.status_code in NOT_FOUND_OR_INVALID, response.text[:300]

    @pytest.mark.parametrize(("method", "path"), ROUTES_WITH_IDS)
    def test_as_a_member_every_id_out_of_range_is_not_found_invalid_or_admins_only(
        self, session, world, method, path
    ):
        client = outsider_client(session, FakeSuggester())

        response = call(
            client, method, path, spoil_every_id(world), token=csrf_token(client), form_artist_id=OUT_OF_RANGE
        )

        expected = (FORBIDDEN,) if ROUTES[(method, path)] == FORBIDDEN else NOT_FOUND_OR_INVALID
        assert response.status_code in expected, response.text[:300]

    @pytest.mark.parametrize(("method", "path", "position"), ONE_ID_AT_A_TIME)
    def test_as_an_admin_each_id_out_of_range_on_its_own_is_not_found_or_invalid(
        self, session, world, method, path, position
    ):
        # The other ids are real, and tests/test_web_access_controls.py shows the admin succeeds
        # on every write route with all of them real, so only this id's own guard can refuse.
        client = admin_client(session)
        token = csrf_token(client)

        if position == FORM_ARTIST_ID:
            # A form's artist choice is a field to correct (422); a URL id names something missing (404).
            response = call(client, method, path, world.ids, token=token, form_artist_id=OUT_OF_RANGE)
            expected = 422
        else:
            response = call(client, method, path, {**world.ids, position: OUT_OF_RANGE}, token=token)
            expected = 404

        assert response.status_code == expected, response.text[:300]
