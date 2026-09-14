"""Step 1 of contact research: what a curator tells you about themselves, in their own words.

The samples below are real description formats from the first live batch, lightly renamed.
The extractor must pull out routes a person could actually use (email, a social handle, a
submission form) and the links worth following, without mistaking decoration for either.
"""

import pytest

from pipeline.contact_extract import FoundRoute, extract_contacts


def routes(text: str) -> list[tuple[str, str]]:
    return [(route.route_type, route.value) for route in extract_contacts(text).routes]


def links(text: str) -> list[str]:
    return list(extract_contacts(text).links)


class TestEmails:
    @pytest.mark.parametrize(
        ("text", "email"),
        [
            (
                "vibe with me on late night drives. Submissions: PlaylistsByElise@gmail.com",
                "playlistsbyelise@gmail.com",
            ),
            ("Mainly ambient idm & drill and bass. | pitch.playlistd@gmail.com", "pitch.playlistd@gmail.com"),
            ("want a spot on the playlist email drakewesten173@gmail.com.", "drakewesten173@gmail.com"),
            ("Demos to demos [at] quietlabel [dot] com please", "demos@quietlabel.com"),
            ("Demos: demos (at) quietlabel.com", "demos@quietlabel.com"),
        ],
    )
    def test_finds_the_address(self, text, email):
        assert routes(text) == [("email", email)]

    def test_an_email_domain_is_not_also_a_link(self):
        assert links("Submissions: curator@quietlabel.com") == []

    @pytest.mark.parametrize("text", ["Rock@Night sessions", "music @ home", "v0.2 - This is IDM"])
    def test_ignores_things_that_only_look_like_email(self, text):
        assert routes(text) == []


class TestHandles:
    @pytest.mark.parametrize(
        ("text", "route"),
        [
            ("Instagram: @kinkashistacy ❤️‍🔥🌕🦉", ("instagram", "kinkashistacy")),
            (
                "Follow @laguerradelasgalaxiasvinyl on Instagram an my years compilation",
                ("instagram", "laguerradelasgalaxiasvinyl"),
            ),
            ("IG @synth.curator for more", ("instagram", "synth.curator")),
            ("insta: @Night_Drives.", ("instagram", "night_drives")),
            ("twitter @glitchlists", ("x", "glitchlists")),
            ("bluesky: @glitch.bsky.social", ("bluesky", "glitch.bsky.social")),
        ],
    )
    def test_platform_hint_names_the_route(self, text, route):
        assert routes(text) == [route]

    @pytest.mark.parametrize(
        ("text", "route"),
        [
            ("https://www.instagram.com/some.curator/?hl=en", ("instagram", "some.curator")),
            ("x.com/GlitchLists", ("x", "glitchlists")),
            ("https://bsky.app/profile/glitch.bsky.social", ("bluesky", "glitch.bsky.social")),
        ],
    )
    def test_profile_urls_become_routes_not_links(self, text, route):
        assert routes(text) == [route]
        assert links(text) == []

    def test_a_bare_handle_with_no_platform_is_not_a_route(self):
        assert routes("thanks to @someone for the cover") == []

    def test_a_post_url_is_neither_route_nor_link(self):
        text = "see https://instagram.com/p/C8xYz12/"
        assert routes(text) == []
        assert links(text) == []

    def test_an_at_sign_before_a_website_is_not_a_handle(self):
        text = "check the radio show & record label @ www.theslowmusicmovement.org"
        assert routes(text) == []
        assert links(text) == ["https://www.theslowmusicmovement.org"]


class TestSubmissionRoutes:
    @pytest.mark.parametrize(
        "url",
        [
            "https://forms.gle/Ab12Cd34",
            "https://docs.google.com/forms/d/e/1FAIpQL/viewform",
            "https://glitchlists.typeform.com/to/xYz",
            "https://tally.so/r/w8Lp",
        ],
    )
    def test_form_links_are_submission_routes(self, url):
        assert routes(f"Submit here: {url}") == [("submission-form", url)]
        assert links(f"Submit here: {url}") == []

    def test_a_discord_invite_is_a_route(self):
        text = "Submit tracks for consideration: https://discord.gg/K4tkz8e"
        assert routes(text) == [("other", "https://discord.gg/K4tkz8e")]


class TestLinksToFollow:
    @pytest.mark.parametrize(
        ("text", "link"),
        [
            ("www.soundcloud.com/k-gavrilov for more KANZ music.", "https://www.soundcloud.com/k-gavrilov"),
            ("Clear your head with a drive.. website: maskedmortal.com", "https://maskedmortal.com"),
            ("nostalgic nights. Visit: PurZynthRekords.com", "https://PurZynthRekords.com"),
            ("Enjoy! https://rumprecordings.bandcamp.com/", "https://rumprecordings.bandcamp.com/"),
            ("all my links: linktr.ee/glitchlists", "https://linktr.ee/glitchlists"),
            (
                "Full details @ http://bocpages.org/wiki/Campfire_Mixtape.",
                "http://bocpages.org/wiki/Campfire_Mixtape",
            ),
        ],
    )
    def test_finds_the_link(self, text, link):
        assert links(text) == [link]
        assert routes(text) == []

    def test_spotify_links_are_not_worth_following(self):
        assert links("more at https://open.spotify.com/user/someone and spotify.link/abc") == []

    def test_the_same_link_is_listed_once(self):
        assert links("linktr.ee/glitchlists · https://linktr.ee/glitchlists") == [
            "https://linktr.ee/glitchlists"
        ]

    @pytest.mark.parametrize("text", ["v0.2 - IDM", "e.g. Aphex Twin", "Vol. 2 Issue #31", "No.1 hits"])
    def test_version_numbers_and_abbreviations_are_not_links(self, text):
        assert links(text) == []


class TestWholeDescriptions:
    def test_routes_keep_the_order_they_appear_in(self):
        text = "IG @glitchlists · demos: hello@glitchlists.net · https://forms.gle/Ab12"
        assert routes(text) == [
            ("instagram", "glitchlists"),
            ("email", "hello@glitchlists.net"),
            ("submission-form", "https://forms.gle/Ab12"),
        ]

    def test_the_same_route_is_listed_once(self):
        text = "Instagram: @glitchlists — instagram.com/glitchlists"
        assert routes(text) == [("instagram", "glitchlists")]

    @pytest.mark.parametrize("text", ["", "   ", None, "Our choice of the best IDM tracks"])
    def test_nothing_to_find(self, text):
        result = extract_contacts(text)
        assert result.routes == ()
        assert result.links == ()

    def test_routes_carry_their_exclusion_key(self):
        assert FoundRoute("email", "hello@glitchlists.net").key == "email:hello@glitchlists.net"
        assert FoundRoute("instagram", "glitchlists").key == "instagram:glitchlists"
