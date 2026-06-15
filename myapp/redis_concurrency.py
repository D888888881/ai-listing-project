"""Redis 分布式并发槽位（跨 Gunicorn worker / Celery 进程共享）。"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_ACQUIRE_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local token = ARGV[2]
local expire_at = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZCARD', key) < limit then
  redis.call('ZADD', key, expire_at, token)
  return 1
end
return 0
"""

_RELEASE_LUA = """
redis.call('ZREM', KEYS[1], ARGV[1])
return 1
"""

_redis_client = None
_redis_client_lock = threading.Lock()
_redis_semaphores: dict[str, "RedisCountingSemaphore"] = {}
_redis_semaphores_lock = threading.Lock()


def redis_enabled() -> bool:
    if not getattr(settings, "NANO_BANANA_USE_REDIS_SEMAPHORE", True):
        return False
    return bool((getattr(settings, "REDIS_URL", None) or "").strip())


def _redis_connect_kwargs() -> dict:
    """redis-py 5 默认 RESP3 会发 HELLO；旧版 Redis / 部分兼容服务不支持，用 RESP2。"""
    protocol = int(getattr(settings, "REDIS_PROTOCOL", 2))
    return {
        "decode_responses": True,
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
        "health_check_interval": 30,
        "protocol": protocol,
    }


def get_redis_client():
    """懒加载 Redis 客户端；不可用时返回 None。"""
    global _redis_client
    if not redis_enabled():
        return None
    with _redis_client_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            import redis

            url = settings.REDIS_URL.strip()
            _redis_client = redis.Redis.from_url(url, **_redis_connect_kwargs())
            _redis_client.ping()
            return _redis_client
        except Exception as exc:
            logger.warning("Redis unavailable, falling back to in-process semaphores: %s", exc)
            _redis_client = None
            return None


def invalidate_redis_client() -> None:
    """连接异常时清缓存，便于下次重建。"""
    global _redis_client
    with _redis_client_lock:
        _redis_client = None


class RedisCountingSemaphore:
    """基于 ZSET 的分布式计数信号量，槽位带租约防止泄漏。"""

    def __init__(self, client, key: str, limit: int, lease_sec: float):
        self._client = client
        self.key = key
        self.limit = max(1, int(limit))
        self.lease_sec = max(30.0, float(lease_sec))

    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> str:
        token = uuid.uuid4().hex
        wait_timeout = float(
            timeout
            if timeout is not None
            else getattr(settings, "NANO_BANANA_SEMAPHORE_ACQUIRE_TIMEOUT", 600)
        )
        deadline = time.time() + max(1.0, wait_timeout)
        while True:
            now = time.time()
            try:
                ok = self._client.eval(
                    _ACQUIRE_LUA,
                    1,
                    self.key,
                    self.limit,
                    token,
                    now + self.lease_sec,
                    now,
                )
            except Exception as exc:
                invalidate_redis_client()
                raise RuntimeError(f"Redis semaphore acquire failed ({self.key}): {exc}") from exc
            if ok == 1:
                return token
            if not blocking or time.time() >= deadline:
                raise TimeoutError(
                    "生图 API 并发槽位已满（可能因上次停止后槽位未释放）。"
                    "请稍后重试，或联系管理员检查 Redis 与 worker 状态。"
                )
            time.sleep(0.05)

    def release(self, token: str) -> None:
        if not token:
            return
        try:
            self._client.eval(_RELEASE_LUA, 1, self.key, token)
        except Exception as exc:
            logger.warning("Redis semaphore release failed (%s): %s", self.key, exc)


def _lease_sec() -> float:
    poll_max = float(getattr(settings, "NANO_BANANA_POLL_MAX_WAIT", 360))
    http_timeout = float(getattr(settings, "NANO_BANANA_HTTP_TIMEOUT", 460))
    return max(600.0, poll_max + http_timeout + 120.0)


def global_redis_semaphore() -> Optional[RedisCountingSemaphore]:
    from .image_gen_config import api_global_limit

    client = get_redis_client()
    if client is None:
        return None
    limit = max(1, api_global_limit())
    key = "nano_banana:api:global"
    with _redis_semaphores_lock:
        sem = _redis_semaphores.get(key)
        if sem is None or sem.limit != limit:
            sem = RedisCountingSemaphore(client, key, limit, _lease_sec())
            _redis_semaphores[key] = sem
        return sem


def cleanup_expired_semaphore_slots(key: str) -> None:
    """清理已过期的分布式并发槽位。"""
    client = get_redis_client()
    if not client or not key:
        return
    try:
        client.zremrangebyscore(key, "-inf", time.time())
    except Exception as exc:
        logger.debug("semaphore cleanup skipped (%s): %s", key, exc)


def purge_semaphore_key(key: str) -> None:
    """强制释放某条信号量键上的全部槽位（用于停止/崩溃恢复）。"""
    client = get_redis_client()
    if not client or not key:
        return
    try:
        client.delete(key)
    except Exception as exc:
        logger.warning("semaphore purge failed (%s): %s", key, exc)


def release_user_api_semaphore(user_id: int) -> None:
    uid = int(user_id or 0)
    if uid <= 0:
        return
    purge_semaphore_key(f"nano_banana:api:user:{uid}")


def touch_semaphore_limits(user_id: int) -> None:
    """任务开始前清理过期槽位，避免僵死占用。"""
    if get_redis_client() is None:
        return
    cleanup_expired_semaphore_slots("nano_banana:api:global")
    uid = int(user_id or 0)
    if uid > 0:
        cleanup_expired_semaphore_slots(f"nano_banana:api:user:{uid}")


def user_redis_semaphore(user_id: int) -> Optional[RedisCountingSemaphore]:
    from .image_gen_config import api_per_user_limit_for_user_id

    client = get_redis_client()
    if client is None:
        return None
    uid = int(user_id or 0)
    if uid <= 0:
        return None
    limit = max(1, api_per_user_limit_for_user_id(uid))
    key = f"nano_banana:api:user:{uid}"
    with _redis_semaphores_lock:
        cache_key = f"{key}:{limit}"
        sem = _redis_semaphores.get(cache_key)
        if sem is None:
            sem = RedisCountingSemaphore(client, key, limit, _lease_sec())
            _redis_semaphores[cache_key] = sem
        return sem
