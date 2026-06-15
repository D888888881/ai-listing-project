"""生图运维指标（Redis 计数，供运维页 / JSON API）。"""
from __future__ import annotations

import time
from typing import Any, Optional

from django.conf import settings

from .redis_concurrency import get_redis_client

_METRICS_PREFIX = "image_gen:metrics:"
_TIMING_PREFIX = "image_gen:timing:"


def _client():
    return get_redis_client()


def increment_counter(name: str, amount: int = 1) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.hincrby(f"{_METRICS_PREFIX}counters", name, amount)
    except Exception:
        pass


def record_timing(name: str, seconds: float) -> None:
    client = _client()
    if client is None:
        return
    try:
        key = f"{_TIMING_PREFIX}{name}"
        pipe = client.pipeline()
        pipe.lpush(key, f"{seconds:.4f}")
        pipe.ltrim(key, 0, 499)
        pipe.execute()
    except Exception:
        pass


def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * pct))
    return round(sorted_vals[idx], 3)


def _timing_stats(name: str) -> dict[str, Optional[float]]:
    client = _client()
    if client is None:
        return {"count": 0, "p50": None, "p95": None, "avg": None}
    try:
        raw = client.lrange(f"{_TIMING_PREFIX}{name}", 0, 499)
        vals = [float(x) for x in raw if x]
        if not vals:
            return {"count": 0, "p50": None, "p95": None, "avg": None}
        return {
            "count": len(vals),
            "p50": _percentile(vals, 0.5),
            "p95": _percentile(vals, 0.95),
            "avg": round(sum(vals) / len(vals), 3),
        }
    except Exception:
        return {"count": 0, "p50": None, "p95": None, "avg": None}


def celery_queue_lengths() -> dict[str, int]:
    """Redis broker 队列深度（Celery Redis 传输）。"""
    client = _client()
    if client is None:
        return {}
    queues = [
        getattr(settings, "CELERY_IMAGE_GEN_QUEUE_HIGH", "image_gen_high"),
        getattr(settings, "CELERY_IMAGE_GEN_QUEUE", "image_gen"),
        getattr(settings, "CELERY_IMAGE_GEN_QUEUE_DLQ", "image_gen_dlq"),
    ]
    out: dict[str, int] = {}
    for q in queues:
        try:
            out[q] = int(client.llen(q))
        except Exception:
            out[q] = -1
    return out


def redis_semaphore_usage() -> dict[str, Any]:
    client = _client()
    if client is None:
        return {}
    now = time.time()
    try:
        global_key = "nano_banana:api:global"
        client.zremrangebyscore(global_key, "-inf", now)
        return {"global_active_slots": int(client.zcard(global_key))}
    except Exception:
        return {}


def metrics_snapshot() -> dict[str, Any]:
    from .image_gen_config import api_limits_status

    client = _client()
    counters: dict[str, int] = {}
    if client is not None:
        try:
            raw = client.hgetall(f"{_METRICS_PREFIX}counters") or {}
            counters = {k: int(v) for k, v in raw.items()}
        except Exception:
            pass
    return {
        "counters": counters,
        "batch_duration_sec": _timing_stats("batch"),
        "single_image_sec": _timing_stats("single_image"),
        "celery_queues": celery_queue_lengths(),
        "semaphore": redis_semaphore_usage(),
        "limits": api_limits_status(),
    }
