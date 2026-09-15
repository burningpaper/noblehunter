"""Importing a profile YAML into the database, via the library function and the CLI."""

from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from core.models import Artist, Profile, SearchTerm
from core.profile_config import load_profile_config
from core.profile_import import import_profile
from pipeline.cli import app
from tests.conftest import TEST_DATABASE_URL
from tests.factories import default_artist_id, make_artist

REPO_ROOT = Path(__file__).parent.parent
TRACK_URL = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"

PROFILE_YAML = f"""
name: Import Test Profile
digest_target: 12
genres: [IDM, braindance, ambient electronica]
reference_artists: [Aphex Twin, Boards of Canada, Autechre]
anti_signals:
  artists: [Lofi Girl]
  terms: [lo-fi study beats]
tracks:
  - title: Glass Weather
    spotify_url: {TRACK_URL}
    description: Brittle breaks.
search_terms: [glitchy ambient, braindance playlist]
"""


def config_from(tmp_path: Path, content: str = PROFILE_YAML):
    path = tmp_path / "profile.yaml"
    path.write_text(content)
    return load_profile_config(path)


class TestImportProfile:
    def test_new_profile_gets_everything(self, session, tmp_path):
        result = import_profile(session, default_artist_id(session), config_from(tmp_path))

        profile = session.get(Profile, result.profile_id)
        assert result.created is True
        assert profile.name == "Import Test Profile"
        assert profile.digest_target == 12
        assert profile.is_active is False
        assert [a.display_name for a in profile.reference_artists] == [
            "Aphex Twin",
            "Boards of Canada",
            "Autechre",
        ]
        assert {(s.kind, s.value) for s in profile.anti_signals} == {
            ("artist", "Lofi Girl"),
            ("term", "lo-fi study beats"),
        }
        assert [t.spotify_url for t in profile.tracks] == [TRACK_URL]
        assert {(t.term, t.origin, t.status) for t in profile.search_terms} == {
            ("glitchy ambient", "manual", "active"),
            ("braindance playlist", "manual", "active"),
        }

    def test_genres_keep_priority_order(self, session, tmp_path):
        result = import_profile(session, default_artist_id(session), config_from(tmp_path))

        genres = sorted(session.get(Profile, result.profile_id).genres, key=lambda g: g.priority)
        assert [g.tag for g in genres] == ["IDM", "braindance", "ambient electronica"]

    def test_reimport_updates_settings_and_replaces_lists(self, session, tmp_path):
        import_profile(session, default_artist_id(session), config_from(tmp_path))
        changed = PROFILE_YAML.replace("digest_target: 12", "digest_target: 8").replace(
            "[Aphex Twin, Boards of Canada, Autechre]", "[Plaid, Ochre, Autechre]"
        )

        result = import_profile(session, default_artist_id(session), config_from(tmp_path, changed))

        profile = session.get(Profile, result.profile_id)
        assert result.created is False
        assert profile.digest_target == 8
        assert sorted(a.display_name for a in profile.reference_artists) == ["Autechre", "Ochre", "Plaid"]

    def test_reimport_adds_terms_but_never_deletes_existing_ones(self, session, tmp_path):
        first = import_profile(session, default_artist_id(session), config_from(tmp_path))
        session.add(
            SearchTerm(
                profile_id=first.profile_id,
                term="late-night electronics",
                origin="suggested",
                status="active",
            )
        )
        session.flush()
        changed = PROFILE_YAML.replace(
            "[glitchy ambient, braindance playlist]", "[glitchy ambient, IDM playlist]"
        )

        import_profile(session, default_artist_id(session), config_from(tmp_path, changed))

        terms = session.scalars(
            select(SearchTerm.term).where(SearchTerm.profile_id == first.profile_id)
        ).all()
        assert sorted(terms) == [
            "IDM playlist",
            "braindance playlist",
            "glitchy ambient",
            "late-night electronics",
        ]

    def test_reimport_does_not_duplicate_existing_terms_with_different_spelling(self, session, tmp_path):
        first = import_profile(session, default_artist_id(session), config_from(tmp_path))
        changed = PROFILE_YAML.replace("[glitchy ambient, braindance playlist]", "[Glitchy  Ambient]")

        import_profile(session, default_artist_id(session), config_from(tmp_path, changed))

        count = len(
            session.scalars(select(SearchTerm).where(SearchTerm.profile_id == first.profile_id)).all()
        )
        assert count == 2

    def test_the_same_file_can_be_imported_for_two_artists(self, session, tmp_path):
        first = import_profile(session, make_artist(session).id, config_from(tmp_path))
        second = import_profile(session, make_artist(session).id, config_from(tmp_path))

        assert first.profile_id != second.profile_id
        assert first.created and second.created


class TestProfileImportCommand:
    def test_imports_example_profile(self, engine, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        monkeypatch.setenv("DATABASE_URL_UNPOOLED", TEST_DATABASE_URL)
        example = REPO_ROOT / "config" / "profiles" / "example.yaml"

        try:
            result = CliRunner().invoke(
                app, ["profile", "import", str(example), "--artist", "CLI Import Artist"]
            )

            assert result.exit_code == 0, result.output
            assert "Example Profile" in result.output
            with Session(engine) as session:
                assert session.scalar(select(Profile).where(Profile.name == "Example Profile")) is not None
        finally:
            with Session(engine) as session:
                session.execute(delete(Profile).where(Profile.name == "Example Profile"))
                session.execute(delete(Artist).where(Artist.name == "CLI Import Artist"))
                session.commit()

    def test_invalid_file_exits_with_readable_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        broken = tmp_path / "broken.yaml"
        broken.write_text("name: Broken\ngenres: [IDM]\n")

        result = CliRunner().invoke(app, ["profile", "import", str(broken)])

        assert result.exit_code == 1
        assert "reference_artists" in result.output
        assert "Traceback" not in result.output
