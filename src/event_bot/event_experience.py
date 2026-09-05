"""Attendance and optional follow-up answers for the post-event survey."""

ATTENDANCE_LABELS = {
    "met": "Да, с компанией",
    "solo": "Да, один/одна",
    "not_attended": "Нет, не ходил(а)",
}

# Historical answers remain readable and old Telegram buttons keep working.
OUTCOME_LABELS = {
    "met": "Сходили с компанией",
    "solo": "Сходили одни",
    "not_attended": "Не ходили",
    "no_show": "Никто не пришёл (старый опрос)",
    "unsafe": "Было некомфортно (старый опрос)",
}

DETAIL_LABELS = {
    "good": "Всё хорошо",
    "unsafe": "Было некомфортно",
    "preferred_solo": "Решил(а) пойти один/одна",
    "no_show": "Пришёл/пришла, но компания не пришла",
    "plans_changed": "Планы изменились",
    "no_company": "Компания не собралась",
    "no_agreement": "Не договорились о встрече",
    "event_cancelled": "Мероприятие отменили",
    "other": "Другая причина",
}

DETAILS_BY_OUTCOME = {
    "met": ("good", "unsafe"),
    "solo": ("preferred_solo", "no_show", "unsafe"),
    "not_attended": (
        "plans_changed", "no_company", "no_agreement", "event_cancelled", "other",
    ),
}

FOLLOWUP_QUESTIONS = {
    "met": "Как вам встреча?",
    "solo": "Почему пошли без компании?",
    "not_attended": "Почему не удалось сходить?",
}
