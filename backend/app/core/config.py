"""
All environment variables flow through this one place, instead of being
read directly with os.environ anywhere else in the code - so there's a
single clear answer to "where does the system get its configuration from",
plus automatic type-checking (pydantic raises a clear error if an env var
is missing, instead of a weird failure mid-run).
"""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres
    postgres_user: str = "garmin_app"
    postgres_password: str = "change_me_locally"
    postgres_db: str = "garmin_ai"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379

    # JWT - secret_key has no default on purpose: if it's missing from .env,
    # pydantic must fail loudly at startup instead of silently signing tokens
    # with a guessable value.
    secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    # SMTP - required, no defaults: without real credentials nothing should
    # silently pretend an email was sent when it wasn't.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str
    smtp_app_password: str
    # The address recipients see as the sender - distinct from smtp_username,
    # which is just the SMTP auth credential (e.g. Resend's username is the
    # literal string "resend", not a real mailbox). "onboarding@resend.dev"
    # is Resend's official address for testing before a custom domain is verified.
    smtp_from_email: str = "onboarding@resend.dev"

    # Base URL used to build links sent in emails (e.g. the verification
    # link). Points straight at the API for now since there's no frontend yet.
    app_base_url: str = "http://localhost:8000"

    # Symmetric encryption key for data that must be stored reversibly (e.g.
    # Garmin session tokens) - unlike secret_key (only ever used to sign/verify,
    # never to recover original data), this key can decrypt, so treat it with
    # at least as much care. No default: refuse to start rather than silently
    # store tokens encrypted with a guessable key.
    fernet_key: str

    # Telegram bot token from @BotFather - required, no default, same
    # fail-loudly-at-startup pattern as the other credentials above.
    telegram_bot_token: str

    # Anthropic API key for the AI coach. Deliberately OPTIONAL (unlike the
    # credentials above): everything that doesn't involve Claude - linking
    # Garmin, syncing, the metrics buttons - must keep working before a key
    # is configured. Code that needs it checks `is_claude_configured` and
    # degrades with a clear message instead of the whole bot failing to boot.
    anthropic_api_key: Optional[str] = None
    claude_model: str = "claude-opus-5"

    @property
    def is_claude_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


# single shared instance for the whole app - import this wherever config is needed
settings = Settings()
