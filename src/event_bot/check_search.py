import asyncio
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from event_bot.db import find_events, init_db
from event_bot.embedding_provider import (
    EmbeddingProvider,
    profile_embedding_text,
    vector_to_blob,
)
from event_bot.models import Profile


CHECK_PROFILES: tuple[tuple[str, Profile], ...] = (
    (
        "техно",
        Profile(
            interests=["техно, электронная музыка, клубные вечеринки, диджей-сеты"],
            avoid=["вязание, рукоделие, спокойные мастер-классы"],
        ),
    ),
    (
        "вязание",
        Profile(
            interests=["вязание, пряжа, рукоделие и творческие мастер-классы"],
            avoid=["громкие клубы и техно-вечеринки"],
        ),
    ),
    (
        "лекции",
        Profile(
            interests=["познавательные лекции об истории, науке и культуре"],
            avoid=["шумные вечеринки"],
        ),
    ),
    (
        "спокойные прогулки",
        Profile(
            interests=["неспешные прогулки, парки, архитектура и тихие экскурсии"],
            avoid=["толпы, громкая музыка и шумные фестивали"],
        ),
    ),
    (
        "шумные фестивали",
        Profile(
            interests=["большие фестивали, концерты, танцы и шумные компании"],
            avoid=["тихие камерные лекции и вязание"],
        ),
    ),
)


async def check_search(provider: EmbeddingProvider) -> None:
    """Печатает пять semantic-топов для быстрой ручной проверки качества."""
    init_db()
    texts: list[str] = []
    for _, profile in CHECK_PROFILES:
        texts.append(profile_embedding_text(profile.interests))
        texts.append(profile_embedding_text(profile.avoid))

    vectors = await provider.embed(texts)
    for index, (label, profile) in enumerate(CHECK_PROFILES):
        positive = vector_to_blob(vectors[index * 2])
        negative = vector_to_blob(vectors[index * 2 + 1])
        events = find_events(
            profile,
            profile_embedding=positive,
            profile_embedding_model=provider.model,
            avoid_embedding=negative,
            avoid_embedding_model=provider.model,
        )
        print(f"\n{label}:")
        for place, event in enumerate(events, start=1):
            print(f"  {place}. {event.title}")


async def _main() -> int:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY не задан")
        return 1

    client = AsyncOpenAI(api_key=api_key)
    try:
        await check_search(EmbeddingProvider(client))
    finally:
        await client.close()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
