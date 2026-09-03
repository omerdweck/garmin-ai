FROM python:3.12-slim

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Alembic has to be in the image, not just on the developer's disk: in
# production there's no bind mount, and without these files
# `alembic upgrade head` on the server has no migrations to run - the
# schema would simply never be created.
COPY alembic.ini .
COPY alembic ./alembic

# Development default. The production compose file overrides `command`,
# since a file watcher restarting the app mid-deploy is an outage, not a
# feature.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "app"]
