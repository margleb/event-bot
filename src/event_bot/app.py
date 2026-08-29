# src/event_bot/app.py
import asyncio
import os
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from dotenv import load_dotenv
from openai import AsyncOpenAI

from event_bot.db import init_db
from event_bot.digest import run_digest_scheduler
from event_bot.embedding_provider import EmbeddingProvider
from event_bot.handlers import router
from event_bot.profile_service import ProfileExtractor
from event_bot.storage import ProfileStore


async def run() -> None:
    """Собирает бота и запускает приём сообщений."""
    # Читает .env и кладёт BOT_TOKEN / OPENAI_API_KEY в окружение.
    # Без OpenAI-ключа бот всё равно стартует с tag fallback для /find.
    load_dotenv()

    # Схема создаётся и мигрирует при каждом старте. События загружаются
    # отдельно: python -m event_bot.import_events
    init_db()

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не задана")

    bot = Bot(token=bot_token)
    miniapp_url = os.getenv("MINIAPP_URL", "").strip()
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="find", description="Подобрать мероприятия"),
            BotCommand(command="profile", description="Мои предпочтения"),
            BotCommand(command="schedule", description="Настроить подборку"),
            BotCommand(command="my", description="Мои мероприятия"),
            BotCommand(command="group", description="Моя группа"),
        ]
    )
    if miniapp_url.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Афиша",
                web_app=WebAppInfo(url=miniapp_url),
            )
        )
    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_client = (
        AsyncOpenAI(api_key=openai_api_key) if openai_api_key else None
    )
    profile_extractor = (
        ProfileExtractor(openai_client) if openai_client is not None else None
    )
    embedding_provider = (
        EmbeddingProvider(openai_client) if openai_client is not None else None
    )
    # Dispatcher принимает апдейты от Telegram и раздаёт их роутерам
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    digest_task = asyncio.create_task(run_digest_scheduler(bot))

    try:
        # polling — бот сам опрашивает Telegram о новых сообщениях.
        # Именованные аргументы попадают в обработчики: у кого в
        # сигнатуре есть profile_store, тот получит этот объект
        await dispatcher.start_polling(
            bot,
            profile_extractor=profile_extractor,
            embedding_provider=embedding_provider,
            profile_store=ProfileStore(),
        )
    # закрываем соединения, даже если polling упал с ошибкой
    finally:
        digest_task.cancel()
        with suppress(asyncio.CancelledError):
            await digest_task
        if openai_client is not None:
            await openai_client.close()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())
