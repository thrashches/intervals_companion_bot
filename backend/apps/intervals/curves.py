"""Power/HR curve personal-record detection against intervals.icu athlete curves."""

from __future__ import annotations

import logging
from typing import Any

from apps.intervals.client import IntervalsAPIError, IntervalsClient
from apps.intervals.models import Activity

logger = logging.getLogger(__name__)

# Key mean-max durations (seconds) we care about for PR shout-outs.
KEY_DURATIONS_SEC = frozenset({5, 15, 30, 60, 120, 300, 720, 1200, 1800, 3600})

_DURATION_LABELS = {
    5: "5 с",
    15: "15 с",
    30: "30 с",
    60: "1 мин",
    120: "2 мин",
    300: "5 мин",
    720: "12 мин",
    1200: "20 мин",
    1800: "30 мин",
    3600: "60 мин",
}


def duration_label(secs: int) -> str:
    if secs in _DURATION_LABELS:
        return _DURATION_LABELS[secs]
    if secs < 60:
        return f"{secs} с"
    minutes = secs // 60
    rem = secs % 60
    if rem:
        return f"{minutes} мин {rem} с"
    return f"{minutes} мин"


def _first_curve(payload: Any) -> dict | None:
    """Normalize ICU power/hr-curves response to a single curve dict."""
    if not payload:
        return None
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
        return None
    if not isinstance(payload, dict):
        return None
    curves = payload.get("list") or payload.get("curves")
    if isinstance(curves, list):
        for item in curves:
            if isinstance(item, dict):
                return item
    # Some responses may be the curve object itself
    if "secs" in payload and "values" in payload:
        return payload
    return None


def extract_prs_from_curve(
    curve: dict | None,
    *,
    activity_id: str,
    metric: str,
) -> list[dict[str, Any]]:
    """Return PR entries where this activity owns the best effort at key durations."""
    if not curve or not activity_id:
        return []
    secs = curve.get("secs") or []
    values = curve.get("values") or []
    activity_ids = curve.get("activity_id") or []
    if not isinstance(secs, list) or not isinstance(values, list):
        return []
    if not isinstance(activity_ids, list):
        return []

    prs: list[dict[str, Any]] = []
    n = min(len(secs), len(values), len(activity_ids))
    for i in range(n):
        try:
            duration = int(secs[i])
        except (TypeError, ValueError):
            continue
        if duration not in KEY_DURATIONS_SEC:
            continue
        owner = activity_ids[i]
        if owner is None:
            continue
        if str(owner) != str(activity_id):
            continue
        try:
            value = float(values[i])
        except (TypeError, ValueError):
            continue
        prs.append(
            {
                "metric": metric,
                "duration_sec": duration,
                "value": round(value, 1),
                "label": duration_label(duration),
            }
        )
    return prs


def detect_curve_prs(
    client: IntervalsClient,
    activity: Activity,
    *,
    curves: str = "all",
) -> list[dict[str, Any]]:
    """
    Fetch athlete power/HR curves and return PRs set by this activity.

    Failures against ICU are logged and return [] so activity reports still send.
    """
    activity_type = (activity.type or "").strip()
    if not activity_type or not activity.external_id:
        return []

    # VirtualRide / VirtualRun often share Ride/Run curve sets on ICU
    curve_type = activity_type
    lower = activity_type.lower()
    if lower == "virtualride":
        curve_type = "Ride"
    elif lower == "virtualrun":
        curve_type = "Run"

    prs: list[dict[str, Any]] = []
    for metric, fetcher in (
        ("power", client.get_power_curves),
        ("hr", client.get_hr_curves),
    ):
        try:
            payload = fetcher(curves=curves, activity_type=curve_type)
        except IntervalsAPIError:
            logger.warning(
                "Failed to fetch %s curves for activity %s",
                metric,
                activity.external_id,
                exc_info=True,
            )
            continue
        curve = _first_curve(payload)
        prs.extend(
            extract_prs_from_curve(
                curve,
                activity_id=activity.external_id,
                metric=metric,
            )
        )

    # Stable order: power first, then by duration
    prs.sort(key=lambda p: (0 if p["metric"] == "power" else 1, p["duration_sec"]))
    return prs


def refresh_activity_curve_prs(
    client: IntervalsClient,
    activity: Activity,
) -> list[dict[str, Any]]:
    """Detect PRs and persist on the activity. Returns the PR list."""
    prs = detect_curve_prs(client, activity)
    activity.curve_prs = prs
    activity.save(update_fields=["curve_prs", "synced_at"])
    return prs
