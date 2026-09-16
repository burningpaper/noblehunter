import pytest

from core.settings import MissingSettingError, Settings, load_settings

# A password no field name could contain: "secret" alone also matches `google_client_secret=None`
# in the repr, which would fail the leak test below without anything having leaked.
POOLED = "postgresql://user:pw-must-not-leak@ep-test-pooler.neon.tech/neondb?sslmode=require"
DIRECT = "postgresql://user:pw-must-not-leak@ep-test.neon.tech/neondb?sslmode=require"


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

    assert "pw-must-not-leak" not in repr(settings)
    assert "pw-must-not-leak" not in str(settings)


def test_default_load_reads_env_local(monkeypatch, tmp_path):
    (tmp_path / ".env.local").write_text(f"DATABASE_URL={POOLED}\n")
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.sqlalchemy_url(pooled=True).startswith("postgresql+psycopg://")


def test_env_local_overrides_env(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://from-env/db\n")
    (tmp_path / ".env.local").write_text("DATABASE_URL=postgresql://from-env-local/db\n")
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.sqlalchemy_url(pooled=True) == "postgresql+psycopg://from-env-local/db"


def test_mail_settings_are_optional(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    for name in ("MAIL_TOKEN_KEY", "GOOGLE_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings(env_file=None)

    assert settings.mail_token_key is None
    assert settings.google_client_id is None


def test_settings_type_is_exported():
    assert Settings is not None
