from openai import AsyncOpenAI

from event_bot.models import Profile


PROFILE_EXTRACTION_INSTRUCTIONS = (
    "Ты извлекаешь предпочтения пользователя для подбора мероприятий. "
    "Не додумывай отсутствующую информацию. "
    "Если день, бюджет или размер компании не указаны, оставляй null. "
    "В interests добавляй только явно выраженные интересы. "
    "В avoid добавляй только явно выраженные отрицательные предпочтения."
)


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

    minimum = profile.preferred_group_size_min
    maximum = profile.preferred_group_size_max
    if minimum is not None and maximum is not None:
        group_size = f"{minimum}–{maximum}"
    elif minimum is not None:
        group_size = f"от {minimum}"
    elif maximum is not None:
        group_size = f"до {maximum}"
    else:
        group_size = "не указан"

    return (
        "Я понял так:\n\n"
        f"Интересы: {interests}\n"
        f"Не нравится: {avoid}\n"
        f"Дни: {days}\n"
        f"Бюджет: {budget}\n"
        f"Размер компании: {group_size}"
    )
