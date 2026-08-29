from openai import AsyncOpenAI

from event_bot.models import Profile, format_group_size


PROFILE_EXTRACTION_INSTRUCTIONS = (
    "Ты извлекаешь предпочтения пользователя для подбора мероприятий. "
    "Если вход состоит из нескольких пронумерованных сообщений, объедини их "
    "как последовательные ответы одного пользователя. Более позднее явное "
    "уточнение заменяет более раннее значение. "
    "Не додумывай отсутствующую информацию. "
    "Если день, бюджет или размер компании не указаны, оставляй null. "
    "В interests добавляй только явно выраженные интересы. "
    "В avoid добавляй только явно выраженные отрицательные предпочтения."
)

CLARIFICATION_QUESTIONS = {
    "interests": (
        "Чтобы подобрать что-то действительно подходящее, уточни: "
        "какие темы или форматы мероприятий тебе интересны?"
    ),
    "days": (
        "В какие дни недели тебе обычно удобно ходить на мероприятия? "
        "Можно ответить «любой день»."
    ),
    "budget": (
        "Какой максимальный бюджет на одно мероприятие? "
        "Можно ответить «любой бюджет»."
    ),
    "group_size": (
        "Какая компания комфортна: один, вдвоём, небольшая группа или без разницы?"
    ),
}


class ProfileExtractor:
    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def extract(self, text: str) -> Profile:
        """Свободный текст пользователя -> заполненная модель Profile."""
        # responses.parse со schema из Pydantic-модели: модель обязана
        # вернуть JSON нужной структуры, руками его парсить не нужно
        response = await self._client.responses.parse(
            model="gpt-4o-2024-08-06",
            instructions=PROFILE_EXTRACTION_INSTRUCTIONS,
            input=text,
            text_format=Profile,
        )

        # None бывает, если модель отказалась отвечать или ответ обрезан
        if response.output_parsed is None:
            raise ValueError("OpenAI не вернул распознанный профиль")

        return response.output_parsed


def build_profile_input(messages: list[str]) -> str:
    """Собирает последовательные ответы в один понятный модели ввод."""
    return "\n".join(
        f"Сообщение пользователя {index}: {text}"
        for index, text in enumerate(messages, start=1)
    )


def next_clarification_field(
    profile: Profile,
    clarified_fields: set[str] | None = None,
) -> str | None:
    """Первое поле, без которого персональная выдача пока недостаточна."""
    clarified = clarified_fields or set()
    # Без хотя бы одного интереса персонализировать ранжирование невозможно.
    if not profile.interests:
        return "interests"
    if not profile.days and "days" not in clarified:
        return "days"
    if profile.budget_rub is None and "budget" not in clarified:
        return "budget"
    if (
        profile.preferred_group_size_min is None
        and profile.preferred_group_size_max is None
        and "group_size" not in clarified
    ):
        return "group_size"
    return None


def clarification_question(field: str) -> str:
    """Возвращает пользовательский вопрос для известного поля профиля."""
    return CLARIFICATION_QUESTIONS[field]


def format_profile(profile: Profile) -> str:
    """Профиль -> текст для подтверждения пользователем."""
    # у незаполненных полей показываем «не указано», а не пустоту
    interests = ", ".join(profile.interests) or "не указаны"
    avoid = ", ".join(profile.avoid) or "не указано"
    days = ", ".join(profile.days) if profile.days else "не указаны"
    budget = (
        f"{profile.budget_rub} ₽"
        if profile.budget_rub is not None
        else "не указан"
    )

    # тот же формат, что и в карточке участника события
    group_size = format_group_size(
        profile.preferred_group_size_min,
        profile.preferred_group_size_max,
    )

    return (
        "Я понял так:\n\n"
        f"Интересы: {interests}\n"
        f"Не нравится: {avoid}\n"
        f"Дни: {days}\n"
        f"Бюджет: {budget}\n"
        f"Размер компании: {group_size}"
    )
