"""
The Mini App's server process - separate from app/main.py on purpose.

app/main.py serves the paused website (/auth/login, /auth/register,
/garmin/link) and is bound to 127.0.0.1 on the server. This app is the one
Caddy exposes to the internet, so it contains the Mini App routes and
nothing else: a route that isn't registered here cannot be reached from
outside, whatever happens to the Caddyfile.

It also runs as a different database role (see docker-compose.prod.yml and
the miniapp_readonly migration) that can only read, and cannot read Garmin
tokens, passwords or coach conversations at all.
"""

from fastapi import FastAPI

from app import models  # noqa: F401 - registers every table, so foreign keys resolve
from app.routers import miniapp

# docs_url/redoc_url/openapi_url off: an interactive map of every endpoint
# is useful on a laptop and a gift to anyone probing the public site.
app = FastAPI(title="Garmin AI - Mini App", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(miniapp.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
