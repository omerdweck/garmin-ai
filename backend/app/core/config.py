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
    # Optional, unlike the credentials above. Email is only ever sent by the
    # website's registration flow (POST /auth/register); the Telegram bot
    # identifies people by chat id and sends no mail at all. Requiring these
    # would force a bot-only deployment to carry a mail-sending credential
    # that nothing there can use - and every unused secret on a public
    # server is blast radius for no benefit.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_app_password: Optional[str] = None

    @property
    def is_email_configured(self) -> bool:
        return bool(self.smtp_username and self.smtp_app_password)
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

    # Telegram Mini App (the visual dashboard opened from the chat). The URL
    # is what the bot's menu button opens; empty means "no Mini App yet", and
    # the bot simply keeps its usual menu. Telegram only opens https URLs.
    miniapp_url: str = ""
    # How old a launch's initData may be. Telegram signs it when the user
    # taps the button, so an hour is generous for a real launch and short
    # enough that a leaked string stops working the same day.
    miniapp_init_data_max_age_seconds: int = 3600
    # Lifetime of the Mini App's own session token - separate from the
    # website's (access_token_expire_minutes, a day) on purpose: a phone
    # WebView left open is the riskier place for a long-lived token.
    miniapp_token_minutes: int = 60

    # Anthropic API key for the AI coach. Deliberately OPTIONAL (unlike the
    # credentials above): everything that doesn't involve Claude - linking
    # Garmin, syncing, the metrics buttons - must keep working before a key
    # is configured. Code that needs it checks `is_claude_configured` and
    # degrades with a clear message instead of the whole bot failing to boot.
    anthropic_api_key: Optional[str] = None
    # Sonnet rather than Opus: measured against this project's own traffic
    # (10 users, ~3 coach turns each per day) Opus came to ~$42/month against
    # Sonnet's ~$17 for work that is summarising health metrics and drafting a
    # training plan - not the kind of reasoning that pays for the top tier.
    # Override in .env to try another tier; nothing else has to change.
    claude_model: str = "claude-sonnet-5"

    @property
    def is_claude_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    # --- Per-user limits on Claude usage ---------------------------------
    # Two ceilings rather than one, because they catch different things: a
    # message cap stops compulsive back-and-forth, a cost cap stops the small
    # number of very long, tool-heavy conversations that a message count
    # would wave through. Both are per user; the quick-lookup buttons make no
    # API call at all and are deliberately never limited.
    #
    # 30/day is generous on purpose - measured at ~1.7 cents a turn, a
    # realistic day is 3-5 messages, so this only bites on genuinely
    # abnormal use rather than nagging ordinary users.
    daily_message_limit: int = 30
    monthly_cost_limit_usd: float = 3.0

    # Chat id that receives admin notifications and may run /admin. Optional:
    # without it the bot behaves exactly as before, just with no admin
    # channel - the feature degrades rather than failing to boot.
    admin_chat_id: Optional[int] = None

    @property
    def is_admin_configured(self) -> bool:
        return self.admin_chat_id is not None

    # --- Who may register ------------------------------------------------
    # Access is granted by the owner approving a named person through the
    # bot, so there is no shared secret to configure. This is the hard
    # ceiling above that: it counts existing users, so lowering it stops new
    # signups without ever removing anybody.
    #
    # Note that admin_chat_id above is what makes signup possible at all -
    # with no approver configured, nobody can be let in.
    max_users: int = 10

    # The website's own registration endpoint. Off because it cannot ask for
    # the owner's approval the way the bot does; see app/routers/auth.py.
    # Turning it on without building that approval step re-opens the only
    # gate this design has.
    website_registration_enabled: bool = False

    # Ceiling on what the scheduled and proactive AI work may spend per
    # calendar month, for the whole system: daily and weekly summaries and
    # the coach's own messages. Separate from the per-user limits above,
    # which only cover conversations a user starts - nothing else stops a bug
    # that sends a thousand "workout analysed" messages. Reaching it stops
    # those messages (never the chat) and tells the owner once.
    ai_monthly_budget_usd: float = 30.0

    # The 30-minute "has the watch uploaded?" check behind the post-workout
    # message. Off means that message waits for the next scheduled sync.
    watch_poll_enabled: bool = True

    # --- Anomaly alerts ---------------------------------------------------
    # Each rule compares the last few days with the athlete's own previous
    # four weeks. The numbers are here, not in the rules, so tuning one does
    # not mean touching logic - and the tests read them from here too.
    #
    # Days of data a baseline needs before any rule may lean on it. Two
    # weeks: fewer and one bad night moves the "normal" itself.
    anomaly_min_baseline_days: int = 14
    # HRV: three falls in a row, ending this far below the usual (in
    # standard deviations). Half a deviation: the run of falls already
    # filters out most noise.
    anomaly_hrv_std_factor: float = 0.5
    # Resting HR: beats above the usual, on two of the last three days.
    anomaly_rhr_delta: int = 5
    # Sleep: a night shorter than this, or scored lower, counts as poor.
    anomaly_sleep_min_minutes: int = 360
    anomaly_sleep_min_score: int = 60
    # Body Battery: three days in a row without recharging above this.
    anomaly_body_battery_recovery: int = 50
    # Easy runs: metres per heartbeat this many percent below the usual on
    # two of the last three.
    anomaly_easy_run_drop_pct: float = 4.0
    # Load: Garmin's acute:chronic ratio above this, or a week this many
    # percent above the four before it.
    anomaly_acwr_max: float = 1.5
    anomaly_volume_jump_pct: float = 30.0
    # Below these there is too little training for a ratio to mean anything:
    # 20 minutes after a week of 10 is "+100%" and nothing to warn about.
    anomaly_min_weekly_minutes: int = 90
    anomaly_min_chronic_load: int = 50

    # --- One-time import of each user's past year ------------------------
    # Who it runs for: "off", "admin" (only the owner - to measure it on one
    # account before everyone) or "all". Starts at "admin" on purpose.
    history_import_scope: str = "admin"
    # A year for the Mini App's year view, plus six weeks before it: Fitness
    # is a 42-day average, and without those weeks its line would start the
    # year at zero and climb for no reason.
    history_import_days: int = 365 + 42
    # Only at night (Israel time), when nobody is waiting on a sync. The
    # window wraps midnight: 23:00 until 06:00.
    history_import_start_hour: int = 23
    history_import_end_hour: int = 6
    # Days per task run, and the pause between Garmin calls. A day is four
    # calls, so 25 days is ~100 calls spread over ~5 minutes - well inside
    # the task's time limit and far gentler than the routine backfill.
    history_import_chunk_days: int = 25
    history_import_pause_seconds: float = 2.5

    # --- Workouts on the watch ---------------------------------------------
    # Sports whose workouts may be sent to anyone's Garmin account, comma
    # separated (e.g. "running,cycling"). Empty by default on purpose: the
    # Garmin workout API is unofficial, and a sport is opened only after its
    # workouts were sent once to the owner's own watch and looked right
    # there. The owner (admin_chat_id) can always send, so that check is
    # possible before anyone else gets the button.
    workout_upload_sports: str = ""

    @property
    def workout_upload_sport_set(self) -> set[str]:
        return {s.strip() for s in self.workout_upload_sports.split(",") if s.strip()}

    # --- Concurrency ------------------------------------------------------
    # How many coach conversations may run at the same time. Each one holds
    # a thread and a database connection for as long as Claude takes to
    # answer (often 10-30s), so this is the real capacity number of the bot.
    # Must stay below the database pool (5 + 10 overflow per process), or a
    # burst of questions would starve the buttons of connections.
    coach_max_concurrent: int = 8

    # Ceiling on updates the bot holds at once, across all users. Updates
    # waiting for the same user's previous one count too, so this is set well
    # above coach_max_concurrent: a waiting update costs almost nothing.
    bot_max_concurrent_updates: int = 64

    # Updates one user may have waiting behind their own unfinished one.
    # Beyond that, extra messages are dropped - a stuck finger or a script
    # must not be able to fill the queue every other user shares.
    bot_max_pending_per_user: int = 5

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


# single shared instance for the whole app - import this wherever config is needed
settings = Settings()
