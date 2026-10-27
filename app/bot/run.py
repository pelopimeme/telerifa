"""Run the bot (long polling):   python -m app.bot.run

Also pushes the Spanish command list and the Mini App menu button through the Bot API (same as BotFather).
Do not run polling and a webhook for the same bot at the same time.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from ..config import settings
from .handlers import router


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Cómo funciona TeleRifa y abrir la app"),
        BotCommand(command="billetera", description="Mis tickets"),
        BotCommand(command="ganadores", description="Últimos 5 ganadores"),
    ])
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="🎟️ TeleRifa", web_app=WebAppInfo(url=settings.web_app_url))
    )
    await dp.start_polling(bot, drop_pending_updates=True, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
