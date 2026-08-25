"""
Celery task definitions - thin wrappers around app.services.garmin_sync
that add task-queue behavior (retries, being callable via .delay()).
The actual sync logic stays in the service layer and knows nothing
about Celery; this file only knows "how do I turn that logic into
something Celery can schedule/queue/retry".
"""

from sqlmodel import Session, select

from app.celery_app import celery_app
from app.core.garmin_client import GarminAuthError, GarminRateLimitError
from app.db.session import engine
from app.models.garmin_account import GarminAccount
from app.services.garmin_sync import sync_user_garmin_data


@celery_app.task(bind=True, max_retries=3, default_retry_delay=300)
def sync_one_user_task(self, user_id: int) -> None:
    """
    One task per user - if one person's sync fails (expired token,
    Garmin temporarily down), it doesn't block or fail anyone else's,
    and Celery can retry just this task instead of redoing everyone.
    A fresh session is opened here rather than reusing a FastAPI request's
    session, since this task runs in a separate worker process with no
    request in progress at all.
    """
    with Session(engine) as session:
        try:
            sync_user_garmin_data(session=session, user_id=user_id)
        except GarminRateLimitError as exc:
            # Garmin is throttling, not a real failure - back off and retry later.
            raise self.retry(exc=exc, countdown=300)
        except GarminAuthError:
            # Bad/expired token - retrying won't fix that. sync_user_garmin_data
            # already recorded this on GarminAccount.last_sync_error.
            return


@celery_app.task
def sync_all_linked_accounts() -> None:
    """
    Runs on Beat's daily schedule. Fans out: for every linked Garmin
    account, enqueues a separate sync_one_user_task instead of looping
    and doing the work directly here - that's what gives each user
    isolation and independent retries.
    """
    with Session(engine) as session:
        user_ids = session.exec(select(GarminAccount.user_id)).all()

    for user_id in user_ids:
        sync_one_user_task.delay(user_id)
