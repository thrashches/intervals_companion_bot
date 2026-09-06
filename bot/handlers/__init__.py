from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from api_client import BackendAPIError, BackendClient
from handlers.states import ConnectStates, SettingsStates
from handlers.texts import (
    CONNECT_INSTRUCTION,
    HELP_TEXT,
    format_form,
    format_plan,
    format_settings,
)
from keyboards import connect_keyboard, main_menu, settings_keyboard

router = Router()
api = BackendClient()


def _uid(message: Message) -> int:
    return message.from_user.id


async def _ensure_user(message: Message) -> dict:
    user = message.from_user
    return api.upsert_user(
        telegram_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        language_code=user.language_code or "ru",
    )


async def _send_chart_items(message: Message, items: list[dict]) -> int:
    """Send each item with chart as a separate photo message. Returns sent count."""
    sent = 0
    for item in items:
        chart_path = item.get("chart_path")
        caption = (item.get("caption") or item.get("name") or "Тренировка")[:1024]
        if chart_path:
            try:
                data = api.download_media(chart_path)
                photo = BufferedInputFile(data, filename=chart_path.split("/")[-1])
                await message.answer_photo(photo=photo, caption=caption)
                sent += 1
                continue
            except Exception:
                pass
        await message.answer(caption)
        sent += 1
    return sent


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    try:
        user = await _ensure_user(message)
    except BackendAPIError as exc:
        await message.answer(f"Ошибка бэкенда: {exc}")
        return

    if user.get("credentials_valid"):
        await message.answer(
            f"Снова привет, {message.from_user.first_name or 'атлет'}! "
            "Intervals.icu подключён.",
            reply_markup=main_menu(connected=True),
        )
    else:
        await message.answer(
            f"Привет! Я Intervals Companion.\n\n{CONNECT_INSTRUCTION}",
            reply_markup=main_menu(connected=False),
            reply_to_message_id=None,
        )
        await message.answer("Готовы подключить аккаунт?", reply_markup=connect_keyboard())


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@router.message(Command("disconnect"))
async def cmd_disconnect(message: Message, state: FSMContext):
    await state.clear()
    try:
        await _ensure_user(message)
        api.disconnect(_uid(message))
        await message.answer(
            "API-ключ удалён. Чтобы подключить снова — /start",
            reply_markup=main_menu(connected=False),
        )
    except BackendAPIError as exc:
        await message.answer(f"Не удалось отключить: {exc}")


@router.message(F.text == "🔗 Подключить")
@router.callback_query(F.data == "connect:start")
async def connect_start(event: Message | CallbackQuery, state: FSMContext):
    await state.set_state(ConnectStates.waiting_for_key)
    text = "Отправьте API-ключ одним сообщением."
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.answer(text)
    else:
        await event.answer(text)


@router.message(ConnectStates.waiting_for_key)
async def connect_receive_key(message: Message, state: FSMContext):
    api_key = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass

    if not api_key or len(api_key) < 8:
        await message.answer("Ключ слишком короткий. Попробуйте ещё раз.")
        return

    try:
        await _ensure_user(message)
        result = api.connect(_uid(message), api_key)
        await state.clear()
        name = result.get("athlete_name") or ""
        await message.answer(
            f"✅ Аккаунт подключен{f' ({name})' if name else ''}!\n"
            "Используйте меню или команды /plan, /reports и /form.",
            reply_markup=main_menu(connected=True),
        )
    except BackendAPIError as exc:
        await message.answer(
            f"Не удалось подключить ключ: {exc}\nПроверьте ключ и попробуйте снова."
        )


@router.message(Command("plan"))
@router.message(F.text == "📅 План")
async def cmd_plan(message: Message):
    try:
        await _ensure_user(message)
        data = api.get_plan(_uid(message), refresh=True)
        await message.answer(format_plan(data), reply_markup=main_menu(connected=True))
        workouts = data.get("workouts") or []
        chart_items = [w for w in workouts if w.get("chart_path")]
        if chart_items:
            await _send_chart_items(message, chart_items)
    except BackendAPIError as exc:
        await message.answer(f"Не удалось получить план: {exc}")


@router.message(Command("reports"))
@router.message(F.text == "📊 Отчёты")
async def cmd_reports(message: Message):
    try:
        await _ensure_user(message)
        data = api.get_recent_reports(_uid(message), limit=5)
        items = data.get("items") or []
        if not items:
            await message.answer(
                "Нет недавних тренировок.",
                reply_markup=main_menu(connected=True),
            )
            return
        await message.answer(
            f"📊 Последние тренировки ({len(items)}):",
            reply_markup=main_menu(connected=True),
        )
        await _send_chart_items(message, items)
    except BackendAPIError as exc:
        await message.answer(f"Не удалось получить отчёты: {exc}")


@router.message(Command("form"))
@router.message(F.text == "💪 Форма")
async def cmd_form(message: Message):
    try:
        await _ensure_user(message)
        data = api.get_form(_uid(message), refresh=True)
        await message.answer(format_form(data), reply_markup=main_menu(connected=True))
    except BackendAPIError as exc:
        await message.answer(f"Не удалось получить форму: {exc}")


@router.message(Command("settings"))
@router.message(F.text == "⚙️ Настройки")
async def cmd_settings(message: Message, state: FSMContext):
    await state.clear()
    try:
        await _ensure_user(message)
        settings = api.get_settings(_uid(message))
        await message.answer(
            format_settings(settings),
            reply_markup=settings_keyboard(settings),
        )
    except BackendAPIError as exc:
        await message.answer(f"Ошибка настроек: {exc}")


@router.callback_query(F.data == "settings:toggle_announce")
async def toggle_announce(callback: CallbackQuery):
    try:
        settings = api.get_settings(callback.from_user.id)
        updated = api.patch_settings(
            callback.from_user.id,
            {"announce_enabled": not settings.get("announce_enabled", True)},
        )
        await callback.message.edit_text(
            format_settings(updated), reply_markup=settings_keyboard(updated)
        )
        await callback.answer("Обновлено")
    except BackendAPIError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data == "settings:toggle_report")
async def toggle_report(callback: CallbackQuery):
    try:
        settings = api.get_settings(callback.from_user.id)
        updated = api.patch_settings(
            callback.from_user.id,
            {"report_enabled": not settings.get("report_enabled", True)},
        )
        await callback.message.edit_text(
            format_settings(updated), reply_markup=settings_keyboard(updated)
        )
        await callback.answer("Обновлено")
    except BackendAPIError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data == "settings:set_time")
async def settings_set_time(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsStates.waiting_for_time)
    await callback.answer()
    await callback.message.answer("Введите время анонса в формате ЧЧ:ММ (например 08:30)")


@router.callback_query(F.data == "settings:set_tz")
async def settings_set_tz(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsStates.waiting_for_timezone)
    await callback.answer()
    await callback.message.answer(
        "Введите IANA timezone, например Europe/Moscow или Asia/Yekaterinburg"
    )


@router.message(SettingsStates.waiting_for_time)
async def receive_time(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    try:
        hh, mm = text.split(":")
        hour, minute = int(hh), int(mm)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        value = f"{hour:02d}:{minute:02d}:00"
        updated = api.patch_settings(_uid(message), {"announce_time": value})
        await state.clear()
        await message.answer(
            format_settings(updated), reply_markup=settings_keyboard(updated)
        )
    except Exception:
        await message.answer("Неверный формат. Пример: 07:45")


@router.message(SettingsStates.waiting_for_timezone)
async def receive_tz(message: Message, state: FSMContext):
    tz = (message.text or "").strip()
    try:
        updated = api.patch_settings(_uid(message), {"timezone": tz})
        await state.clear()
        await message.answer(
            format_settings(updated), reply_markup=settings_keyboard(updated)
        )
    except BackendAPIError as exc:
        await message.answer(f"Не удалось сохранить timezone: {exc}")
