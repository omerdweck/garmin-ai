"""
Wires every handler into the Telegram Application, in the order that matters.

Moved verbatim from the former single-file app/bot.py.
"""
import logging

from telegram import MenuButtonWebApp, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot_concurrency import build_update_processor
from app.core.config import settings
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.records import format_records
from app.services.metrics_view import (
    format_race_predictions,
    format_week,
)
from app.bot.activities import on_activity_detail, on_activity_type, on_activity_types_menu, on_records, show_activity_types
from app.bot.admin import admin
from app.bot.calories import on_calorie_reminder, on_calorie_step, show_calories
from app.bot.coach import enter_chat_mode, exit_chat_mode, on_talk_about_it, show_menu, talk_to_coach
from app.bot.common import LEGACY_BTN_PLAN, LEGACY_BTN_WEEK, BTN_ACTIVITIES, BTN_ADD_ACTIVITY, BTN_BUILD_WORKOUT, BTN_CALORIES, BTN_COACH, BTN_EXIT_CHAT, BTN_METRICS, BTN_PLAN, BTN_RACES, BTN_RECORDS, BTN_SETTINGS, BTN_SYNC, BTN_WEEK
from app.bot.manual_activity import add_activity_start, on_manual_step, on_manual_text
from app.bot.metrics import _quick_lookup, on_metric_drilldown, show_metrics, sync_now
from app.bot.onboarding import ASK_EMAIL, ASK_PASSWORD, AWAITING_TERMS, ask_password, cancel, do_link, on_join_decision, on_join_request, on_terms_response, start
from app.bot.profile import on_profile_action, show_profile
from app.bot.plans import on_plan_action, on_plan_cancel
from app.bot.schedule import on_schedule_nav, on_week_review, show_schedule
from app.bot.workouts import build_workout_menu, on_builder_step, on_workout_action
from app.bot.preferences import on_account_action, on_notify_setting, on_settings_action, on_summary_hour, on_weekly_setting, show_settings

logger = logging.getLogger(__name__)



def build_application() -> Application:
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        # Different users in parallel, each user's own updates in order -
        # see app/bot_concurrency.py for why plain concurrency is not enough.
        .concurrent_updates(build_update_processor())
        .build()
    )

    onboarding = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            # Only the inline buttons advance from here - typed text is
            # ignored until the terms are answered one way or the other.
            AWAITING_TERMS: [CallbackQueryHandler(on_terms_response, pattern=r"^terms:")],
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, do_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(onboarding)

    application.add_handler(CommandHandler("menu", show_menu))
    application.add_handler(CommandHandler("sync", sync_now))
    application.add_handler(CommandHandler("records", _quick_lookup(format_records)))
    application.add_handler(CommandHandler("settings", show_settings))
    application.add_handler(CommandHandler("profile", show_profile))
    # Deliberately absent from set_my_commands below - see admin().
    application.add_handler(CommandHandler("admin", admin))

    # Exact-text handlers for the menu buttons, registered before the
    # catch-all so a button tap is never mistaken for a typed question -
    # including while in chat mode, so the exit button always works.
    for button_text, handler in (
        (BTN_METRICS, show_metrics),
        (BTN_ACTIVITIES, show_activity_types),
        (BTN_WEEK, _quick_lookup(format_week)),
        (LEGACY_BTN_WEEK, _quick_lookup(format_week)),
        (BTN_RACES, _quick_lookup(format_race_predictions)),
        (BTN_RECORDS, _quick_lookup(format_records)),
        (BTN_PLAN, show_schedule),
        (LEGACY_BTN_PLAN, show_schedule),
        (BTN_ADD_ACTIVITY, add_activity_start),
        (BTN_CALORIES, show_calories),
        (BTN_SYNC, sync_now),
        (BTN_COACH, enter_chat_mode),
        (BTN_SETTINGS, show_settings),
        (BTN_EXIT_CHAT, exit_chat_mode),
        (BTN_BUILD_WORKOUT, build_workout_menu),
    ):
        application.add_handler(MessageHandler(filters.Text([button_text]), handler))

    application.add_handler(CallbackQueryHandler(on_summary_hour, pattern=r"^summary_hour:"))
    application.add_handler(CallbackQueryHandler(on_workout_action, pattern=r"^wk:"))
    application.add_handler(CallbackQueryHandler(on_week_review, pattern=r"^wkw:"))
    application.add_handler(CallbackQueryHandler(on_schedule_nav, pattern=r"^sch:"))
    application.add_handler(CallbackQueryHandler(on_builder_step, pattern=r"^wkb:"))
    application.add_handler(CallbackQueryHandler(on_settings_action, pattern=r"^settings:"))
    application.add_handler(CallbackQueryHandler(on_weekly_setting, pattern=r"^weekly:"))
    application.add_handler(CallbackQueryHandler(on_notify_setting, pattern=r"^notify:"))
    application.add_handler(CallbackQueryHandler(on_talk_about_it, pattern=r"^talk:"))
    application.add_handler(CallbackQueryHandler(on_profile_action, pattern=r"^profile:"))
    # plancancel: before plan: - the narrower pattern has to win.
    application.add_handler(CallbackQueryHandler(on_plan_cancel, pattern=r"^plancancel:"))
    application.add_handler(CallbackQueryHandler(on_plan_action, pattern=r"^plan:"))
    application.add_handler(CallbackQueryHandler(on_metric_drilldown, pattern=r"^metric:"))
    application.add_handler(CallbackQueryHandler(on_manual_step, pattern=r"^manual:"))
    # calrem: before cal: - the narrower pattern has to win, same as
    # acttype_menu before acttype below.
    application.add_handler(CallbackQueryHandler(on_calorie_reminder, pattern=r"^calrem:"))
    application.add_handler(CallbackQueryHandler(on_calorie_step, pattern=r"^cal:"))
    # Group -1: runs before the coach handler, which matches every plain
    # message. Without this a typed "37" would go to Claude as a question.
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_manual_text), group=-1
    )
    application.add_handler(CallbackQueryHandler(on_account_action, pattern=r"^account:"))
    # Outside the conversation on purpose: approval arrives out of band,
    # minutes or hours later, so neither side can be held in a state.
    application.add_handler(CallbackQueryHandler(on_join_request, pattern=r"^join:request$"))
    application.add_handler(CallbackQueryHandler(on_join_decision, pattern=r"^join_decide:"))
    # Ordered narrowest-first: "acttype_menu" would otherwise be swallowed
    # by the broader "acttype:" pattern.
    application.add_handler(CallbackQueryHandler(on_records, pattern=r"^records$"))
    application.add_handler(CallbackQueryHandler(on_activity_types_menu, pattern=r"^acttype_menu$"))
    application.add_handler(CallbackQueryHandler(on_activity_type, pattern=r"^acttype:"))
    application.add_handler(CallbackQueryHandler(on_activity_detail, pattern=r"^act:"))

    # Typed text - answered by the coach only while in chat mode, otherwise
    # pointed back at the buttons.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, talk_to_coach))

    return application


async def _register_commands(application: Application) -> None:
    """
    Publishes the slash commands so they show up in Telegram's own "/" menu -
    a discoverable fallback for anyone whose reply keyboard is hidden.
    """
    await application.bot.set_my_commands(
        [
            ("menu", "הצג את תפריט הכפתורים"),
            ("sync", "סנכרון מול גרמין"),
            ("settings", "הגדרות"),
            ("profile", "מה המאמן יודע עליי"),
            ("start", "חיבור חשבון גרמין"),
        ]
    )

    # The button left of the message box opens the Mini App dashboard. Set
    # without a chat_id, so it is everyone's default. It takes the place of
    # Telegram's "/" menu button - the commands above still work, and still
    # appear as soon as someone types "/".
    if settings.miniapp_url:
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="📊 הדשבורד", web_app=WebAppInfo(url=settings.miniapp_url))
        )


def main() -> None:
    application = build_application()
    application.post_init = _register_commands
    logger.info("Starting Telegram bot (long polling)")
    application.run_polling()
