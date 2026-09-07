"""
Reading and cancelling training plans without involving Claude.

Same principle as metrics_view: the plan is already stored exactly, so
showing it or deleting it costs nothing and should never spend a token.
Claude is for building and revising a plan - not for reciting one.
"""

from datetime import date as date_type
from datetime import timedelta

from sqlmodel import Session, col, delete, select

from app.models.plan_session import DAY_NAMES_HE, PlanSession
from app.models.training_plan import TrainingPlan

RLM = "‏"

DISCIPLINE_LABELS = {
    "running": "🏃 ריצה",
    "swimming": "🏊 שחייה",
    "cycling": "🚴 אופניים",
    "strength": "🏋️ כוח",
    "general": "💪 כללי",
}


def _rtl(text: str) -> str:
    return f"{RLM}{text}"


def discipline_label(discipline: str) -> str:
    return DISCIPLINE_LABELS.get(discipline, discipline)


def active_plans(session: Session, user_id: int) -> list[TrainingPlan]:
    """Approved plans only - a proposal the user never accepted is not theirs."""
    return list(
        session.exec(
            select(TrainingPlan).where(
                TrainingPlan.user_id == user_id,
                TrainingPlan.is_active == True,  # noqa: E712 - SQL comparison, not Python
            )
        ).all()
    )


def pending_plans(session: Session, user_id: int) -> list[TrainingPlan]:
    return list(
        session.exec(
            select(TrainingPlan).where(
                TrainingPlan.user_id == user_id,
                TrainingPlan.is_active == False,  # noqa: E712
            )
        ).all()
    )


def approve_plans(session: Session, user_id: int) -> int:
    """
    Accepts every pending proposal, replacing the approved plan for each
    discipline it covers.

    Replacing rather than accumulating: approving a new running plan means
    the old running plan is gone, which is what "this is my plan now" means.
    """
    pending = pending_plans(session, user_id)
    if not pending:
        return 0

    for proposal in pending:
        superseded = session.exec(
            select(TrainingPlan).where(
                TrainingPlan.user_id == user_id,
                TrainingPlan.discipline == proposal.discipline,
                TrainingPlan.is_active == True,  # noqa: E712
                TrainingPlan.id != proposal.id,
            )
        ).all()
        for old in superseded:
            session.exec(delete(PlanSession).where(PlanSession.plan_id == old.id))
            session.delete(old)

        proposal.is_active = True
        session.add(proposal)

    session.commit()
    return len(pending)


def discard_pending(session: Session, user_id: int) -> int:
    """Throws away unapproved proposals, leaving the approved plan untouched."""
    pending = pending_plans(session, user_id)
    for proposal in pending:
        session.exec(delete(PlanSession).where(PlanSession.plan_id == proposal.id))
        session.delete(proposal)
    session.commit()
    return len(pending)


def format_plan(session: Session, plan: TrainingPlan) -> str:
    """The week, day by day - the same shape the coach presents when it builds one."""
    sessions = session.exec(
        select(PlanSession)
        .where(PlanSession.plan_id == plan.id)
        .order_by(col(PlanSession.day_of_week))
    ).all()

    lines = [_rtl(f"📋 *{discipline_label(plan.discipline)}*"), ""]

    if not sessions:
        # A plan saved before structured weeks existed, or one the coach
        # wrote without a schedule. Show the prose rather than nothing.
        lines.append(_rtl(plan.plan))
        return "\n".join(lines)

    for item in sessions:
        day = DAY_NAMES_HE[item.day_of_week]
        if item.is_rest:
            lines.append(_rtl(f"*{day}* — {item.title} 😴"))
        else:
            targets = []
            if item.target_distance_km:
                targets.append(f"{item.target_distance_km:g} ק\"מ")
            if item.target_duration_minutes:
                targets.append(f"{item.target_duration_minutes} דק'")
            if item.target_pace:
                targets.append(item.target_pace)
            suffix = f" · {' · '.join(targets)}" if targets else ""
            lines.append(_rtl(f"*{day}* — {item.title}{suffix}"))
            if item.details:
                lines.append(_rtl(f"   {item.details}"))
        lines.append("")

    return "\n".join(lines).rstrip()


def format_upcoming_week(session: Session, user_id: int) -> str:
    """
    The next seven days, dated, with every discipline merged into each day.

    Seven days forward from today rather than a fixed Sunday-to-Saturday
    week: opening this on a Wednesday should not show four days that have
    already been and gone.

    Merged by day rather than listed per plan because "what do I have on
    Tuesday" is a question that crosses disciplines - someone running and
    swimming wants one answer, not two lists to reconcile.
    """
    plans = active_plans(session, user_id)
    if not plans:
        return ""

    by_plan = {p.id: p.discipline for p in plans}
    sessions = session.exec(
        select(PlanSession).where(PlanSession.plan_id.in_(list(by_plan)))
    ).all()

    by_day: dict[int, list] = {}
    for item in sessions:
        by_day.setdefault(item.day_of_week, []).append(item)

    today = date_type.today()
    lines = [_rtl("📋 *התוכנית שלך - השבוע הקרוב*"), ""]

    for offset in range(7):
        day = today + timedelta(days=offset)
        # isoweekday() is Monday=1..Sunday=7; % 7 maps Sunday to 0, matching
        # the stored week.
        index = day.isoweekday() % 7
        items = by_day.get(index, [])

        marker = " _(היום)_" if offset == 0 else (" _(מחר)_" if offset == 1 else "")
        # The date always gets its own line, even for a single session. With
        # two disciplines on one day, hanging the first workout off the date
        # and indenting the second made them look like different kinds of
        # thing rather than two equal items on the same day.
        lines.append(_rtl(f"*{DAY_NAMES_HE[index]} {day.strftime('%d/%m')}*{marker}"))

        if not items:
            # No row at all is not the same as a prescribed rest day, and
            # saying so is more honest than implying the coach chose it.
            lines.append(_rtl("   אין אימון מתוכנן"))
            lines.append("")
            continue

        # Rest days last: on a day with both a swim and a "rest from
        # running", the session you have to act on should be read first.
        for item in sorted(items, key=lambda i: i.is_rest):
            label = discipline_label(by_plan[item.plan_id]).split(" ", 1)[0]

            if item.is_rest:
                lines.append(_rtl(f"   {label} {item.title} 😴"))
                continue

            targets = []
            if item.target_distance_km:
                targets.append(f"{item.target_distance_km:g} ק\"מ")
            if item.target_duration_minutes:
                targets.append(f"{item.target_duration_minutes} דק\'")
            if item.target_pace:
                targets.append(item.target_pace)
            suffix = f" · {' · '.join(targets)}" if targets else ""
            lines.append(_rtl(f"   {label} *{item.title}*{suffix}"))
            if item.details:
                lines.append(_rtl(f"      {item.details}"))

        lines.append("")

    return "\n".join(lines).rstrip()


def cancel_plan(session: Session, user_id: int, discipline: str) -> bool:
    """
    Removes one discipline's plan and its week. Scoped to user_id as well as
    discipline: the callback data naming the plan arrives from the client,
    so it must never be the only thing deciding whose row is deleted.

    Sessions go first - they hold a foreign key to the plan and there is no
    ON DELETE CASCADE behind them.

    Restricted to the *approved* plan. Since proposals exist, a discipline
    can have two rows, and a bare .first() would have picked whichever the
    database happened to return - so "delete my running plan" could have
    quietly thrown away the draft under review and left the real plan in
    place. Discarding a proposal is discard_pending, a different action.
    """
    plan = session.exec(
        select(TrainingPlan).where(
            TrainingPlan.user_id == user_id,
            TrainingPlan.discipline == discipline,
            TrainingPlan.is_active == True,  # noqa: E712
        )
    ).first()
    if plan is None:
        return False

    session.exec(delete(PlanSession).where(PlanSession.plan_id == plan.id))
    session.delete(plan)
    session.commit()
    return True
