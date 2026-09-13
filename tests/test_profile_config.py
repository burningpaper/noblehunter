"""Profile config: the YAML a profile is imported from, validated before it touches the database."""

from pathlib import Path

import pytest

from core.profile_config import ProfileConfigError, load_profile_config, normalize_text

TRACK_URL = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"

VALID_YAML = f"""
name: "  Synman  "
digest_target: 15
genres: [IDM, braindance, ambient electronica]
reference_artists: [Aphex Twin, Boards of Canada, Autechre]
anti_signals:
  artists: [Lofi Girl]
  terms: [lo-fi study beats, EDM]
tracks:
  - title: Glass Weather
    spotify_url: "{TRACK_URL}?si=abc123"
    description: Brittle breaks under a slow synth tide.
search_terms: [glitchy ambient]
"""


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "profile.yaml"
    path.write_text(content)
    return path


def test_valid_profile_loads_with_clean_values(tmp_path):
    config = load_profile_config(write(tmp_path, VALID_YAML))

    assert config.name == "Synman"
    assert config.digest_target == 15
    assert config.active is False
    assert config.genres == ["IDM", "braindance", "ambient electronica"]
    assert config.anti_signals.terms == ["lo-fi study beats", "EDM"]
    assert config.tracks[0].spotify_url == TRACK_URL


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(ProfileConfigError, match="not found"):
        load_profile_config(tmp_path / "nope.yaml")


def test_broken_yaml_reports_the_line(tmp_path):
    with pytest.raises(ProfileConfigError, match="line"):
        load_profile_config(write(tmp_path, "name: Synman\ngenres: [IDM,\n"))


def test_needs_at_least_three_reference_artists(tmp_path):
    content = VALID_YAML.replace("[Aphex Twin, Boards of Canada, Autechre]", "[Aphex Twin]")

    with pytest.raises(ProfileConfigError, match="reference_artists"):
        load_profile_config(write(tmp_path, content))


def test_duplicate_artists_are_rejected_after_normalisation(tmp_path):
    content = VALID_YAML.replace(
        "[Aphex Twin, Boards of Canada, Autechre]", "[Aphex Twin, aphex  TWIN, Autechre]"
    )

    with pytest.raises(ProfileConfigError, match="Duplicate.*aphex twin"):
        load_profile_config(write(tmp_path, content))


def test_duplicate_search_terms_are_rejected(tmp_path):
    content = VALID_YAML.replace("[glitchy ambient]", "[glitchy ambient, Glitchy Ambient]")

    with pytest.raises(ProfileConfigError, match="Duplicate.*glitchy ambient"):
        load_profile_config(write(tmp_path, content))


def test_empty_list_entries_are_rejected(tmp_path):
    content = VALID_YAML.replace("[IDM, braindance, ambient electronica]", "[IDM, '   ']")

    with pytest.raises(ProfileConfigError, match="genres"):
        load_profile_config(write(tmp_path, content))


def test_track_url_must_be_a_spotify_track(tmp_path):
    content = VALID_YAML.replace(f"{TRACK_URL}?si=abc123", "https://soundcloud.com/synman/glass-weather")

    with pytest.raises(ProfileConfigError, match="spotify_url"):
        load_profile_config(write(tmp_path, content))


def test_active_profile_needs_five_search_terms(tmp_path):
    content = VALID_YAML + "active: true\n"

    with pytest.raises(ProfileConfigError, match="5 search terms"):
        load_profile_config(write(tmp_path, content))


def test_active_profile_with_enough_terms_loads(tmp_path):
    terms = "[glitchy ambient, braindance playlist, IDM playlist, late-night electronics, Autechre playlist]"
    content = VALID_YAML.replace("[glitchy ambient]", terms) + "active: true\n"

    assert load_profile_config(write(tmp_path, content)).active is True


def test_digest_target_must_be_sensible(tmp_path):
    content = VALID_YAML.replace("digest_target: 15", "digest_target: 500")

    with pytest.raises(ProfileConfigError, match="digest_target"):
        load_profile_config(write(tmp_path, content))


def test_error_lists_every_problem_at_once(tmp_path):
    content = VALID_YAML.replace("[Aphex Twin, Boards of Canada, Autechre]", "[Aphex Twin]").replace(
        "digest_target: 15", "digest_target: 0"
    )

    with pytest.raises(ProfileConfigError) as error:
        load_profile_config(write(tmp_path, content))

    assert "reference_artists" in str(error.value)
    assert "digest_target" in str(error.value)


@pytest.mark.parametrize(
    "left, right",
    [("  Aphex   TWIN ", "aphex twin"), ("µ-Ziq", "μ-ziq"), ("Boards\tof Canada", "boards of canada")],
)
def test_normalize_text_matches_spelling_variants(left, right):
    assert normalize_text(left) == normalize_text(right)


def test_example_profile_in_repo_is_valid():
    example = Path(__file__).parent.parent / "config" / "profiles" / "example.yaml"

    assert load_profile_config(example).name
