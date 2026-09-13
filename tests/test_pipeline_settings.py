"""Pipeline settings: search keys and which database URL the Mac Mini connects with."""

import pytest

from core.settings import MissingSettingError
from pipeline.settings import load_pipeline_settings

NAMES = (
    "SERPER_API_KEY",
    "BRAVE_API_KEY",
    "PIPELINE_DATABASE_URL",
    "DATABASE_URL_UNPOOLED",
    "DATABASE_URL",
)
PIPELINE_URL = "postgresql://noble_pipeline:pipesecret@ep-test.neon.tech/neondb?sslmode=require"
DIRECT_URL = "postgresql://neondb_owner:ownersecret@ep-test.neon.tech/neondb?sslmode=require"
POOLED_URL = "postgresql://neondb_owner:ownersecret@ep-test-pooler.neon.tech/neondb?sslmode=require"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # keep the repo's own env files out of these tests
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SERPER_API_KEY", "serper-test-key")
    monkeypatch.setenv("BRAVE_API_KEY", "brave-test-key")
    return monkeypatch


def test_prefers_the_pipeline_role_url(env):
    env.setenv("PIPELINE_DATABASE_URL", PIPELINE_URL)
    env.setenv("DATABASE_URL_UNPOOLED", DIRECT_URL)

    url = load_pipeline_settings(env_file=None).sqlalchemy_url()

    assert url.startswith("postgresql+psycopg://noble_pipeline:")


def test_falls_back_to_the_direct_url(env):
    env.setenv("DATABASE_URL_UNPOOLED", DIRECT_URL)
    env.setenv("DATABASE_URL", POOLED_URL)

    assert "ep-test.neon.tech" in load_pipeline_settings(env_file=None).sqlalchemy_url()


def test_falls_back_to_database_url_last(env):
    env.setenv("DATABASE_URL", POOLED_URL)

    assert "ep-test-pooler.neon.tech" in load_pipeline_settings(env_file=None).sqlalchemy_url()


def test_no_database_url_at_all_is_a_clear_error(env):
    settings = load_pipeline_settings(env_file=None)

    with pytest.raises(MissingSettingError, match="PIPELINE_DATABASE_URL"):
        settings.sqlalchemy_url()


def test_missing_search_keys_are_named(env):
    env.delenv("SERPER_API_KEY")
    env.delenv("BRAVE_API_KEY")

    with pytest.raises(MissingSettingError) as error:
        load_pipeline_settings(env_file=None)

    assert "SERPER_API_KEY" in str(error.value)
    assert "BRAVE_API_KEY" in str(error.value)


def test_reads_the_roles_file_written_by_create_roles(env, tmp_path):
    (tmp_path / ".env.roles.local").write_text(f"PIPELINE_DATABASE_URL={PIPELINE_URL}\n")

    url = load_pipeline_settings().sqlalchemy_url()

    assert "noble_pipeline" in url


def test_secrets_never_appear_in_repr(env):
    env.setenv("PIPELINE_DATABASE_URL", PIPELINE_URL)

    settings = load_pipeline_settings(env_file=None)

    for secret in ("serper-test-key", "brave-test-key", "pipesecret"):
        assert secret not in repr(settings)


def test_search_keys_are_readable_for_the_providers(env):
    settings = load_pipeline_settings(env_file=None)

    assert settings.serper_api_key.get_secret_value() == "serper-test-key"
    assert settings.brave_api_key.get_secret_value() == "brave-test-key"
