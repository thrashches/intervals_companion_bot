# Generated manually for Activity power/HR zone seconds

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intervals", "0004_activity_curve_prs"),
    ]

    operations = [
        migrations.AddField(
            model_name="activity",
            name="power_zone_secs",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="activity",
            name="hr_zone_secs",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
