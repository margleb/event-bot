from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Верно",
                    callback_data="profile_confirm",
                ),
                InlineKeyboardButton(
                    text="✏️ Исправить",
                    callback_data="profile_edit",
                ),
            ]
        ]
    )
