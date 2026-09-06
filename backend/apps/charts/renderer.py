from __future__ import annotations

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
