"""
Import all models here, so that `SQLModel.metadata` "knows about" every
table as soon as someone does `import app.models` - even if nothing in
this file directly uses these classes. Alembic's autogenerate (see
backend/alembic/env.py) relies on this to diff the real DB against the
full set of models, not just whichever one happens to get imported first.
"""

from app.models.activity import Activity  # noqa: F401
from app.models.chat_message import ChatMessage  # noqa: F401
from app.models.daily_metric import DailyMetric  # noqa: F401
from app.models.garmin_account import GarminAccount  # noqa: F401
from app.models.training_plan import TrainingPlan  # noqa: F401
from app.models.usage_event import UsageEvent  # noqa: F401
from app.models.user import User  # noqa: F401
