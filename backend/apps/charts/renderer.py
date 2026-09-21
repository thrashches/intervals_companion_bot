from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from django.conf import settings


ZONE_COLORS = {
    1: "#4a90d9",
    2: "#5cb85c",
    3: "#f0ad4e",
    4: "#d9534f",
    5: "#c9302c",
    6: "#9b59b6",
    7: "#8e44ad",
}

# Approximate mid %FTP for power zones when only zone id is known
_ZONE_INTENSITY = {1: 45, 2: 65, 3: 82, 4: 97, 5: 112, 6: 130, 7: 150}


def _duration_seconds(item: dict) -> float:
    return float(
        item.get("moving_time")
        or item.get("elapsed_time")
        or item.get("duration")
        or ((item.get("end_time") or 0) - (item.get("start_time") or 0))
        or 60
    )


def _mid_numeric(obj: Any, *keys: str) -> float | None:
    if obj is None:
        return None
    if isinstance(obj, (int, float)):
        return float(obj)
    if not isinstance(obj, dict):
        return None
    values: list[float] = []
    for key in keys or ("value", "start", "end"):
        raw = obj.get(key)
        if raw is None:
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return sum(values) / len(values)


def _bar_value(item: dict) -> float:
    """Prefer intensity; fall back to average_watts only if intensity missing."""
    intensity = item.get("intensity")
    if intensity is not None:
        try:
            return float(intensity)
        except (TypeError, ValueError):
            pass
    watts = item.get("average_watts")
    if watts is not None:
        try:
            return float(watts)
        except (TypeError, ValueError):
            pass
    return 50.0


def _step_intensity(step: dict, ftp: float | None = None) -> float:
    """Resolve planned step height as intensity (%FTP-like)."""
    explicit = step.get("intensity") or step.get("percent")
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass

    power = step.get("power")
    resolved = step.get("_power")
    units = ""
    if isinstance(power, dict):
        units = str(power.get("units") or "").lower()
        mid = _mid_numeric(power, "value", "start", "end")
        if units in ("%ftp", "ftp", "%") and mid is not None:
            return mid
        if units in ("w", "watts", "watt") and mid is not None and ftp:
            return mid / float(ftp) * 100.0
        if units in ("power_zone", "zone"):
            zone_val = power.get("value")
            if resolved and ftp:
                watts_mid = _mid_numeric(resolved, "value", "start", "end")
                if watts_mid is not None:
                    return watts_mid / float(ftp) * 100.0
            try:
                return float(_ZONE_INTENSITY.get(int(zone_val), 50))
            except (TypeError, ValueError):
                pass
        if mid is not None and ftp and mid > 150:
            return mid / float(ftp) * 100.0
        if mid is not None:
            return mid

    if isinstance(power, (int, float)):
        if ftp and float(power) > 150:
            return float(power) / float(ftp) * 100.0
        return float(power)

    if resolved and ftp:
        watts_mid = _mid_numeric(resolved, "value", "start", "end")
        if watts_mid is not None:
            return watts_mid / float(ftp) * 100.0

    zone = step.get("zone") or step.get("power_zone")
    try:
        return float(_ZONE_INTENSITY.get(int(zone), 50))
    except (TypeError, ValueError):
        return 50.0


def _step_type(step: dict) -> str:
    raw = (
        step.get("type")
        or step.get("intensity")
        or step.get("step_type")
        or "WORK"
    )
    return str(raw).upper()


def _flatten_steps(steps: list, reps: int = 1) -> list[dict]:
    flat: list[dict] = []
    for _ in range(max(int(reps or 1), 1)):
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            nested = step.get("steps")
            if nested:
                nested_reps = step.get("reps") or step.get("repeat") or 1
                flat.extend(_flatten_steps(nested, nested_reps))
                continue
            flat.append(step)
    return flat


def steps_to_intervals(workout_doc: dict | None, ftp: float | None = None) -> list[dict]:
    """Convert planned workout_doc.steps into interval bars (intensity on Y)."""
    doc = workout_doc or {}
    if ftp is None:
        try:
            ftp = float(doc["ftp"]) if doc.get("ftp") is not None else None
        except (TypeError, ValueError):
            ftp = None
    steps = doc.get("steps") or []
    flat = _flatten_steps(steps)
    intervals: list[dict] = []

    for step in flat:
        duration = _duration_seconds(step)
        zone = step.get("zone") or step.get("power_zone")
        if zone is None and isinstance(step.get("power"), dict):
            if str(step["power"].get("units") or "").lower() in ("power_zone", "zone"):
                zone = step["power"].get("value")
        item: dict[str, Any] = {
            "moving_time": duration,
            "type": _step_type(step),
            "zone": int(zone) if zone else 2,
            "intensity": _step_intensity(step, ftp=ftp),
        }
        intervals.append(item)

    return intervals


def render_intervals_chart(
    chart_id: str,
    intervals: list,
    title: str = "",
    y_label: str | None = None,
) -> str:
    """
    Render a simple intervals bar chart similar to intervals.icu style.
    X axis in minutes; Y axis intensity.
    Returns relative path under MEDIA_ROOT.
    """
    media_root = Path(settings.MEDIA_ROOT)
    charts_dir = media_root / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    safe_id = str(chart_id).replace("/", "_")
    filename = f"{safe_id}.png"
    full_path = charts_dir / filename

    if y_label is None:
        y_label = "Интенсивность"

    if not intervals:
        fig, ax = plt.subplots(figsize=(10, 3), facecolor="#1e1e1e")
        ax.set_facecolor("#1e1e1e")
        ax.text(0.5, 0.5, "Нет данных интервалов", ha="center", va="center", color="white")
        ax.set_xticks([])
        ax.set_yticks([])
        fig.savefig(full_path, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return f"charts/{filename}"

    fig, ax = plt.subplots(figsize=(11, 3.2), facecolor="#1a1a1a")
    ax.set_facecolor("#1a1a1a")

    cursor_min = 0.0
    max_value = 1.0

    for item in intervals:
        duration_sec = _duration_seconds(item)
        duration_min = duration_sec / 60.0
        value = _bar_value(item)
        max_value = max(max_value, value)
        zone = int(item.get("zone") or 2)
        color = ZONE_COLORS.get(zone, "#888888")
        interval_type = (item.get("type") or "WORK").upper()
        alpha = 0.45 if interval_type in ("RECOVERY", "REST", "COOLDOWN", "WARMUP") else 0.9
        ax.bar(
            cursor_min + duration_min / 2,
            value,
            width=max(duration_min, 1 / 60),
            color=color,
            alpha=alpha,
            align="center",
            edgecolor="#111111",
            linewidth=0.3,
        )
        cursor_min += duration_min

    ax.set_xlim(0, max(cursor_min, 0.1))
    ax.set_ylim(0, max_value * 1.15)
    ax.tick_params(colors="#cccccc")
    for spine in ax.spines.values():
        spine.set_color("#444444")
    ax.set_xlabel("Время (мин)", color="#cccccc")
    ax.set_ylabel(y_label, color="#cccccc")
    if title:
        ax.set_title(title, color="#ffffff", fontsize=11, pad=10)

    fig.savefig(full_path, dpi=140, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return f"charts/{filename}"


def _charts_dir() -> Path:
    media_root = Path(settings.MEDIA_ROOT)
    charts_dir = media_root / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    return charts_dir


def render_form_trend_chart(
    user_id: int,
    series: list[dict],
    title: str = "",
    end_date: date | None = None,
) -> str:
    """
    Line chart of CTL / ATL / TSB over days.
    series items: {date: ISO str, ctl, atl, tsb} (values may be None).
    Returns relative path under MEDIA_ROOT.
    """
    charts_dir = _charts_dir()
    stamp = (end_date or date.today()).isoformat()
    filename = f"form_{user_id}_{stamp}.png"
    full_path = charts_dir / filename

    fig, ax = plt.subplots(figsize=(11, 4.2), facecolor="#1a1a1a")
    ax.set_facecolor("#1a1a1a")

    if not series:
        ax.text(
            0.5,
            0.5,
            "Нет данных формы",
            ha="center",
            va="center",
            color="white",
        )
        ax.set_xticks([])
        ax.set_yticks([])
        fig.savefig(full_path, dpi=140, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return f"charts/{filename}"

    dates = [datetime.fromisoformat(item["date"]).date() for item in series]
    ctl = [item.get("ctl") for item in series]
    atl = [item.get("atl") for item in series]
    tsb = [item.get("tsb") for item in series]

    ax.plot(dates, ctl, color="#4a90d9", linewidth=2.0, label="CTL (Fitness)", marker="o", markersize=3)
    ax.plot(dates, atl, color="#f0ad4e", linewidth=2.0, label="ATL (Fatigue)", marker="o", markersize=3)
    ax.plot(dates, tsb, color="#5cb85c", linewidth=2.0, label="TSB (Form)", marker="o", markersize=3)
    ax.axhline(0, color="#666666", linewidth=0.8, linestyle="--")

    ax.legend(facecolor="#2a2a2a", edgecolor="#444444", labelcolor="#dddddd", fontsize=9)
    ax.tick_params(colors="#cccccc")
    for spine in ax.spines.values():
        spine.set_color("#444444")
    ax.set_xlabel("Дата", color="#cccccc")
    ax.set_ylabel("Значение", color="#cccccc")
    if title:
        ax.set_title(title, color="#ffffff", fontsize=11, pad=10)
    fig.autofmt_xdate()

    fig.savefig(full_path, dpi=140, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return f"charts/{filename}"


def render_zone_distribution_chart(
    user_id: int,
    actual_secs: list[int],
    planned_secs: list[int] | None,
    source: str = "power",
    period_start: date | None = None,
    period_end: date | None = None,
    title: str = "",
) -> str:
    """
    Grouped bar chart: actual vs planned time in Z1–Z5(+).
    Returns relative path under MEDIA_ROOT.
    """
    charts_dir = _charts_dir()
    start_s = period_start.isoformat() if period_start else "start"
    end_s = period_end.isoformat() if period_end else "end"
    filename = f"zones_{user_id}_{start_s}_{end_s}.png"
    full_path = charts_dir / filename

    labels = ["Z1", "Z2", "Z3", "Z4", "Z5+"]
    actual = list(actual_secs or [0, 0, 0, 0, 0])[:5]
    while len(actual) < 5:
        actual.append(0)
    planned = None
    if planned_secs is not None:
        planned = list(planned_secs or [0, 0, 0, 0, 0])[:5]
        while len(planned) < 5:
            planned.append(0)

    fig, ax = plt.subplots(figsize=(10, 4.5), facecolor="#1a1a1a")
    ax.set_facecolor("#1a1a1a")

    x = list(range(5))
    width = 0.36 if planned is not None else 0.55
    actual_min = [s / 60.0 for s in actual]
    colors = [ZONE_COLORS.get(i + 1, "#888888") for i in range(5)]

    if planned is not None:
        planned_min = [s / 60.0 for s in planned]
        ax.bar(
            [i - width / 2 for i in x],
            actual_min,
            width=width,
            color=colors,
            alpha=0.95,
            label="Факт",
            edgecolor="#111111",
            linewidth=0.4,
        )
        ax.bar(
            [i + width / 2 for i in x],
            planned_min,
            width=width,
            color=colors,
            alpha=0.35,
            label="План",
            edgecolor="#aaaaaa",
            linewidth=0.6,
            hatch="//",
        )
        ax.legend(facecolor="#2a2a2a", edgecolor="#444444", labelcolor="#dddddd", fontsize=9)
    else:
        ax.bar(
            x,
            actual_min,
            width=width,
            color=colors,
            alpha=0.95,
            edgecolor="#111111",
            linewidth=0.4,
        )

    total = sum(actual) or 1
    for i, secs in enumerate(actual):
        pct = 100.0 * secs / total
        height = actual_min[i]
        if height > 0:
            ax.text(
                i - (width / 2 if planned is not None else 0),
                height,
                f"{pct:.0f}%",
                ha="center",
                va="bottom",
                color="#dddddd",
                fontsize=8,
            )

    source_label = "мощность" if source == "power" else "пульс"
    ax.set_xticks(x)
    ax.set_xticklabels(labels, color="#cccccc")
    ax.tick_params(colors="#cccccc")
    for spine in ax.spines.values():
        spine.set_color("#444444")
    ax.set_ylabel("Минуты", color="#cccccc")
    chart_title = f"{title} ({source_label})" if title else f"Распределение зон ({source_label})"
    ax.set_title(chart_title, color="#ffffff", fontsize=11, pad=10)

    fig.savefig(full_path, dpi=140, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return f"charts/{filename}"
