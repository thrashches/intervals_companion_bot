# Generated manually for NotificationSettings period analysis fields

import datetime

from django.db import migrations, models


def enable_period_analysis(apps, schema_editor):
    NotificationSettings = apps.get_model("users", "NotificationSettings")
    NotificationSettings.objects.update(period_analysis_enabled=True)


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_subscription"),
    ]

    operations = [
        migrations.RenameField(
            model_name="notificationsettings",
            old_name="morning_summary_enabled",
            new_name="period_analysis_enabled",
        ),
        migrations.AlterField(
            model_name="notificationsettings",
            name="period_analysis_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Дневной и воскресный недельный AI-анализ",
            ),
        ),
        migrations.AddField(
            model_name="notificationsettings",
            name="analysis_time",
            field=models.TimeField(
                default=datetime.time(21, 30),
                help_text="Локальное время отправки дневного/недельного анализа",
            ),
        ),
        migrations.RunPython(enable_period_analysis, migrations.RunPython.noop),
    ]
