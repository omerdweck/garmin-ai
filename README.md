# Garmin AI

A Telegram bot that turns a Garmin watch into a personal training coach.

People link their own Garmin account through a chat conversation, then either
tap a button for a specific number or talk to an AI coach that reads their
real training data and builds plans around it. It runs for a small, invited
group — around ten people — on a single small server.

> **Note on scope.** This is a personal project run by an individual, not a
> product. It stores other people's health data, uses an unofficial Garmin
> interface, and offers no warranty or uptime guarantee. If you are thinking
> of running it for anyone but yourself, read [Operating it for other
> people](#operating-it-for-other-people) first.

---

## What it does

**Instant lookups** — one tap each, answered straight from the database with
no AI call: resting heart rate, steps, sleep breakdown, recovery, the week so
far, a full metrics snapshot, and a browsable workout history with
per-exercise muscle groups for strength sessions.

**Training load** — Garmin's own acute (7-day) and chronic (28-day) load,
training status, load balance, readiness score and recommended recovery time.
Read from Garmin rather than derived from stored activities: an acute:chronic
ratio computed from durations would be a worse version of a number Garmin
already produces from sensor data we never see. The ratio is what makes a
week heavy or light *for that person* — the same 300 is overreaching for one
user and a taper for another.

**Manual workout entry** — for sessions done without the watch. Buttons for the
common cases, typed input for everything else, stored in the same table as
Garmin's own workouts with the fields it cannot know left NULL rather than
guessed. The entry screen warns up front that this is only for workouts the
watch did not record, because nothing merges a manual row with a Garmin one
that arrives later.

**An AI coach** — a real conversation. It calls tools to read the user's
metrics, training load, workouts, goal and saved training plans, so its
answers cite actual numbers rather than generalities. It remembers a stated
goal ("sub-50 10K by December") and the plans it prescribes, across
conversations.

A plan the coach writes is a **proposal**, not a decision. It is stored
inactive and enters tracking only when the user presses approve — so revising
it during a conversation never disturbs the plan they are currently following,
and the daily summary never announces a commitment nobody made.

It is not, and does not present itself as, medical advice — the system prompt
sends anything health-related to a professional rather than reasoning about
it.

**Calorie tracking** — entirely optional, and off unless the user sets a
target. Intake is logged by hand against Garmin's *total* daily expenditure,
not workout calories: comparing a 2,000 kcal intake against a 400 kcal workout
would report an enormous surplus every day. Up to three daily reminders, and an
offer to stop tracking after five silent days rather than nagging someone who
has given up. A day the watch was not worn has no balance and says so — the
user can type the real figure or use their own weekly average, both labelled as
such.

**Personal meal plans** — 2-3 options per meal slot rather than a fixed weekly
menu, because what is in the fridge decides. Tracked and revised like a
training plan.

**Automatic syncing** — twice daily, plus a manual button. Every sync reports
when the *watch* last uploaded to Garmin, and warns when that was long enough
ago to be the real reason data looks stale.

**A daily summary** — a short end-of-day message at an hour each user picks.

**Owner controls** — signup by owner approval, a user cap, per-user message
and cost ceilings, and per-call cost accounting.

---

## Architecture

Six containers, one `docker compose` stack:

| Service | What it is |
|---|---|
| `bot` | The Telegram bot (long polling). The product. |
| `api` | FastAPI. Serves the paused website's auth/Garmin endpoints and `/health`. |
| `worker` | Celery worker — Garmin syncs, backfills, daily summaries. |
| `beat` | Celery scheduler — twice-daily sync, hourly summary and calorie-reminder dispatch. |
| `db` | PostgreSQL 16. Publishes no port. |
| `redis` | Celery broker. Publishes no port. |

```
Telegram  ──►  bot  ──┐
                      ├──►  PostgreSQL
Celery beat ──► worker┘         ▲
                  │             │
                  ├──► Garmin Connect (unofficial API)
                  └──► Anthropic API (coach + daily summary)
```

The bot calls the service layer directly rather than going through the API
over HTTP — it is the same trusted codebase, so a network hop and a second
auth mechanism would buy nothing.

### Layout

```
backend/app/
  bot.py              Telegram handlers, menus, onboarding conversation
  bot_texts.py        Long user-facing copy (welcome, terms) — Hebrew
  tasks.py            Celery tasks: sync, backfill, daily summary
  celery_app.py       Celery config and the beat schedule
  core/
    claude_client.py  The coach: tool definitions, tool loop, usage metering
    claude_prompts.py System prompts
    claude_pricing.py Per-model token prices
    garmin_client.py  The only module that imports garminconnect
    crypto.py         Fernet encryption for stored Garmin tokens
    config.py         Every environment variable, in one place
  models/             SQLModel tables
  services/
    garmin_sync.py    Pull from Garmin, upsert into our tables
    metrics_view.py   Format metrics for Telegram (no AI involved)
    activity_view.py  Format workouts, per type
    manual_activity.py    Workouts entered by hand, and parsing what was typed
    plan_view.py      Render, approve and delete training plans
    calorie_tracking.py   Intake, burn, the balance and its sources
    meal_plan_view.py Render and delete a meal plan
    account_lifecycle.py  Link / disconnect / delete
    signup_control.py Join requests, owner approval and the user cap
    usage_limits.py   Per-user quotas and the usage report
    admin_view.py     Owner-facing reports
  routers/            FastAPI endpoints (website, currently paused)
scripts/
  deploy.sh           Pull, build, migrate, recreate, verify
  backup.sh           Encrypted nightly database dump
  restore.sh          Decrypt a backup (needs the private key)
  install-backup.sh   Install the nightly systemd timer
```

### Design decisions worth knowing

**Buttons never call the AI.** Restating numbers the database already holds
exactly is the easiest way to spend tokens on nothing. Claude is reserved for
the conversational path, where interpretation is the actual product.

**Every Claude tool is scoped to one `user_id`, bound from the caller.** The
model never supplies or influences whose data is read. That is what makes one
bot safe to run for many people.

**All timestamps and uniqueness are per user.** `(user_id, date)`,
`(user_id, garmin_activity_id)`, `(user_id, discipline, is_active)` — two people can
even link the same Garmin account without colliding.

**Garmin passwords are never stored.** They are used once to log in, then an
encrypted session token is stored instead. The Telegram message containing
the password is deleted from the chat immediately.

**Absence is stored, never inferred.** A rest day is a row saying "rest", not
a missing row. A day with no burn figure has no balance rather than a zero. A
manual workout's heart rate is NULL rather than a guess. In each case the
alternative would have the system asserting something nobody measured.

**Nothing the coach writes takes effect on its own.** Training plans wait for
approval; calorie tracking does nothing until a target is set; a stale
Garmin figure is never silently replaced by an estimate. The model proposes,
the user decides.

**Claude's cached prefix is kept byte-stable.** The current date goes in the
user turn, never the system prompt — a date inside the cached prefix would
change daily and silently destroy every cache hit.

---

## Running it locally

Requires Docker and Docker Compose.

```bash
cp .env.example .env      # then fill in the values it documents
docker compose up --build -d
docker compose exec api alembic upgrade head
curl http://localhost:8000/health
```

You will need, at minimum, a `TELEGRAM_BOT_TOKEN` from
[@BotFather](https://t.me/BotFather). Without `ANTHROPIC_API_KEY` everything
works except the coach and the daily summary.

`.env.example` documents every variable, which are optional, and how to
generate the ones that must be random.

### Migrations

Schema changes are Alembic migrations, generated **locally** and only ever
applied in production:

```bash
docker compose exec api alembic revision --autogenerate -m "what changed"
docker compose exec api alembic upgrade head
```

Two recurring gotchas:

- autogenerate does not add `import sqlmodel`, which the generated file needs
  for `AutoString` columns. Add it by hand.
- `docker-compose.prod.yml` has no bind mounts, so running autogenerate on
  the server writes the file inside a container that `--rm` then deletes.

---

## Deployment

Production runs from `docker-compose.prod.yml`, which differs from the
development file by removals: no bind mounts (the image carries the code, so
what runs is exactly what was built), no `--reload`, and the API bound to
`127.0.0.1` only.

Deploy with the script, never with a bare `docker compose up -d`:

```bash
./scripts/deploy.sh
```

It pulls, builds, migrates, **force-recreates**, and then verifies what is
actually running. That last part is not ceremony: plain `up -d` repeatedly
reported every service as "Started" while the containers kept running the
previous image, so a deploy that changed nothing looked identical to one that
worked.

### Server setup

Any small VPS will do — measured usage is well under 1 GB of RAM across all
six containers. The steps are: a non-root sudo user, key-only SSH with root
login and password auth disabled, `ufw` allowing only port 22, `fail2ban`,
unattended upgrades, Docker Engine, a read-only GitHub deploy key, and
`.env`.

Note that **Docker bypasses ufw** by writing iptables rules directly. The
real protection for the API is binding it to `127.0.0.1` in the compose file,
not the firewall.

### Backups

`scripts/install-backup.sh` installs a systemd timer that dumps the database
nightly, encrypted.

Encryption is public-key: the private half is generated on the operator's
laptop and never uploaded, so **the server can write backups and cannot read
them**. A stolen disk or a compromised server yields ciphertext. A passphrase
in `.env` would have sat next to the data it protects.

The trade-off is absolute: lose that private key and every backup is
permanently unreadable. There is no recovery path, by design.

```bash
./scripts/restore.sh garmin_ai-20260906-033000.sql.gz.enc
```

---

## Operating it for other people

Handing this to friends changes what it is. Some of this is built; some is
your responsibility.

**Built in:**

- **Signup by approval.** A newcomer taps "request access" and the owner
  approves them by name, with their Telegram handle and numeric chat id shown
  — the id being the part nobody can choose. This replaced a shared invite
  code: a code forwarded in a group chat stops being a secret and the owner
  never finds out, where an approval names the person before they are in.
  A rejected request is not reopened by asking again.
- **A hard user cap.** `MAX_USERS` is checked before a request is even sent,
  and `/auth/register` on the paused website is closed outright — an
  anonymous HTTP caller cannot obtain an approval, so leaving it open would
  be a way around the only gate the design has.
- **Per-user ceilings.** A daily message count and a monthly cost cap,
  checked *before* the API call. They catch different things: a message count
  stops compulsive back-and-forth, a cost cap stops the long tool-heavy
  conversations a message count waves through.
- **Cost accounting per API call**, recorded at the one choke point every
  request passes through, so tool-loop rounds are counted rather than missed.
- **`/admin`** — usage per user, quota changes, and full deletion. Answers
  non-owners with silence rather than a refusal, which would confirm it
  exists.
- **Complete deletion.** The table list is derived from the model metadata,
  so a future table cannot be forgotten.

**Still yours to handle:**

- **Garmin's terms.** The connection uses an unofficial, reverse-engineered
  interface. Using it may not be consistent with Garmin's terms of service,
  and Garmin rate-limits by IP — which in production means one address for
  everybody. Onboard one or two people a day, not five in an evening: each
  new user triggers a 30-day backfill of roughly 120 Garmin calls.
- **You are the support desk.** There is nobody else.
- **You can read everyone's data,** and the terms of use say so plainly.
  Anyone you invite should understand that before they link an account.
- **Rate limiting** if you ever expose the API. `/auth/register` and
  `/auth/login` need it at the reverse proxy; an HTTP endpoint has no
  equivalent of the bot's per-conversation attempt counter.

---

## Conventions

- **Code comments and docstrings are English.** User-facing bot text is
  Hebrew.
- **Comments explain *why*.** What the code does is in the code.
- **Every environment variable flows through `app/core/config.py`.** Nothing
  reads `os.environ` directly, so "where does configuration come from" has
  one answer.
- **`garminconnect` is imported in exactly one module.** It is an unofficial
  client; when Garmin changes something, one file needs fixing.

## Status

Deployed and running. The website is paused — its endpoints still work and
are gated, so it can be resumed without rebuilding authentication.
