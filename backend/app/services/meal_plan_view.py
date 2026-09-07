"""
Showing and deleting a meal plan without involving Claude.

Same principle as plan_view: the plan is stored exactly, so reciting it
should never spend a token. Claude builds and revises it; this reads it back.
"""

from typing import Optional

from sqlmodel import Session, delete, select

from app.models.meal_plan import MEAL_SLOT_LABELS, MEAL_SLOTS, MealOption, MealPlan

RLM = "‏"


def _rtl(text: str) -> str:
    return f"{RLM}{text}"


def get_plan(session: Session, user_id: int) -> Optional[MealPlan]:
    return session.exec(select(MealPlan).where(MealPlan.user_id == user_id)).first()


def format_meal_plan(session: Session, user_id: int) -> str:
    """Slot by slot, options under each. Empty string when there is no plan."""
    plan = get_plan(session, user_id)
    if plan is None:
        return ""

    options = session.exec(
        select(MealOption).where(MealOption.plan_id == plan.id)
    ).all()

    by_slot: dict[str, list] = {}
    for option in options:
        by_slot.setdefault(option.meal_slot, []).append(option)

    lines = [_rtl(f"🍽 *התפריט שלך* - יעד {plan.target_calories:,} קלוריות"), ""]

    # Iterated in MEAL_SLOTS order rather than dictionary order, so breakfast
    # always comes before dinner regardless of what order they were saved in.
    for slot in MEAL_SLOTS:
        items = by_slot.get(slot)
        if not items:
            continue
        lines.append(_rtl(f"*{MEAL_SLOT_LABELS.get(slot, slot)}*"))
        for option in items:
            kcal = f" · {option.calories:,} קל'" if option.calories else ""
            lines.append(_rtl(f"   • *{option.title}*{kcal}"))
            if option.description:
                lines.append(_rtl(f"     {option.description}"))
        lines.append("")

    if plan.approach:
        lines.append(_rtl(f"_{plan.approach}_"))

    return "\n".join(lines).rstrip()


def delete_meal_plan(session: Session, user_id: int) -> bool:
    """
    Scoped by user_id: this is reached from a callback the client sends.
    Children first - they hold a foreign key with no cascade behind it.
    """
    plan = get_plan(session, user_id)
    if plan is None:
        return False

    session.exec(delete(MealOption).where(MealOption.plan_id == plan.id))
    session.delete(plan)
    session.commit()
    return True
