"""
Owner-facing reports and actions, rendered for Telegram.

Separate from metrics_view because the audience is different: that module
formats one user's data for that user, this one formats everyone's usage for
whoever runs the bot. Keeping them apart is also what makes it obvious at a
glance that no ordinary user path imports anything from here.

Text is Hebrew, like every other user-facing string; comments stay English.
"""

from datetime import datetime, timezone

from sqlmodel import Session, func, select

from app.core.config import settings
from app.models.garmin_account import GarminAccount
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.services.signup_control import decide, pending_requests, signup_status
from app.services.usage_limits import usage_report

RLM = "‏"


def _rtl(text: str) -> str:
    return f"{RLM}{text}"


def format_admin_summary(session: Session) -> str:
    """Headline numbers: how many people, what they cost, who stands out."""
    rows = usage_report(session)
    total_cost = sum(r["cost_usd"] for r in rows)

    linked = session.exec(
        select(func.count())
        .select_from(GarminAccount)
        .where(GarminAccount.disconnected_at.is_(None))
    ).one()

    signup = signup_status(session)

    month = datetime.now(timezone.utc).strftime("%m/%Y")
    lines = [
        _rtl(f"📊 *ניהול - {month}*"),
        "",
        _rtl(f"משתמשים: *{signup['users']}/{signup['max_users']}*"),
        _rtl(f"מחוברים לגרמין: *{linked}*"),
        _rtl(f"עלות החודש: *${total_cost:.2f}*"),
        _rtl(f"מודל: {settings.claude_model}"),
        "",
    ]

    # Without an approver nobody can ever be let in, which is a state worth
    # shouting about rather than discovering when a friend says the bot
    # ignored them.
    if not signup["approver_configured"]:
        lines.append(_rtl("⚠️ *אין מאשר מוגדר* - אף אחד לא יכול להצטרף. הגדר ADMIN_CHAT_ID"))
    else:
        lines.append(_rtl("🔒 הרשמה: באישור שלך"))

    if signup["pending"]:
        lines.append(_rtl(f"🙋 *ממתינות לאישור: {signup['pending']}* - `/admin requests`"))
    if signup["full"]:
        lines.append(_rtl("🚧 מלא - הרשמות חדשות נחסמות"))

    lines += [
        "",
        _rtl(f"מכסות: {settings.daily_message_limit} הודעות ליום, ${settings.monthly_cost_limit_usd:.2f} לחודש"),
    ]

    # Only worth naming people when someone is actually approaching a limit -
    # a list of everyone is what /admin users is for.
    heavy = [r for r in rows if r["cost_usd"] >= settings.monthly_cost_limit_usd * 0.7]
    if heavy:
        lines += ["", _rtl("⚠️ *מתקרבים למכסה:*")]
        for r in heavy:
            lines.append(_rtl(f"  משתמש {r['user_id']}: ${r['cost_usd']:.2f}"))

    lines += ["", _rtl("`/admin users` לרשימה מלאה")]
    return "\n".join(lines)


def format_admin_users(session: Session) -> str:
    """Every registered user with this month's usage, heaviest first."""
    rows = usage_report(session)
    if not rows:
        return _rtl("אין משתמשים רשומים.")

    lines = [_rtl("👥 *משתמשים* (החודש)"), ""]
    for r in rows:
        limit = r["daily_limit"]
        limit_note = " · 🚫 חסום" if limit == 0 else ("" if limit == settings.daily_message_limit else f" · מכסה {limit}")
        lines.append(
            _rtl(f"*#{r['user_id']}* · chat `{r['telegram_chat_id']}`{limit_note}")
        )
        lines.append(
            _rtl(f"   ${r['cost_usd']:.3f} · {r['api_calls']} קריאות · {r['tokens']:,} טוקנים")
        )
    lines += [
        "",
        _rtl("`/admin limit <id> <n>` - שינוי מכסה (0 = חסימה)"),
        _rtl("`/admin delete <id>` - מחיקה מלאה"),
    ]
    return "\n".join(lines)


def format_pending_requests(session: Session) -> str:
    """
    Anyone still waiting. Exists because the approval buttons arrive as a
    Telegram notification, and notifications get swiped away - without this
    a missed one means someone waits forever with no way to ask again.
    """
    requests = pending_requests(session)
    if not requests:
        return _rtl("אין בקשות ממתינות ✓")

    lines = [_rtl("🙋 *בקשות ממתינות*"), ""]
    for r in requests:
        handle = f"@{r.telegram_username}" if r.telegram_username else "אין שם משתמש"
        lines.append(_rtl(f"*{r.display_name or 'לא צוין'}* · {handle}"))
        lines.append(_rtl(f"   מזהה: `{r.telegram_chat_id}` · {r.requested_at.strftime('%d/%m %H:%M')}"))
    lines += [
        "",
        _rtl("`/admin approve <chat_id>` · `/admin reject <chat_id>`"),
        _rtl("⚠️ השם והיוזר נבחרים על ידי המבקש - אשר רק אם אתה מזהה."),
    ]
    return "\n".join(lines)


def set_user_limit(session: Session, user_id: int, limit: int) -> str:
    user = session.get(User, user_id)
    if user is None:
        return _rtl(f"❌ אין משתמש {user_id}")

    user.daily_message_limit = limit
    session.add(user)
    session.commit()

    if limit == 0:
        return _rtl(f"🚫 משתמש {user_id} נחסם משיחה עם המאמן (הנתונים נשמרו).")
    return _rtl(f"✅ מכסת משתמש {user_id} עודכנה ל-{limit} הודעות ליום.")


def format_new_user_alert(session: Session, user: User) -> str:
    """Sent to the owner the moment someone registers."""
    total = session.exec(select(func.count()).select_from(User)).one()
    return "\n".join(
        [
            _rtl("🆕 *משתמש חדש נרשם*"),
            _rtl(f"מזהה: {user.id} · chat `{user.telegram_chat_id}`"),
            _rtl(f"סה\"כ משתמשים: *{total}*"),
        ]
    )
