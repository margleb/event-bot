# src/event_bot/app.py
import asyncio
import os

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv
from openai import AsyncOpenAI

from event_bot.db import init_db, seed_events
from event_bot.handlers import router
from event_bot.profile_service import ProfileExtractor
from event_bot.storage import ProfileStore


async def run() -> None:
    """Собирает бота и запускает приём сообщений."""
    # читает .env и кладёт BOT_TOKEN / OPENAI_API_KEY в переменные
    # окружения; AsyncOpenAI подхватит свой ключ сам
    load_dotenv()

    # схема и демо-данные при каждом старте: обе операции безопасны
    # для повторного вызова
    init_db()
    seed_events()

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не задана")

    bot = Bot(token=bot_token)
    openai_client = AsyncOpenAI()
    # Dispatcher принимает апдейты от Telegram и раздаёт их роутерам
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    try:
        # polling — бот сам опрашивает Telegram о новых сообщениях.
        # Именованные аргументы попадают в обработчики: у кого в
        # сигнатуре есть profile_store, тот получит этот объект
        await dispatcher.start_polling(
            bot,
            profile_extractor=ProfileExtractor(openai_client),
            profile_store=ProfileStore(),
        )
    # закрываем соединения, даже если polling упал с ошибкой
    finally:
        await openai_client.close()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())
