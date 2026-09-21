# Generated manually for is_test_account

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_period_analysis_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegramuser",
            name="is_test_account",
            field=models.BooleanField(
                default=False,
                help_text="Тестовый аккаунт для пробной рассылки новостей",
            ),
        ),
    ]
