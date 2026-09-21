# Generated manually for WellnessDay

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("intervals", "0002_activity_ai_summary"),
        ("users", "0002_subscription"),
    ]

    operations = [
        migrations.CreateModel(
            name="WellnessDay",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("date", models.DateField()),
                ("sleep_secs", models.IntegerField(blank=True, null=True)),
                ("sleep_quality", models.FloatField(blank=True, null=True)),
                ("sleep_score", models.FloatField(blank=True, null=True)),
                ("resting_hr", models.FloatField(blank=True, null=True)),
                ("avg_sleeping_hr", models.FloatField(blank=True, null=True)),
                ("hrv", models.FloatField(blank=True, null=True)),
                ("weight", models.FloatField(blank=True, null=True)),
                ("fatigue", models.FloatField(blank=True, null=True)),
                ("soreness", models.FloatField(blank=True, null=True)),
                ("stress", models.FloatField(blank=True, null=True)),
                ("mood", models.FloatField(blank=True, null=True)),
                ("motivation", models.FloatField(blank=True, null=True)),
                ("injury", models.FloatField(blank=True, null=True)),
                ("readiness", models.FloatField(blank=True, null=True)),
                ("fitness", models.FloatField(blank=True, null=True)),
                ("fatigue_atl", models.FloatField(blank=True, null=True)),
                ("form", models.FloatField(blank=True, null=True)),
                ("comments", models.TextField(blank=True, default="")),
                ("raw_json", models.JSONField(blank=True, default=dict)),
                ("synced_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="wellness_days",
                        to="users.telegramuser",
                    ),
                ),
            ],
            options={
                "verbose_name": "Wellness день",
                "verbose_name_plural": "Wellness дни",
                "ordering": ["-date"],
                "indexes": [
                    models.Index(
                        fields=["user", "date"], name="intervals_w_user_id_date_idx"
                    ),
                ],
                "unique_together": {("user", "date")},
            },
        ),
    ]
