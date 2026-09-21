# Generated manually for ServiceNews and NEWS kind

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0001_initial"),
        ("users", "0004_service_news_and_test_account"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notificationlog",
            name="kind",
            field=models.CharField(
                choices=[
                    ("announce", "Announce"),
                    ("report", "Report"),
                    ("day_analysis", "Day analysis"),
                    ("week_analysis", "Week analysis"),
                    ("system", "System"),
                    ("news", "News"),
                ],
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="ServiceNews",
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
                ("title", models.CharField(max_length=255)),
                (
                    "body_md",
                    models.TextField(
                        help_text="Markdown: **bold**, *italic*, [link](url), `code`, списки",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("sending", "Sending"),
                            ("sent", "Sent"),
                            ("failed", "Failed"),
                        ],
                        default="draft",
                        max_length=16,
                    ),
                ),
                (
                    "ready_to_send",
                    models.BooleanField(
                        default=False,
                        help_text="Пометить к массовой рассылке (подхватит Celery beat)",
                    ),
                ),
                ("send_generation", models.PositiveIntegerField(default=1)),
                ("sent_count", models.PositiveIntegerField(default=0)),
                ("failed_count", models.PositiveIntegerField(default=0)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Новость сервиса",
                "verbose_name_plural": "Новости сервиса",
                "ordering": ["-created_at"],
            },
        ),
    ]
