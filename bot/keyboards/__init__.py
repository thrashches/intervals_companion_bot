from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def main_menu(connected: bool = False) -> ReplyKeyboardMarkup:
    if connected:
        rows = [
            [KeyboardButton(text="📅 План"), KeyboardButton(text="📊 Отчёты")],
            [KeyboardButton(text="💪 Форма"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ]
    else:
        rows = [
            [KeyboardButton(text="🔗 Подключить")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)



def connect_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подключить API-ключ", callback_data="connect:start")],
        ]
    )


def settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    ann = "✅ Вкл" if settings.get("announce_enabled") else "❌ Выкл"
    rep = "✅ Вкл" if settings.get("report_enabled") else "❌ Выкл"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Анонсы: {ann}", callback_data="settings:toggle_announce"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Отчёты: {rep}", callback_data="settings:toggle_report"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Изменить время анонса", callback_data="settings:set_time"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Изменить timezone", callback_data="settings:set_tz"
                )
            ],
        ]
    )
