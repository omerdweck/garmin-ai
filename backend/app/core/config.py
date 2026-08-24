"""
All environment variables flow through this one place, instead of being
read directly with os.environ anywhere else in the code - so there's a
single clear answer to "where does the system get its configuration from",
plus automatic type-checking (pydantic raises a clear error if an env var
is missing, instead of a weird failure mid-run).
"""

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

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


# single shared instance for the whole app - import this wherever config is needed
settings = Settings()
