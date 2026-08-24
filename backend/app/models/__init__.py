"""
Import all models here, so that `SQLModel.metadata` "knows about" every
table as soon as someone does `import app.models` - even if nothing in
this file directly uses User. Without this, `init_db()` would not create
the table, because Python would never have actually loaded the class
that defines it.
"""

from app.models.user import User  # noqa: F401
