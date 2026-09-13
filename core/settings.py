"""Settings shared by the web app and the pipeline.

Database URLs are held as SecretStr so they never show up in logs, reprs or error
messages. The web app (Vercel) uses the pooled URL; the pipeline and migrations can use
the direct one when it's configured.
"""

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# Later files win: .env.local (per-machine, e.g. from Vercel/Neon) overrides .env.
ENV_FILES = (".env", ".env.local")
DRIVER_SCHEME = "postgresql+psycopg://"
PLAIN_SCHEMES = ("postgresql://", "postgres://")


class MissingSettingError(RuntimeError):
    """A required setting is absent. The message names the setting, never a value."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    database_url: SecretStr
    database_url_unpooled: SecretStr | None = None

    def sqlalchemy_url(self, pooled: bool = True) -> str:
        secret = (
            self.database_url if pooled or self.database_url_unpooled is None else self.database_url_unpooled
        )
        return _with_driver(secret.get_secret_value())


def load_settings(env_file: str | tuple[str, ...] | None = ENV_FILES) -> Settings:
    try:
        return Settings(_env_file=env_file)
    except ValidationError as error:
        missing = [str(e["loc"][0]).upper() for e in error.errors() if e["type"] == "missing"]
        if missing:
            raise MissingSettingError(
                f"{', '.join(missing)} is not set. "
                "Add it to .env or .env.local (see .env.example), or set it in the environment."
            ) from None
        invalid = ", ".join(str(e["loc"][0]).upper() for e in error.errors())
        raise MissingSettingError(f"Invalid settings: {invalid}.") from None


def _with_driver(url: str) -> str:
    for scheme in PLAIN_SCHEMES:
        if url.startswith(scheme):
            return DRIVER_SCHEME + url[len(scheme) :]
    return url
