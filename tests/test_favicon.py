"""The favicon: declared as an SVG, and /favicon.ico answered quietly instead of with a 404 page.

Vercel routes every path to this app, so a browser's automatic /favicon.ico request fell through
to the catch-all and came back as the full styled "Page not found" page. A user read that as a
broken sign-in. None of this may need a session: the browser asks before anyone has one.
"""

import re

from fastapi.testclient import TestClient

from web.app import STATIC_DIR, create_app
from web.settings import WebSettings

SETTINGS = WebSettings(
    web_database_url="postgresql+psycopg://noble_web:dbsecret@127.0.0.1:55432/noble_test",
    session_secret="s" * 48,
    google_client_id="123-abc.apps.googleusercontent.com",
    google_client_secret="GOCSPX-testsecret",
    allowed_emails="owner@example.com",
    secure_cookies=False,
)
ICON_URL = "/static/icon.svg"
HTML = {"accept": "text/html"}


def client() -> TestClient:
    return TestClient(create_app(SETTINGS))


def accent_colour() -> str:
    """The one accent token in the stylesheet, which the nav's mark is painted with."""
    css = (STATIC_DIR / "css" / "app.css").read_text()
    match = re.search(r"--color-accent:\s*([^;]+);", css)
    assert match, "app.css no longer defines --color-accent"
    return match.group(1).strip()


def test_the_legacy_path_answers_204_without_a_session():
    response = client().get("/favicon.ico")

    assert response.status_code == 204
    assert response.content == b""


def test_the_legacy_path_never_returns_an_error_page():
    """The whole point: a browser asking for the old path must not be shown a rendered 404."""
    response = client().get("/favicon.ico", headers=HTML)

    assert response.status_code == 204
    assert "Page not found" not in response.text


def test_pages_declare_the_svg_icon_and_an_apple_touch_icon():
    html = client().get("/login", headers=HTML).text

    assert f'<link rel="icon" type="image/svg+xml" href="{ICON_URL}">' in html
    assert f'<link rel="apple-touch-icon" href="{ICON_URL}">' in html


def test_the_icon_is_served_from_our_own_origin_as_an_svg():
    response = client().get(ICON_URL)

    assert response.status_code == 200
    assert "image/svg+xml" in response.headers["content-type"]


def test_the_icon_wears_the_accent_colour_from_the_stylesheet():
    """A static file can't read a CSS variable, so the token's value is pinned here instead."""
    svg = client().get(ICON_URL).text

    assert accent_colour() in svg


def test_the_content_security_policy_allows_our_own_images():
    """An icon is fetched as an image; if img-src ever lost 'self' it would silently never render."""
    policy = client().get("/login", headers=HTML).headers["content-security-policy"]

    assert "img-src 'self'" in policy
