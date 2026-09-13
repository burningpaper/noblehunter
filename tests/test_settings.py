import pytest

from core.settings import MissingSettingError, Settings, load_settings

POOLED = "postgresql://user:secret@ep-test-pooler.neon.tech/neondb?sslmode=require"
DIRECT = "postgresql://user:secret@ep-test.neon.tech/neondb?sslmode=require"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("DATABASE_URL", "DATABASE_URL_UNPOOLED"):
        monkeypatch.delenv(name, raising=False)


def test_pooled_url_uses_psycopg_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", POOLED)

    settings = load_settings(env_file=None)

    assert settings.sqlalchemy_url(pooled=True) == POOLED.replace("postgresql://", "postgresql+psycopg://", 1)


def test_direct_url_used_when_unpooled_requested(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", POOLED)
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", DIRECT)

    settings = load_settings(env_file=None)

    assert settings.sqlalchemy_url(pooled=False) == DIRECT.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )


def test_unpooled_falls_back_to_pooled_when_not_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", POOLED)

    settings = load_settings(env_file=None)

    assert settings.sqlalchemy_url(pooled=False) == settings.sqlalchemy_url(pooled=True)


def test_legacy_postgres_scheme_is_converted(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@host/db")

    settings = load_settings(env_file=None)

    assert settings.sqlalchemy_url(pooled=True) == "postgresql+psycopg://user:pw@host/db"


def test_missing_database_url_raises_clear_error():
    with pytest.raises(MissingSettingError, match="DATABASE_URL"):
        load_settings(env_file=None)


def test_repr_never_leaks_password(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", POOLED)

    settings = load_settings(env_file=None)

    assert "secret" not in repr(settings)
    assert "secret" not in str(settings)


def test_settings_type_is_exported():
    assert Settings is not None
