"""
The Celery application instance - the object that turns a decorated
Python function into a background task, and knows how to talk to Redis
(the broker) to hand work off to / receive it from workers.

Three separate processes point at this same module:
- the FastAPI app, when it enqueues a task with `.delay(...)`
- the `worker` service (docker-compose), started as
  `celery -A app.celery_app worker` - actually executes tasks
- the `beat` service, started as `celery -A app.celery_app beat` - the
  scheduler that enqueues tasks on a timer, but never executes them itself
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

redis_url = f"redis://{settings.redis_host}:{settings.redis_port}/0"

celery_app = Celery(
    "garmin_ai",
    broker=redis_url,
    backend=redis_url,
    # Registers @celery_app.task-decorated functions in app/tasks.py -
    # Celery imports this module itself once the app starts, instead of
    # us importing app.tasks directly here (which would be circular:
    # app/tasks.py needs `celery_app` to already exist to decorate with it).
    include=["app.tasks"],
)

# Beat schedule times are in UTC by default - set explicitly so "06:00"
# means 06:00 Israel time, not UTC.
celery_app.conf.timezone = "Asia/Jerusalem"

celery_app.conf.beat_schedule = {
    # Twice a day: 08:00 catches the previous night's finalized sleep/HRV
    # data, 20:00 catches the day's steps/activities/stress before bed.
    "sync-all-garmin-accounts-morning": {
        "task": "app.tasks.sync_all_linked_accounts",
        "schedule": crontab(hour=8, minute=0),
    },
    "sync-all-garmin-accounts-evening": {
        "task": "app.tasks.sync_all_linked_accounts",
        "schedule": crontab(hour=20, minute=0),
    },
    # Wakes up every hour and dispatches summaries to whoever chose that
    # hour - one static schedule entry regardless of how many different
    # times users pick. Runs at :05 so it lands just after the 20:00 sync
    # rather than racing it, giving the evening summary fresh data.
    "dispatch-daily-summaries": {
        "task": "app.tasks.dispatch_daily_summaries",
        "schedule": crontab(minute=5),
    },
}
