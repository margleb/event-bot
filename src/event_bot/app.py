import asyncio
import os

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv
from openai import AsyncOpenAI

from event_bot.handlers import router
from event_bot.profile_service import ProfileExtractor
from event_bot.storage import ProfileStore


async def run() -> None:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не задана")

    bot = Bot(token=bot_token)
    openai_client = AsyncOpenAI()
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    try:
        await dispatcher.start_polling(
            bot,
            profile_extractor=ProfileExtractor(openai_client),
            profile_store=ProfileStore(),
        )
    finally:
        await openai_client.close()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())
