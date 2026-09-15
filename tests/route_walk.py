"""The route table the access walks share: every route, what an outsider gets, and how to call it.

A new route must be added to ROUTES, or test_every_route_is_covered fails. That is the point:
access can't be forgotten quietly.
"""

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from starlette.routing import Mount

from core.access import MAX_POSTGRES_INT
from tests.profile_helpers import TRACK_URL

OK, FORBIDDEN, NOT_FOUND = 200, 403, 404
OUT_OF_RANGE = MAX_POSTGRES_INT + 1

# Usable without access to any artist: signing in and out, and the health check.
EXEMPT = {
    ("GET", "/login"),
    ("GET", "/auth/google"),
    ("GET", "/auth/callback"),
    ("POST", "/logout"),
    ("GET", "/health"),
}

# What a signed-in member of a *different* artist gets, using the other artist's real ids.
ROUTES = {
    ("GET", "/"): OK,
    ("GET", "/profiles"): OK,
    ("POST", "/profiles"): NOT_FOUND,
    ("GET", "/profiles/{profile_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/settings"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/activate"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/pause"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/genres"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/genres/{genre_id}/move"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/genres/{genre_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/artists"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/artists/{artist_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/anti-signals"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/anti-signals/{signal_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/tracks"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/tracks/{track_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/terms"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/terms/{term_id}/status"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/terms/{term_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/suggest/{section}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/suggest/{section}/add"): NOT_FOUND,
    ("GET", "/digest"): OK,
    ("GET", "/digest/{day}"): OK,
    ("POST", "/outreach/{outreach_id}/verdict"): NOT_FOUND,
    ("GET", "/runs/status"): OK,
    ("POST", "/runs/request"): FORBIDDEN,
    ("POST", "/settings/claude-budget"): FORBIDDEN,
    ("GET", "/people"): FORBIDDEN,
    ("POST", "/people/members"): FORBIDDEN,
    ("POST", "/people/artists/{artist_id}/rename"): FORBIDDEN,
    ("POST", "/people/artists/{artist_id}/members/{user_id}/remove"): FORBIDDEN,
}

# Valid-looking forms, so a route that forgot its access check would actually do something.
FORMS = {
    "/profiles": {"name": "Stolen", "digest_target": "10"},
    "/profiles/{profile_id}/settings": {"name": "Renamed", "digest_target": "10", "min_followers": "0"},
    "/profiles/{profile_id}/genres": {"tag": "Stolen"},
    "/profiles/{profile_id}/genres/{genre_id}/move": {"direction": "down"},
    "/profiles/{profile_id}/artists": {"name": "Stolen"},
    "/profiles/{profile_id}/anti-signals": {"kind": "term", "value": "stolen"},
    "/profiles/{profile_id}/tracks": {"title": "Stolen", "spotify_url": TRACK_URL, "description": ""},
    "/profiles/{profile_id}/terms": {"term": "stolen"},
    "/profiles/{profile_id}/terms/{term_id}/status": {"status": "paused"},
    "/profiles/{profile_id}/suggest/{section}": {"prompt": "More like this"},
    "/profiles/{profile_id}/suggest/{section}/add": {"choice": '{"value": "stolen"}'},
    "/outreach/{outreach_id}/verdict": {"verdict": "bad-fit"},
    "/settings/claude-budget": {"budget": "9.00"},
    "/people/members": {"email": "x@example.com", "artist_id": "new", "new_artist_name": "Stolen"},
    "/people/artists/{artist_id}/rename": {"name": "Stolen"},
}

# Every id a URL can carry. `section` and `day` aren't ids.
ID_PARAMS = (
    "profile_id",
    "genre_id",
    "reference_artist_id",
    "signal_id",
    "track_id",
    "term_id",
    "outreach_id",
    "artist_id",
    "user_id",
)
# Ids of things inside a profile, checked only after the profile itself is found.
CHILD_ID_PARAMS = ("genre_id", "reference_artist_id", "signal_id", "track_id", "term_id")
CHILD_ID_PATH_PARTS = ("{genre_id}", "{artist_id}", "{signal_id}", "{track_id}", "{term_id}")


def registered_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path) the app answers, including routes inside included routers.

    FastAPI 0.141 keeps included routers nested inside `app.routes`, so scanning that list for
    APIRoutes finds only `/` and `/health`. `iter_route_contexts` flattens them, and its `path`
    includes any prefix given to `include_router`.
    """
    found = set()
    for context in iter_route_contexts(app.routes):
        if isinstance(context.original_route, Mount):  # /static: files only, nothing per artist
            continue
        assert isinstance(context.original_route, APIRoute), f"The walk can't call {context.path!r}"
        found.update((method, context.path) for method in context.methods)
    return found


# Routes whose form carries an artist id: creating a profile, adding a member.
FORM_ARTIST_ID_ROUTES = {("POST", "/profiles"), ("POST", "/people/members")}


def takes_an_id(method: str, path: str) -> bool:
    """Whether the route's URL or form carries an id the out-of-range sweep can spoil."""
    return "_id}" in path or (method, path) in FORM_ARTIST_ID_ROUTES


def fill_path(path: str, ids: dict) -> str:
    values = dict(ids)
    if path.startswith("/profiles/"):
        values["artist_id"] = ids["reference_artist_id"]  # on profile routes it's a reference artist
    return path.format(**values)


def call(
    client: TestClient,
    method: str,
    path: str,
    ids: dict,
    *,
    token: str,
    htmx: bool = True,
    form_artist_id: object | None = None,
):
    """Call a route from ROUTES the way the page would, with `ids` filled into the URL and form.

    The form's artist id (creating a profile, adding a member) is `ids["artist_id"]` unless
    `form_artist_id` says otherwise.
    """
    form = dict(FORMS.get(path, {}))
    if (method, path) in FORM_ARTIST_ID_ROUTES:
        form["artist_id"] = str(ids["artist_id"] if form_artist_id is None else form_artist_id)
    headers = {"accept": "text/html", "x-csrf-token": token}
    if htmx:
        headers["hx-request"] = "true"
    return client.request(method, fill_path(path, ids), data=form or None, headers=headers)
