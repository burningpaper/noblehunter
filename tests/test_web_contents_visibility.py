"""Editing a profile, and Ask Claude, work only on the viewer's own artists.

Every write route on `web/profile_contents.py` and `web/suggestions.py` is checked against
another artist's profile: it must answer 404 and leave that profile's contents exactly as
they were. A nonexistent profile id and another artist's profile id must look identical, so
a member can't tell "doesn't exist" from "isn't yours". Out-of-range ids (bigger than
Postgres's `integer` column can hold) must not reach the database and crash with a 500. And
the visibility check must run before any other work: before Ask Claude is asked anything, and
before a route's own validation of what it was sent.
"""

import pytest

from core.access import MAX_POSTGRES_INT
from core.models import AntiSignal, Profile, ProfileGenre, ProfileTrack, ReferenceArtist, SearchTerm
from core.profile_contents import add_anti_signal
from tests.factories import make_artist, make_member, make_profile, make_user
from tests.profile_helpers import add_contents
from tests.web_helpers import FakeSuggester, app_client, csrf_token, member_client, sign_in


def setup(session, **client_options):
    """A member of Mine, and one profile per artist, each with two of everything movable."""
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    own = add_contents(session, make_profile(session, "Own", artist=mine), genres=2)
    other = add_contents(session, make_profile(session, "Other", artist=theirs), genres=2)
    add_anti_signal(session, own.id, "artist", "Boring Band")
    add_anti_signal(session, other.id, "artist", "Boring Band")
    make_member(session, mine, make_user(session, "nik@example.com"))
    return member_client(session, "nik@example.com", **client_options), own, other


def admin_client(session):
    client = app_client(session)
    sign_in(client)
    return client


def htmx_headers(client):
    return {"x-csrf-token": csrf_token(client), "hx-request": "true"}


def html_headers(client):
    return {"x-csrf-token": csrf_token(client), "accept": "text/html"}


def post(client, path: str, data: dict, headers: dict | None = None):
    return client.post(path, data=data, headers=headers or htmx_headers(client))


def delete(client, path: str):
    return client.delete(path, headers=htmx_headers(client))


def snapshot(session, profile_id: int):
    """Everything a profile_contents write route could touch."""
    session.expire_all()
    profile = session.get(Profile, profile_id)
    genres = [
        (g.tag, g.priority)
        for g in session.query(ProfileGenre).filter_by(profile_id=profile_id).order_by(ProfileGenre.priority)
    ]
    artists = sorted(a.display_name for a in session.query(ReferenceArtist).filter_by(profile_id=profile_id))
    anti_signals = sorted(
        (s.kind, s.value) for s in session.query(AntiSignal).filter_by(profile_id=profile_id)
    )
    tracks = sorted(
        (t.title, t.spotify_url, t.description)
        for t in session.query(ProfileTrack).filter_by(profile_id=profile_id)
    )
    terms = sorted(
        (t.term, t.status, t.origin) for t in session.query(SearchTerm).filter_by(profile_id=profile_id)
    )
    return (profile.name, profile.is_active, genres, artists, anti_signals, tracks, terms)


def _add_genre(other):
    return "post", f"/profiles/{other.id}/genres", {"tag": "Sneaky"}


def _move_genre(other):
    # Move the higher-priority genre up, so a real move (if it happened) would swap the pair.
    movable = max(other.genres, key=lambda genre: genre.priority)
    return "post", f"/profiles/{other.id}/genres/{movable.id}/move", {"direction": "up"}


def _remove_genre(other):
    genre = other.genres[0]
    return "delete", f"/profiles/{other.id}/genres/{genre.id}", None


def _add_artist(other):
    return "post", f"/profiles/{other.id}/artists", {"name": "Sneaky Artist"}


def _remove_artist(other):
    artist = other.reference_artists[0]
    return "delete", f"/profiles/{other.id}/artists/{artist.id}", None


def _add_anti_signal(other):
    return "post", f"/profiles/{other.id}/anti-signals", {"kind": "artist", "value": "Sneaky Signal"}


def _remove_anti_signal(other):
    signal = other.anti_signals[0]
    return "delete", f"/profiles/{other.id}/anti-signals/{signal.id}", None


def _add_track(other):
    return (
        "post",
        f"/profiles/{other.id}/tracks",
        {"title": "Sneaky Track", "spotify_url": "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQD"},
    )


def _remove_track(other):
    track = other.tracks[0]
    return "delete", f"/profiles/{other.id}/tracks/{track.id}", None


def _add_term(other):
    return "post", f"/profiles/{other.id}/terms", {"term": "sneaky term"}


def _set_term_status(other):
    term = other.search_terms[0]
    return "post", f"/profiles/{other.id}/terms/{term.id}/status", {"status": "paused"}


def _remove_term(other):
    term = other.search_terms[0]
    return "delete", f"/profiles/{other.id}/terms/{term.id}", None


WRITE_ROUTES = [
    _add_genre,
    _move_genre,
    _remove_genre,
    _add_artist,
    _remove_artist,
    _add_anti_signal,
    _remove_anti_signal,
    _add_track,
    _remove_track,
    _add_term,
    _set_term_status,
    _remove_term,
]


@pytest.mark.parametrize("route", WRITE_ROUTES, ids=lambda route: route.__name__.lstrip("_"))
def test_every_write_route_on_another_artists_profile_is_404_and_changes_nothing(session, route):
    client, _, other = setup(session)
    method, path, data = route(other)
    before = snapshot(session, other.id)

    response = client.request(method.upper(), path, data=data, headers=htmx_headers(client))

    assert response.status_code == 404
    assert snapshot(session, other.id) == before


def test_a_member_edits_their_own_profile_add_genre(session):
    client, own, _ = setup(session)

    response = post(client, f"/profiles/{own.id}/genres", {"tag": "Braindance"})

    assert response.status_code == 200
    assert session.query(ProfileGenre).filter_by(profile_id=own.id, tag="Braindance").count() == 1


def test_a_member_edits_their_own_profile_set_term_status(session):
    client, own, _ = setup(session)
    term = own.search_terms[0]

    response = post(client, f"/profiles/{own.id}/terms/{term.id}/status", {"status": "paused"})

    assert response.status_code == 200
    assert session.get(SearchTerm, term.id).status == "paused"


def test_ask_claude_about_another_artists_profile_is_404(session):
    client, _, other = setup(session)

    assert (
        post(client, f"/profiles/{other.id}/suggest/genres", {"prompt": "More like this"}).status_code == 404
    )
    assert (
        post(client, f"/profiles/{other.id}/suggest/genres/add", {"choice": '{"value": "x"}'}).status_code
        == 404
    )


def test_the_suggester_is_never_called_to_ask_about_a_foreign_profile(session):
    fake = FakeSuggester()
    client, _, other = setup(session, suggester=fake)

    response = post(client, f"/profiles/{other.id}/suggest/genres", {"prompt": "More like this"})

    assert response.status_code == 404
    assert fake.calls == []


def test_the_suggester_is_never_called_to_add_a_foreign_profiles_suggestion(session):
    fake = FakeSuggester()
    client, _, other = setup(session, suggester=fake)

    response = post(client, f"/profiles/{other.id}/suggest/genres/add", {"choice": '{"value": "x"}'})

    assert response.status_code == 404
    assert fake.calls == []


def test_an_unknown_section_on_a_foreign_profile_is_404_without_calling_the_suggester(session):
    fake = FakeSuggester()
    client, _, other = setup(session, suggester=fake)

    response = post(client, f"/profiles/{other.id}/suggest/tracks", {"prompt": "x"})

    assert response.status_code == 404
    assert fake.calls == []


def test_a_nonexistent_profile_and_another_artists_profile_look_identical_on_a_contents_route(session):
    client, _, other = setup(session)
    missing_id = other.id + 1_000_000

    for_missing = post(client, f"/profiles/{missing_id}/genres", {"tag": "Sneaky"})
    for_other = post(client, f"/profiles/{other.id}/genres", {"tag": "Sneaky"})

    assert for_missing.status_code == for_other.status_code == 404
    assert for_missing.text == for_other.text


def test_a_nonexistent_profile_and_another_artists_profile_look_identical_as_html(session):
    client, _, other = setup(session)
    missing_id = other.id + 1_000_000

    for_missing = post(
        client, f"/profiles/{missing_id}/genres", {"tag": "Sneaky"}, headers=html_headers(client)
    )
    for_other = post(client, f"/profiles/{other.id}/genres", {"tag": "Sneaky"}, headers=html_headers(client))

    assert for_missing.status_code == for_other.status_code == 404
    assert for_missing.text == for_other.text


def test_a_nonexistent_profile_and_another_artists_profile_look_identical_on_a_suggest_route(session):
    client, _, other = setup(session)
    missing_id = other.id + 1_000_000

    for_missing = post(client, f"/profiles/{missing_id}/suggest/genres", {"prompt": "More like this"})
    for_other = post(client, f"/profiles/{other.id}/suggest/genres", {"prompt": "More like this"})

    assert for_missing.status_code == for_other.status_code == 404
    assert for_missing.text == for_other.text


def test_an_out_of_range_profile_id_is_404_not_500_for_an_admin_on_a_contents_route(session):
    client = admin_client(session)

    response = post(client, f"/profiles/{MAX_POSTGRES_INT + 1}/genres", {"tag": "Sneaky"})

    assert response.status_code == 404


def test_an_out_of_range_profile_id_is_404_not_500_for_an_admin_on_a_suggest_route(session):
    client = admin_client(session)

    response = post(
        client, f"/profiles/{MAX_POSTGRES_INT + 1}/suggest/genres/add", {"choice": '{"value": "x"}'}
    )

    assert response.status_code == 404


def test_removing_another_profiles_genre_through_your_own_profile_is_404_and_changes_nothing(session):
    client, own, other = setup(session)
    other_genre = other.genres[0]
    own_before, other_before = snapshot(session, own.id), snapshot(session, other.id)

    response = delete(client, f"/profiles/{own.id}/genres/{other_genre.id}")

    assert response.status_code == 404
    assert snapshot(session, own.id) == own_before
    assert snapshot(session, other.id) == other_before


def test_moving_another_profiles_genre_through_your_own_profile_is_404_and_changes_nothing(session):
    client, own, other = setup(session)
    other_genre = other.genres[0]
    own_before, other_before = snapshot(session, own.id), snapshot(session, other.id)

    response = post(client, f"/profiles/{own.id}/genres/{other_genre.id}/move", {"direction": "up"})

    assert response.status_code == 404
    assert snapshot(session, own.id) == own_before
    assert snapshot(session, other.id) == other_before


def test_setting_another_profiles_term_status_through_your_own_profile_is_404_and_changes_nothing(session):
    client, own, other = setup(session)
    other_term = other.search_terms[0]
    own_before, other_before = snapshot(session, own.id), snapshot(session, other.id)

    response = post(client, f"/profiles/{own.id}/terms/{other_term.id}/status", {"status": "paused"})

    assert response.status_code == 404
    assert snapshot(session, own.id) == own_before
    assert snapshot(session, other.id) == other_before


def test_removing_another_profiles_term_through_your_own_profile_is_404_and_changes_nothing(session):
    client, own, other = setup(session)
    other_term = other.search_terms[0]
    own_before, other_before = snapshot(session, own.id), snapshot(session, other.id)

    response = delete(client, f"/profiles/{own.id}/terms/{other_term.id}")

    assert response.status_code == 404
    assert snapshot(session, own.id) == own_before
    assert snapshot(session, other.id) == other_before


def test_an_invalid_move_direction_on_a_foreign_profile_is_404_not_400(session):
    client, _, other = setup(session)
    genre = other.genres[0]

    response = post(client, f"/profiles/{other.id}/genres/{genre.id}/move", {"direction": "sideways"})

    assert response.status_code == 404


def test_an_invalid_move_direction_on_your_own_profile_is_400(session):
    client, own, _ = setup(session)
    genre = own.genres[0]

    response = post(client, f"/profiles/{own.id}/genres/{genre.id}/move", {"direction": "sideways"})

    assert response.status_code == 400
