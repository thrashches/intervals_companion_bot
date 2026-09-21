CONNECT_INSTRUCTION = (
    "Чтобы подключить intervals.icu:\n\n"
    "1. Откройте https://intervals.icu\n"
    "2. Перейдите в Settings → Developer Settings\n"
    "3. Скопируйте API Key\n"
    "4. Нажмите «Подключить» и отправьте ключ сюда одним сообщением\n\n"
    "⚠️ Ключ даёт доступ к вашим данным. Не делитесь им. "
    "Сообщение с ключом будет удалено."
)

HELP_TEXT = (
    "Intervals Companion — бот-помощник для intervals.icu.\n\n"
    "Команды:\n"
    "/start — начать / статус\n"
    "/plan — план на сегодня и завтра (с картинками)\n"
    "/reports — отчёты по последним тренировкам\n"
    "/form — форма (CTL/ATL/TSB) + график за 30 дней\n"
    "/zones — распределение зон Z1–Z5 за неделю\n"
    "/analyze — AI-анализ дня или недели\n"
    "/settings — расписание анонсов и анализа\n"
    "/disconnect — отключить API-ключ\n"
    "/help — эта справка\n\n"
    "Бот присылает анонс тренировки по расписанию, отчёт после загрузки "
    "активности и AI-анализ дня (по умолчанию в 21:30; в воскресенье — ещё и недели "
    "с графиками формы и зон)."
)


def format_plan(data: dict) -> str:
    def block(title: str, events: list) -> str:
        if not events:
            return f"<b>{title}</b>\n— нет событий\n"
        lines = [f"<b>{title}</b>"]
        for ev in events:
            load = f", load {ev['icu_training_load']:.0f}" if ev.get("icu_training_load") else ""
            lines.append(f"• {ev.get('name') or 'Событие'} ({ev.get('type') or '—'}{load})")
        return "\n".join(lines) + "\n"

    return (
        "📅 <b>Тренировочный план</b>\n\n"
        + block("Сегодня", data.get("today") or [])
        + "\n"
        + block("Завтра", data.get("tomorrow") or [])
        + "\n"
        + block("Ближайшие", data.get("upcoming") or [])
    )


def format_form(data: dict) -> str:
    payload = data.get("data")
    if not payload:
        return "Данные формы пока недоступны. Попробуйте позже."
    def fmt(v):
        return "—" if v is None else f"{v:.1f}"

    return (
        "💪 <b>Форма спортсмена</b>\n"
        f"Fitness (CTL): {fmt(payload.get('fitness'))}\n"
        f"Fatigue (ATL): {fmt(payload.get('fatigue'))}\n"
        f"Form (TSB): {fmt(payload.get('form'))}\n"
        f"Вес: {fmt(payload.get('weight'))} кг\n"
        f"VO2max: {fmt(payload.get('vo2max'))}\n"
        f"Дата снимка: {payload.get('as_of_date') or '—'}"
    )


def format_settings(settings: dict) -> str:
    days = settings.get("announce_days") or list(range(7))
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    days_str = ", ".join(day_names[d] for d in days if 0 <= d <= 6)
    analysis_time = settings.get("analysis_time") or "21:30:00"
    return (
        "⚙️ <b>Настройки уведомлений</b>\n"
        f"Анонсы: {'вкл' if settings.get('announce_enabled') else 'выкл'}\n"
        f"Отчёты: {'вкл' if settings.get('report_enabled') else 'выкл'}\n"
        f"AI-анализ: {'вкл' if settings.get('period_analysis_enabled', True) else 'выкл'}\n"
        f"Время анонса: {settings.get('announce_time')}\n"
        f"Время анализа: {analysis_time}\n"
        f"Timezone: {settings.get('timezone')}\n"
        f"Дни анонсов: {days_str}"
    )
