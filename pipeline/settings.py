"""Settings for the pipeline on the Mac Mini.

The pipeline needs two search keys and a database URL. The URL is chosen in order of least
privilege: the `noble_pipeline` role written by `pipeline.cli db create-roles` first, then
the owner's direct connection, then whatever `DATABASE_URL` holds. Secrets stay SecretStr,
and errors name settings, never values.
"""

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.settings import MissingSettingError, with_psycopg_driver

PIPELINE_ENV_FILES = (".env", ".env.local", ".env.roles.local")
DATABASE_URL_PREFERENCE = ("pipeline_database_url", "database_url_unpooled", "database_url")


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PIPELINE_ENV_FILES, extra="ignore")

    serper_api_key: SecretStr
    brave_api_key: SecretStr
    pipeline_database_url: SecretStr | None = None
    database_url_unpooled: SecretStr | None = None
    database_url: SecretStr | None = None

    def sqlalchemy_url(self) -> str:
        for name in DATABASE_URL_PREFERENCE:
            secret = getattr(self, name)
            if secret is not None and secret.get_secret_value().strip():
                return with_psycopg_driver(secret.get_secret_value().strip())
        raise MissingSettingError(
            "No database URL is set. Add PIPELINE_DATABASE_URL (written by `pipeline.cli db create-roles`), "
            "DATABASE_URL_UNPOOLED or DATABASE_URL."
        )


def load_pipeline_settings(env_file: str | tuple[str, ...] | None = PIPELINE_ENV_FILES) -> PipelineSettings:
    try:
        return PipelineSettings(_env_file=env_file)
    except ValidationError as error:
        missing = [
            str(problem["loc"][0]).upper() for problem in error.errors() if problem["type"] == "missing"
        ]
        invalid = [
            str(problem["loc"][0]).upper() for problem in error.errors() if problem["type"] != "missing"
        ]
        parts = []
        if missing:
            parts.append(f"{', '.join(missing)} not set")
        if invalid:
            parts.append(f"{', '.join(invalid)} invalid")
        raise MissingSettingError(f"Pipeline settings: {'; '.join(parts)}. See .env.example.") from None
