"""Settings for the web app on Vercel.

Every value here is required: an app that can't identify its one allowed user should refuse
to start, not quietly let nobody in (or everybody). Secrets are SecretStr, and errors name
the setting, never the value.
"""

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.settings import ENV_FILES, MissingSettingError, with_psycopg_driver

MIN_SESSION_SECRET_LENGTH = 32


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    web_database_url: SecretStr
    session_secret: SecretStr
    google_client_id: str
    google_client_secret: SecretStr
    allowed_emails: str
    secure_cookies: bool = True
    # Optional on purpose: without it the app still runs and Ask Claude explains what's missing.
    anthropic_api_key: SecretStr | None = None
    # Optional on purpose: without it the Pitch mailbox card explains that mail isn't configured.
    mail_token_key: SecretStr | None = None

    @field_validator("session_secret")
    @classmethod
    def session_secret_is_long(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_SESSION_SECRET_LENGTH:
            raise ValueError(f"must be at least {MIN_SESSION_SECRET_LENGTH} characters")
        return value

    @field_validator("allowed_emails")
    @classmethod
    def at_least_one_email(cls, value: str) -> str:
        if not any(part.strip() for part in value.split(",")):
            raise ValueError("must list at least one email address")
        return value

    @property
    def allowed_email_set(self) -> frozenset[str]:
        return frozenset(part.strip().lower() for part in self.allowed_emails.split(",") if part.strip())

    def sqlalchemy_url(self) -> str:
        return with_psycopg_driver(self.web_database_url.get_secret_value())


def load_web_settings(env_file: str | tuple[str, ...] | None = ENV_FILES) -> WebSettings:
    try:
        return WebSettings(_env_file=env_file)
    except ValidationError as error:
        problems = [_describe(problem) for problem in error.errors()]
        raise MissingSettingError(f"Web settings: {'; '.join(problems)}. See .env.example.") from None


def _describe(problem: dict) -> str:
    name = str(problem["loc"][0]).upper()
    if problem["type"] == "missing":
        return f"{name} is not set"
    return f"{name} is invalid ({problem['msg'].removeprefix('Value error, ')})"
