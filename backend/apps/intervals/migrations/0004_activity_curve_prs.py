# Generated manually for Activity.curve_prs

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intervals", "0003_wellnessday"),
    ]

    operations = [
        migrations.AddField(
            model_name="activity",
            name="curve_prs",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
