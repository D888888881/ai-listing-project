"""生图并发限额：环境变量 + SystemSetting（超级管理员可动态调整）。"""
from __future__ import annotations

import logging

from django.conf import settings

from .models import SystemSetting

logger = logging.getLogger(__name__)

KEY_API_GLOBAL = "nano_api_semaphore_global"
KEY_API_PER_USER = "nano_api_semaphore_per_user"
KEY_API_PER_USER_SUPERUSER = "nano_api_semaphore_per_user_superuser"


def _int_setting(key: str, env_name: str, default: int) -> int:
    db_val = SystemSetting.get_value(key, "")
    if db_val.isdigit():
        return max(1, int(db_val))
    return max(1, int(getattr(settings, env_name, default)))


def api_global_limit() -> int:
    return _int_setting(KEY_API_GLOBAL, "NANO_BANANA_API_SEMAPHORE_GLOBAL", 60)


def api_per_user_limit(*, is_superuser: bool = False) -> int:
    if is_superuser:
        return _int_setting(
            KEY_API_PER_USER_SUPERUSER,
            "NANO_BANANA_API_SEMAPHORE_PER_USER_SUPERUSER",
            int(getattr(settings, "NANO_BANANA_API_SEMAPHORE_PER_USER", 6)),
        )
    return _int_setting(KEY_API_PER_USER, "NANO_BANANA_API_SEMAPHORE_PER_USER", 6)


def set_api_limits(
    *,
    global_limit: int,
    per_user: int,
    per_user_superuser: int,
    user=None,
) -> None:
    SystemSetting.set_value(KEY_API_GLOBAL, str(max(1, global_limit)), user=user)
    SystemSetting.set_value(KEY_API_PER_USER, str(max(1, per_user)), user=user)
    SystemSetting.set_value(
        KEY_API_PER_USER_SUPERUSER,
        str(max(1, per_user_superuser)),
        user=user,
    )


def api_limits_status() -> dict:
    return {
        "global": api_global_limit(),
        "per_user": api_per_user_limit(is_superuser=False),
        "per_user_superuser": api_per_user_limit(is_superuser=True),
        "from_db": {
            "global": bool(SystemSetting.get_value(KEY_API_GLOBAL)),
            "per_user": bool(SystemSetting.get_value(KEY_API_PER_USER)),
            "per_user_superuser": bool(SystemSetting.get_value(KEY_API_PER_USER_SUPERUSER)),
        },
    }


def celery_queue_for_user(user) -> str:
    """超级管理员 / staff 走高优先级队列。"""
    if user_is_priority(user):
        return getattr(settings, "CELERY_IMAGE_GEN_QUEUE_HIGH", "image_gen_high")
    return getattr(settings, "CELERY_IMAGE_GEN_QUEUE", "image_gen")


def user_is_priority(user) -> bool:
    return bool(user and (getattr(user, "is_superuser", False) or getattr(user, "is_staff", False)))


def celery_workers_available() -> bool:
    """检测是否有 Celery worker 在线（用于决定异步入队或同步回退）。"""
    try:
        from celery import current_app

        inspect = current_app.control.inspect(timeout=3.0)
        ping = inspect.ping() if inspect is not None else None
        if ping:
            return True
    except Exception as exc:
        msg = str(exc).lower()
        logger.warning("Celery worker inspect failed: %s", exc)
        # RESP3 HELLO / 旧 Redis 不兼容：改走 Web 后台线程，避免 apply_async 再踩 result backend
        if "hello" in msg or "unknown command" in msg:
            logger.info(
                "Celery inspect unavailable (Redis protocol); fallback to local background thread"
            )
        return False
    return False


def api_per_user_limit_for_user_id(user_id: int) -> int:
    if user_id <= 0:
        return api_per_user_limit(is_superuser=False)
    from django.contrib.auth import get_user_model

    u = get_user_model().objects.filter(pk=user_id).only("is_superuser", "is_staff").first()
    return api_per_user_limit(is_superuser=user_is_priority(u))
