# Generated manually for Activity.ai_summary

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intervals", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="activity",
            name="ai_summary",
            field=models.TextField(blank=True, default=""),
        ),
    ]
