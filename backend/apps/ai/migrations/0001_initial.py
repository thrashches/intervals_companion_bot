# Generated manually for AiPeriodReport

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("users", "0002_subscription"),
    ]

    operations = [
        migrations.CreateModel(
            name="AiPeriodReport",
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
                (
                    "kind",
                    models.CharField(
                        choices=[("day", "Day"), ("week", "Week")], max_length=16
                    ),
                ),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("summary", models.TextField(blank=True, default="")),
                ("context_json", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("ready", "Ready"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("error", models.TextField(blank=True, default="")),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_period_reports",
                        to="users.telegramuser",
                    ),
                ),
            ],
            options={
                "verbose_name": "AI-анализ периода",
                "verbose_name_plural": "AI-анализы периодов",
                "ordering": ["-period_start", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="aiperiodreport",
            index=models.Index(
                fields=["user", "kind", "-period_start"],
                name="ai_aiperiod_user_id_kind_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="aiperiodreport",
            index=models.Index(fields=["status"], name="ai_aiperiod_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="aiperiodreport",
            constraint=models.UniqueConstraint(
                fields=("user", "kind", "period_start", "period_end"),
                name="uniq_ai_period_report",
            ),
        ),
    ]
