"""
כל הגדרות הסביבה (env vars) עוברות דרך המקום הזה, ולא נקראות ישירות
עם os.environ בשום מקום אחר בקוד - כך יש נקודה אחת ברורה של "מאיפה
המערכת מקבלת את התצורה שלה", וגם type-checking אוטומטי (pydantic
יזרוק שגיאה ברורה אם משתנה סביבה חסר, במקום כשל מוזר באמצע ריצה).
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


# instance יחיד ומשותף לכל האפליקציה - נייבא את זה בכל מקום שצריך תצורה
settings = Settings()
