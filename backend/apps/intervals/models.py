from django.db import models

from apps.users.models import TelegramUser


class AthleteSnapshot(models.Model):
    user = models.OneToOneField(
        TelegramUser, on_delete=models.CASCADE, related_name="athlete_snapshot"
    )
    fitness = models.FloatField(null=True, blank=True)  # CTL
    fatigue = models.FloatField(null=True, blank=True)  # ATL
    form = models.FloatField(null=True, blank=True)  # TSB
    weight = models.FloatField(null=True, blank=True)
    vo2max = models.FloatField(null=True, blank=True)
    as_of_date = models.DateField(null=True, blank=True)
    raw_json = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Снимок формы"
        verbose_name_plural = "Снимки формы"

    def __str__(self) -> str:
        return f"Form for {self.user_id} @ {self.as_of_date}"


class WellnessDay(models.Model):
    """Daily wellness row from intervals.icu (sleep, RHR, weight, subjective scores)."""

    user = models.ForeignKey(
        TelegramUser, on_delete=models.CASCADE, related_name="wellness_days"
    )
    date = models.DateField()
    sleep_secs = models.IntegerField(null=True, blank=True)
    sleep_quality = models.FloatField(null=True, blank=True)
    sleep_score = models.FloatField(null=True, blank=True)
    resting_hr = models.FloatField(null=True, blank=True)
    avg_sleeping_hr = models.FloatField(null=True, blank=True)
    hrv = models.FloatField(null=True, blank=True)
    weight = models.FloatField(null=True, blank=True)
    fatigue = models.FloatField(null=True, blank=True)  # subjective 1–4/10
    soreness = models.FloatField(null=True, blank=True)
    stress = models.FloatField(null=True, blank=True)
    mood = models.FloatField(null=True, blank=True)
    motivation = models.FloatField(null=True, blank=True)
    injury = models.FloatField(null=True, blank=True)
    readiness = models.FloatField(null=True, blank=True)
    fitness = models.FloatField(null=True, blank=True)  # CTL
    fatigue_atl = models.FloatField(null=True, blank=True)  # ATL
    form = models.FloatField(null=True, blank=True)  # TSB
    comments = models.TextField(blank=True, default="")
    raw_json = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Wellness день"
        verbose_name_plural = "Wellness дни"
        unique_together = ("user", "date")
        indexes = [
            models.Index(fields=["user", "date"]),
        ]
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"Wellness {self.user_id} @ {self.date}"


class CalendarEventCache(models.Model):
    user = models.ForeignKey(
        TelegramUser, on_delete=models.CASCADE, related_name="calendar_events"
    )
    external_id = models.CharField(max_length=64)
    category = models.CharField(max_length=64, blank=True, default="")
    name = models.CharField(max_length=512, blank=True, default="")
    type = models.CharField(max_length=64, blank=True, default="")
    start_date_local = models.DateTimeField(null=True, blank=True)
    end_date_local = models.DateTimeField(null=True, blank=True)
    icu_training_load = models.FloatField(null=True, blank=True)
    workout_doc = models.JSONField(default=dict, blank=True)
    raw_json = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Событие календаря"
        verbose_name_plural = "События календаря"
        unique_together = ("user", "external_id")
        indexes = [
            models.Index(fields=["user", "start_date_local"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.start_date_local})"


class Activity(models.Model):
    user = models.ForeignKey(
        TelegramUser, on_delete=models.CASCADE, related_name="activities"
    )
    external_id = models.CharField(max_length=64)
    name = models.CharField(max_length=512, blank=True, default="")
    type = models.CharField(max_length=64, blank=True, default="")
    start_date_local = models.DateTimeField(null=True, blank=True)
    moving_time = models.IntegerField(null=True, blank=True)
    distance = models.FloatField(null=True, blank=True)
    icu_training_load = models.FloatField(null=True, blank=True)
    icu_intensity = models.FloatField(null=True, blank=True)
    average_watts = models.FloatField(null=True, blank=True)
    weighted_average_watts = models.FloatField(null=True, blank=True)
    average_heartrate = models.FloatField(null=True, blank=True)
    max_heartrate = models.FloatField(null=True, blank=True)
    total_elevation_gain = models.FloatField(null=True, blank=True)
    compliance = models.FloatField(null=True, blank=True)
    matched_event = models.ForeignKey(
        CalendarEventCache,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="matched_activities",
    )
    intervals_json = models.JSONField(default=list, blank=True)
    raw_json = models.JSONField(default=dict, blank=True)
    # Seconds in each power zone from icu_zone_times (index 0 = Z1)
    power_zone_secs = models.JSONField(default=list, blank=True)
    # Seconds in each HR zone from icu_hr_zone_times (index 0 = Z1)
    hr_zone_secs = models.JSONField(default=list, blank=True)
    # Compact PRs from athlete power/HR curves, e.g.
    # [{"metric":"power","duration_sec":300,"value":320,"label":"5 мин"}]
    curve_prs = models.JSONField(default=list, blank=True)
    chart_path = models.CharField(max_length=512, blank=True, default="")
    ai_summary = models.TextField(blank=True, default="")
    report_sent_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Активность"
        verbose_name_plural = "Активности"
        unique_together = ("user", "external_id")
        indexes = [
            models.Index(fields=["user", "start_date_local"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.external_id})"
