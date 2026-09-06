import os

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("FERNET_KEY", "0RIWxZ0iw0C1g1NRXQh-xoKit7sito-Qn6_8Qtls4ao=")
os.environ.setdefault("INTERNAL_API_TOKEN", "test-internal-token")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
