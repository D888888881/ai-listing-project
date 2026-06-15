"""Celery 应用配置。"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai_listing_project.settings")

app = Celery("ai_listing_project")
app.config_from_object("django.conf:settings", namespace="CELERY")
# 与 Django REDIS_PROTOCOL 对齐，避免 broker 使用 RESP3 HELLO
try:
    from django.conf import settings as django_settings

    _proto = int(getattr(django_settings, "REDIS_PROTOCOL", 2))
    app.conf.broker_transport_options = {
        **(app.conf.broker_transport_options or {}),
        "protocol": _proto,
    }
    app.conf.result_backend_transport_options = {
        **(app.conf.result_backend_transport_options or {}),
        "protocol": _proto,
    }
except Exception:
    pass
app.autodiscover_tasks()
