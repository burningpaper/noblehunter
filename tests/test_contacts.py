"""Contact normalisation: the foundation of 'never show Jarred the same curator twice'.

Every contact found in the wild (a description, a Linktree, a bio) must reduce to one
stable key, so two spellings of the same person collide in the database.
"""

import pytest

from core.contacts import (
    contact_key,
    email_domain_key,
    normalize_email,
    normalize_handle,
    normalize_url,
)


class TestNormalizeEmail:
    @pytest.mark.parametrize(
        "raw",
        [
            "curator@label.com",
            "Curator@Label.COM",
            "  curator@label.com  ",
            "mailto:curator@label.com",
            "curator@label.com.",
            "(curator@label.com)",
        ],
    )
    def test_variants_reduce_to_one_address(self, raw):
        assert normalize_email(raw) == "curator@label.com"

    @pytest.mark.parametrize("raw", ["", "   ", "not-an-email", "a@b", "@label.com", "curator@", "a@@b.com"])
    def test_invalid_input_returns_none(self, raw):
        assert normalize_email(raw) is None


class TestNormalizeHandle:
    @pytest.mark.parametrize(
        "raw",
        [
            "@SynthCurator",
            "synthcurator",
            "instagram.com/synthcurator",
            "https://www.instagram.com/SynthCurator/",
            "https://instagram.com/synthcurator?igsh=abc123",
        ],
    )
    def test_instagram_variants(self, raw):
        assert normalize_handle("instagram", raw) == "synthcurator"

    @pytest.mark.parametrize(
        "raw",
        ["@IDM_Lists", "https://x.com/idm_lists", "https://twitter.com/IDM_Lists/", "x.com/idm_lists?s=20"],
    )
    def test_x_accepts_both_domains(self, raw):
        assert normalize_handle("x", raw) == "idm_lists"

    @pytest.mark.parametrize(
        "raw",
        ["@Curator.bsky.social", "curator.bsky.social", "https://bsky.app/profile/curator.bsky.social"],
    )
    def test_bluesky_variants(self, raw):
        assert normalize_handle("bluesky", raw) == "curator.bsky.social"

    @pytest.mark.parametrize(
        "platform, raw",
        [
            ("instagram", "https://www.instagram.com/p/C0abc123/"),
            ("instagram", "https://instagram.com/explore/"),
            ("x", "https://x.com/home"),
            ("x", "https://x.com/i/status/123"),
            ("instagram", ""),
            ("x", "@this_handle_is_far_too_long_for_x"),
        ],
    )
    def test_non_profile_links_and_invalid_handles_return_none(self, platform, raw):
        assert normalize_handle(platform, raw) is None

    def test_unknown_platform_raises(self):
        with pytest.raises(ValueError, match="Unsupported platform"):
            normalize_handle("myspace", "@someone")


class TestNormalizeUrl:
    @pytest.mark.parametrize(
        "raw",
        [
            "https://www.Label.com/submit/",
            "http://label.com/submit",
            "label.com/submit?utm_source=spotify",
            "https://label.com/submit#form",
        ],
    )
    def test_variants_reduce_to_host_and_path(self, raw):
        assert normalize_url(raw) == "label.com/submit"

    def test_bare_domain_has_no_trailing_slash(self):
        assert normalize_url("https://www.label.com/") == "label.com"

    def test_shared_host_keeps_the_path_that_identifies_the_person(self):
        assert normalize_url("https://linktr.ee/SynthCurator") == "linktr.ee/synthcurator"

    @pytest.mark.parametrize("raw", ["", "not a url", "https://"])
    def test_invalid_input_returns_none(self, raw):
        assert normalize_url(raw) is None


class TestContactKey:
    def test_email_key(self):
        assert contact_key("email", "Curator@Label.com") == "email:curator@label.com"

    def test_instagram_key(self):
        assert contact_key("instagram", "https://instagram.com/SynthCurator/") == "instagram:synthcurator"

    def test_x_key(self):
        assert contact_key("x", "@IDM_Lists") == "x:idm_lists"

    def test_submission_form_key_uses_url(self):
        assert contact_key("submission-form", "https://www.label.com/submit/") == "url:label.com/submit"

    def test_other_route_key_uses_url(self):
        assert contact_key("other", "https://linktr.ee/SynthCurator") == "url:linktr.ee/synthcurator"

    def test_unusable_value_returns_none(self):
        assert contact_key("email", "not-an-email") is None

    def test_unknown_route_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported route type"):
            contact_key("carrier-pigeon", "coo")


class TestEmailDomainKey:
    def test_company_domain_is_a_key(self):
        assert email_domain_key("promo@coollabel.com") == "domain:coollabel.com"

    @pytest.mark.parametrize(
        "email",
        [
            "someone@gmail.com",
            "someone@googlemail.com",
            "someone@yahoo.co.uk",
            "someone@hotmail.com",
            "someone@outlook.com",
            "someone@icloud.com",
            "someone@proton.me",
            "someone@protonmail.com",
        ],
    )
    def test_free_email_domains_are_never_keys(self, email):
        assert email_domain_key(email) is None

    def test_invalid_email_returns_none(self):
        assert email_domain_key("nope") is None
