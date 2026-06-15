"""批量生图任务：Redis 热状态 + MySQL 持久化。"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any, Callable, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from .image_gen_config import celery_queue_for_user, celery_workers_available, user_is_priority
from .models import ImageGenJob, OriginalAsinData
from .redis_concurrency import get_redis_client, release_user_api_semaphore

logger = logging.getLogger(__name__)

_JOB_PREFIX = "image_gen:job:"
_CANCEL_PREFIX = "image_gen:cancel:"
_DEFAULT_TTL = 86400


def _job_key(job_id: str) -> str:
    return f"{_JOB_PREFIX}{job_id}"


def new_job_id() -> str:
    return uuid.uuid4().hex


def _cancel_key(job_id: str) -> str:
    return f"{_CANCEL_PREFIX}{job_id}"


def request_job_cancel(job_id: str) -> bool:
    """标记任务为已请求停止（worker 轮询该标记后尽快结束）。"""
    client = get_redis_client()
    if client is None or not job_id:
        return False
    client.setex(_cancel_key(job_id), _DEFAULT_TTL, "1")
    return True


def is_job_cancelled(job_id: str) -> bool:
    client = get_redis_client()
    if client is None or not job_id:
        return False
    return bool(client.get(_cancel_key(job_id)))


def is_job_stopped(job_id: str) -> bool:
    """Redis 停止标记或 DB 终态/已取消均视为应停止。"""
    if not job_id:
        return False
    if is_job_cancelled(job_id):
        return True
    job = _load_job_payload(job_id)
    if not job:
        return False
    status = job.get("status") or ImageGenJob.STATUS_PENDING
    return status in _terminal_statuses()


def clear_job_cancel(job_id: str) -> None:
    client = get_redis_client()
    if client is None or not job_id:
        return
    client.delete(_cancel_key(job_id))


def _terminal_statuses() -> set[str]:
    return {
        ImageGenJob.STATUS_COMPLETED,
        ImageGenJob.STATUS_FAILED,
        ImageGenJob.STATUS_DEAD,
        ImageGenJob.STATUS_CANCELLED,
    }


def refresh_job_snapshot_from_orig(job: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
    """从 OriginalAsinData 刷新成品图与本次 added（轮询/结束时的权威数据源）。"""
    job_id = job.get("job_id") or ""
    row = ImageGenJob.objects.filter(job_id=job_id).first()
    if not row or not row.orig_pk:
        return job
    result = row.result_json if isinstance(row.result_json, dict) else {}
    baseline = int(job.get("baseline_success_count") or result.get("baseline_success_count") or 0)
    job["baseline_success_count"] = baseline
    job = _refresh_job_snapshot_from_orig(job)
    if persist and job_id:
        prev_status = row.status
        job["status"] = prev_status
        save_job(job_id, job)
    return job


def _refresh_job_snapshot_from_orig(job: dict[str, Any]) -> dict[str, Any]:
    """用数据库最新成品图刷新任务快照（停止/僵死任务时用）。"""
    from .ai_image_payload import (
        ai_image_media_url,
        count_generating_finished_images,
        count_successful_finished_images,
        original_images_row_payload,
        payload_finished_images,
    )
    from .nano_banana_service import _display_finished_images_struct

    row = ImageGenJob.objects.filter(job_id=job.get("job_id") or "").first()
    if not row or not row.orig_pk:
        return job
    orig = OriginalAsinData.objects.filter(pk=row.orig_pk).first()
    if not orig:
        return job
    struct = payload_finished_images({"finished_images": getattr(orig, "finished_images", None)})
    display = _display_finished_images_struct(struct)
    baseline = int(job.get("baseline_success_count") or 0)
    added = max(int(job.get("added") or 0), max(0, count_successful_finished_images(struct) - baseline))
    generating = count_generating_finished_images(struct)
    batch_cap = int(job.get("batch_size") or row.batch_size or 0)
    processed = max(int(job.get("processed") or 0), min(batch_cap, added + generating) if batch_cap else added + generating)
    media_url = ai_image_media_url()
    job.update(
        {
            "added": added,
            "processed": processed,
            "batch_size": batch_cap,
            "finished_images": display,
            "finished_images_all": struct,
            "data": original_images_row_payload(display, media_url),
        }
    )
    return job


def _revoke_celery_task(celery_task_id: str) -> None:
    if not celery_task_id:
        return
    try:
        from celery import current_app

        terminate = bool(getattr(settings, "IMAGE_GEN_CANCEL_TERMINATE", False))
        current_app.control.revoke(
            celery_task_id,
            terminate=terminate,
            signal="SIGTERM" if terminate else None,
        )
    except Exception as exc:
        logger.warning("revoke celery task failed %s: %s", celery_task_id, exc)


def finalize_job_cancelled(job_id: str) -> dict[str, Any]:
    """将任务立即标记为已停止，并保留当前进度快照。"""
    job = _load_job_payload(job_id)
    if not job:
        return {"ok": False, "error": "任务不存在或已过期。"}
    status = job.get("status") or ImageGenJob.STATUS_PENDING
    if status in _terminal_statuses():
        clear_job_cancel(job_id)
        job["ok"] = status in (ImageGenJob.STATUS_COMPLETED, ImageGenJob.STATUS_CANCELLED)
        return job

    job = refresh_job_snapshot_from_orig(job, persist=False)
    errors = list(job.get("errors") or [])
    stop_msg = "用户已停止生图"
    if stop_msg not in errors:
        errors.insert(0, stop_msg)
    job.update(
        {
            "job_id": job_id,
            "status": ImageGenJob.STATUS_CANCELLED,
            "ok": True,
            "cancelled": True,
            "partial": True,
            "errors": errors[:20],
        }
    )
    save_job(job_id, job)
    clear_job_cancel(job_id)
    return job


def _celery_task_state(celery_task_id: str) -> str:
    if not celery_task_id:
        return ""
    try:
        from celery.result import AsyncResult

        return str(AsyncResult(celery_task_id).state or "")
    except Exception:
        return ""


def _fail_job(job_id: str, job: dict[str, Any], *, error: str, status: str = ImageGenJob.STATUS_FAILED) -> dict[str, Any]:
    job = _refresh_job_snapshot_from_orig(dict(job))
    job.update(
        {
            "job_id": job_id,
            "status": status,
            "ok": False,
            "error": error,
            "errors": list(job.get("errors") or []) + [error],
            "partial": True,
        }
    )
    save_job(job_id, job)
    return job
def _celery_task_ready(celery_task_id: str) -> bool:
    if not celery_task_id:
        return False
    try:
        from celery.result import AsyncResult

        return AsyncResult(celery_task_id).ready()
    except Exception:
        return False


def _sync_job_from_celery_result(row: ImageGenJob, job: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not row.celery_task_id:
        return None
    try:
        from celery.result import AsyncResult

        ar = AsyncResult(row.celery_task_id)
        if not ar.ready():
            return None
        result = ar.result
        if isinstance(result, dict) and result.get("job_id") == row.job_id:
            save_job(row.job_id, result)
            return _load_job_payload(row.job_id)
        if ar.failed():
            failed = {
                "job_id": row.job_id,
                "status": ImageGenJob.STATUS_DEAD,
                "user_id": row.user_id or 0,
                "asin": row.asin,
                "batch_size": row.batch_size,
                "added": int(job.get("added") or row.added or 0),
                "errors": list(job.get("errors") or row.errors_json or []),
                "error": str(ar.result)[:2000] if ar.result else "Celery 任务失败",
                "finished_images": job.get("finished_images"),
                "data": job.get("data"),
                "ok": False,
            }
            save_job(row.job_id, failed)
            return _load_job_payload(row.job_id)
    except Exception as exc:
        logger.debug("sync celery result skipped job=%s: %s", row.job_id, exc)
    return None


def reconcile_job_status(job_id: str) -> Optional[dict[str, Any]]:
    """修正「Worker 已结束但状态仍为 running」等不一致。"""
    job = _load_job_payload(job_id)
    if not job:
        return None
    status = job.get("status") or ImageGenJob.STATUS_PENDING
    if status in _terminal_statuses():
        return job

    if is_job_cancelled(job_id):
        return finalize_job_cancelled(job_id)

    row = ImageGenJob.objects.filter(job_id=job_id).first()
    if not row:
        return job

    synced = _sync_job_from_celery_result(row, job)
    if synced:
        return synced

    pending_timeout = int(getattr(settings, "IMAGE_GEN_PENDING_TIMEOUT_SEC", 45))
    age = (timezone.now() - row.updated_at).total_seconds()
    if status == ImageGenJob.STATUS_PENDING and age >= pending_timeout:
        task_state = _celery_task_state(row.celery_task_id)
        if task_state == "REVOKED":
            return finalize_job_cancelled(job_id)
        if not celery_workers_available():
            return _fail_job(
                job_id,
                job,
                error="生图 Worker 未运行，任务无法执行。请执行 docker compose up -d worker 后重试。",
            )
        if task_state == "PENDING" and age >= max(pending_timeout, 120):
            return _fail_job(
                job_id,
                job,
                error="任务在队列中等待超时，请检查 Celery worker 是否监听 image_gen / image_gen_high 队列。",
            )

    stale_sec = int(getattr(settings, "IMAGE_GEN_JOB_STALE_SEC", 90))
    if status in (ImageGenJob.STATUS_PENDING, ImageGenJob.STATUS_RUNNING) and age >= stale_sec:
        if row.celery_task_id and _celery_task_ready(row.celery_task_id):
            synced = _sync_job_from_celery_result(row, job)
            if synced:
                return synced
        task_state = _celery_task_state(row.celery_task_id)
        if task_state in ("STARTED", "RETRY", "PROGRESS") or (
            status == ImageGenJob.STATUS_RUNNING and task_state in ("PENDING", "STARTED", "")
        ):
            job = _refresh_job_snapshot_from_orig(job)
            job["status"] = ImageGenJob.STATUS_RUNNING
            save_job(job_id, job)
            return job
    return job


def _load_job_payload(job_id: str) -> Optional[dict[str, Any]]:
    if not job_id:
        return None
    cached = _redis_get(job_id)
    row = ImageGenJob.objects.filter(job_id=job_id).first()
    if not row:
        return cached
    db_payload = _db_row_to_payload(row)
    if not cached:
        _redis_save(job_id, db_payload)
        return db_payload

    merged = dict(cached)
    merged["job_id"] = job_id
    merged["added"] = max(int(merged.get("added") or 0), int(db_payload.get("added") or 0))
    merged["processed"] = max(
        int(merged.get("processed") or 0), int(db_payload.get("processed") or 0)
    )
    db_status = db_payload.get("status") or ""
    cache_status = merged.get("status") or ""
    terminal = _terminal_statuses()
    if db_status in terminal or (
        db_status == ImageGenJob.STATUS_RUNNING and cache_status == ImageGenJob.STATUS_PENDING
    ):
        merged["status"] = db_status
    for key in (
        "finished_images",
        "finished_images_all",
        "data",
        "errors",
        "error",
        "baseline_success_count",
        "batch_size",
        "asin",
        "user_id",
    ):
        val = db_payload.get(key)
        if val is None:
            continue
        if key in ("finished_images", "finished_images_all", "data") and not val:
            continue
        if key == "baseline_success_count":
            if int(val or 0) > 0 or not merged.get("baseline_success_count"):
                merged[key] = val
            continue
        merged[key] = val
    _redis_save(job_id, merged)
    return merged


def job_payload_for_poll(job_id: str) -> Optional[dict[str, Any]]:
    """轮询专用：合并 Redis/DB 后，进行中的任务直接从 ASIN 记录刷新进度。"""
    job = _load_job_payload(job_id)
    if not job:
        return None
    status = job.get("status") or ImageGenJob.STATUS_PENDING
    if status in (ImageGenJob.STATUS_PENDING, ImageGenJob.STATUS_RUNNING):
        job = refresh_job_snapshot_from_orig(job, persist=True)
    elif status in _terminal_statuses():
        job = refresh_job_snapshot_from_orig(job, persist=False)
    return job


def _redis_save(job_id: str, payload: dict[str, Any], ttl: int = _DEFAULT_TTL) -> None:
    client = get_redis_client()
    if client is None:
        return
    client.setex(_job_key(job_id), ttl, json.dumps(payload, ensure_ascii=False))


def _redis_get(job_id: str) -> Optional[dict[str, Any]]:
    client = get_redis_client()
    if client is None:
        return None
    raw = client.get(_job_key(job_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _db_row_to_payload(row: ImageGenJob) -> dict[str, Any]:
    result = row.result_json if isinstance(row.result_json, dict) else {}
    return {
        "job_id": row.job_id,
        "status": row.status,
        "user_id": row.user_id or 0,
        "asin": row.asin,
        "batch_size": row.batch_size,
        "added": row.added,
        "processed": int(result.get("processed") or row.added or 0),
        "baseline_success_count": int(result.get("baseline_success_count") or 0),
        "errors": row.errors_json or [],
        "error": row.error_message or "",
        "finished_images": result.get("finished_images"),
        "finished_images_all": result.get("finished_images_all") or result.get("finished_images"),
        "data": result.get("data"),
        "ok": row.status in (ImageGenJob.STATUS_COMPLETED, ImageGenJob.STATUS_CANCELLED),
        "partial": bool(row.errors_json) and row.added >= 0,
        "queue_name": row.queue_name,
        "retry_count": row.retry_count,
        "parent_job_id": row.parent_job_id,
    }


def create_job_record(
    *,
    user,
    asin: str,
    batch_size: int,
    orig_pk: int,
    job_specs: list[dict[str, Any]],
    user_notes: str = "",
    parent_job_id: str = "",
    retry_count: int = 0,
) -> ImageGenJob:
    job_id = new_job_id()
    queue = celery_queue_for_user(user)
    priority = 10 if user_is_priority(user) else 0
    row = ImageGenJob.objects.create(
        job_id=job_id,
        user=user if getattr(user, "pk", None) else None,
        asin=(asin or "").strip().upper(),
        status=ImageGenJob.STATUS_PENDING,
        queue_name=queue,
        priority=priority,
        batch_size=int(batch_size),
        orig_pk=int(orig_pk),
        user_notes=user_notes or "",
        job_specs_json=job_specs,
        parent_job_id=parent_job_id or "",
        retry_count=int(retry_count),
    )
    payload = _db_row_to_payload(row)
    _redis_save(job_id, payload)
    return row


def update_job_progress(
    job_id: str,
    *,
    added: int,
    batch_size: int,
    errors: list[str],
    orig: OriginalAsinData,
    processed: int | None = None,
) -> None:
    """异步生图过程中增量更新（完成一张即可被前端轮询到）。"""
    from .ai_image_payload import (
        ai_image_media_url,
        count_generating_finished_images,
        count_successful_finished_images,
        original_images_row_payload,
        payload_finished_images,
    )
    from .nano_banana_service import _display_finished_images_struct

    job = _load_job_payload(job_id) or {"job_id": job_id}
    if job.get("status") in _terminal_statuses() or is_job_stopped(job_id):
        return
    struct = payload_finished_images({"finished_images": getattr(orig, "finished_images", None)})
    display = _display_finished_images_struct(struct)
    baseline = int(job.get("baseline_success_count") or 0)
    delta = max(0, count_successful_finished_images(struct) - baseline)
    generating = count_generating_finished_images(struct)
    added_display = max(int(added), delta)
    processed_display = int(processed if processed is not None else added_display)
    processed_display = max(
        processed_display,
        min(int(batch_size), added_display + generating) if batch_size else added_display + generating,
    )
    media_url = ai_image_media_url()
    job.update(
        {
            "job_id": job_id,
            "status": ImageGenJob.STATUS_RUNNING,
            "added": added_display,
            "processed": processed_display,
            "batch_size": int(batch_size),
            "errors": list(errors)[:20],
            "finished_images": display,
            "finished_images_all": struct,
            "data": original_images_row_payload(display, media_url),
            "baseline_success_count": baseline,
            "ok": True,
        }
    )
    save_job(job_id, job)


def save_job(job_id: str, payload: dict[str, Any], ttl: int = _DEFAULT_TTL) -> None:
    _redis_save(job_id, payload, ttl=ttl)
    status = payload.get("status") or ImageGenJob.STATUS_PENDING
    updates: dict[str, Any] = {
        "status": status,
        "added": int(payload.get("added") or 0),
        "errors_json": payload.get("errors") or [],
        "error_message": (payload.get("error") or "")[:2000],
        "updated_at": timezone.now(),
    }
    if status == ImageGenJob.STATUS_RUNNING and not ImageGenJob.objects.filter(
        job_id=job_id, started_at__isnull=False
    ).exists():
        updates["started_at"] = timezone.now()
    if status in (
        ImageGenJob.STATUS_COMPLETED,
        ImageGenJob.STATUS_FAILED,
        ImageGenJob.STATUS_DEAD,
        ImageGenJob.STATUS_CANCELLED,
    ):
        updates["completed_at"] = timezone.now()
    result_json: dict[str, Any] = {}
    row = ImageGenJob.objects.filter(job_id=job_id).only("result_json").first()
    if row and isinstance(row.result_json, dict):
        result_json.update(row.result_json)
    if payload.get("baseline_success_count") is not None:
        result_json["baseline_success_count"] = int(payload.get("baseline_success_count") or 0)
    if payload.get("processed") is not None:
        result_json["processed"] = int(payload.get("processed") or 0)
    if payload.get("finished_images") is not None:
        result_json["finished_images"] = payload.get("finished_images")
    if payload.get("finished_images_all") is not None:
        result_json["finished_images_all"] = payload.get("finished_images_all")
    if payload.get("data") is not None:
        result_json["data"] = payload.get("data")
    if result_json:
        updates["result_json"] = result_json
    ImageGenJob.objects.filter(job_id=job_id).update(**updates)


def set_job_baseline(job_id: str, orig: OriginalAsinData) -> None:
    """记录任务开始前的成功成品图数量，用于增量进度统计。"""
    from .ai_image_payload import count_successful_finished_images

    job = _load_job_payload(job_id) or {"job_id": job_id}
    job["baseline_success_count"] = count_successful_finished_images(
        getattr(orig, "finished_images", None)
    )
    save_job(job_id, job)


def get_active_job_for_user(user_id: int) -> Optional[dict[str, Any]]:
    """返回当前用户最近一条进行中的批量生图任务（用于刷新页面后恢复）。"""
    uid = int(user_id or 0)
    if not uid:
        return None
    row = (
        ImageGenJob.objects.filter(
            user_id=uid,
            status__in=(ImageGenJob.STATUS_PENDING, ImageGenJob.STATUS_RUNNING),
        )
        .order_by("-created_at")
        .first()
    )
    if not row:
        return None
    job_id = row.job_id
    age = (timezone.now() - row.updated_at).total_seconds()
    if (
        row.status == ImageGenJob.STATUS_PENDING
        and not (row.celery_task_id or "").strip()
        and age >= 10
    ):
        job = get_job(job_id) or {"job_id": job_id}
        _fail_job(
            job_id,
            job,
            error="任务启动失败（入队异常），请重新点击批量生图。",
            status=ImageGenJob.STATUS_DEAD,
        )
        return None
    payload = reconcile_job_status(job_id) or get_job(job_id)
    if not payload:
        return None
    if payload.get("status") not in (ImageGenJob.STATUS_PENDING, ImageGenJob.STATUS_RUNNING):
        return None
    return payload


def cancel_job(job_id: str, *, user_id: int) -> dict[str, Any]:
    """用户请求停止任务：立即落库为 cancelled，并尝试终止 Celery worker。"""
    job = _load_job_payload(job_id)
    if not job:
        return {"ok": False, "error": "任务不存在或已过期。"}
    if int(job.get("user_id") or 0) != int(user_id):
        return {"ok": False, "error": "无权操作该任务。"}
    status = job.get("status") or ImageGenJob.STATUS_PENDING
    if status in _terminal_statuses():
        clear_job_cancel(job_id)
        job["ok"] = status in (ImageGenJob.STATUS_COMPLETED, ImageGenJob.STATUS_CANCELLED)
        return {"ok": True, "already_done": True, **job}

    request_job_cancel(job_id)
    release_user_api_semaphore(int(user_id))
    row = ImageGenJob.objects.filter(job_id=job_id).only("celery_task_id").first()
    task_id = (row.celery_task_id or "").strip() if row else ""
    if task_id and task_id not in ("local-thread", "eager-thread"):
        _revoke_celery_task(task_id)

    finalized = finalize_job_cancelled(job_id)
    if not finalized.get("job_id"):
        finalized["job_id"] = job_id
    finalized["ok"] = True
    finalized["status"] = ImageGenJob.STATUS_CANCELLED
    finalized["cancelled"] = True
    finalized["async"] = True
    return finalized


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    return _load_job_payload(job_id)


def async_batch_enabled() -> bool:
    if not getattr(settings, "NANO_BANANA_ASYNC_BATCH", True):
        return False
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return get_redis_client() is not None
    if get_redis_client() is None:
        return False
    if not celery_workers_available():
        logger.warning("Celery worker unavailable — batch jobs will run synchronously on web")
        return False
    return True


def _spawn_background_runner(name: str, target: Callable[..., None], *args: Any, **kwargs: Any) -> None:
    """在守护线程中执行任务（关闭继承的数据库连接，避免阻塞 HTTP 响应）。"""
    from django.db import connection

    def _wrapper() -> None:
        connection.close()
        try:
            target(*args, **kwargs)
        except Exception:
            logger.exception("background batch runner failed (%s)", name)

    thread = threading.Thread(target=_wrapper, name=name, daemon=True)
    thread.start()


def _start_local_batch_thread(*, job_id: str, task_args: list[Any]) -> None:
    from .tasks import run_jobs_batch_async

    ImageGenJob.objects.filter(job_id=job_id).update(celery_task_id="local-thread")
    _spawn_background_runner(
        f"batch-{job_id[:8]}",
        run_jobs_batch_async.apply,
        args=task_args,
    )


def start_batch_job_background(
    *,
    user,
    orig: OriginalAsinData,
    job_specs: list[dict[str, Any]],
    user_notes: str = "",
    parent_job_id: str = "",
    retry_count: int = 0,
) -> tuple[str, dict[str, Any], str]:
    """创建任务并在后台执行，HTTP 可立即返回 job_id 供前端轮询。"""
    from .tasks import run_jobs_batch_async

    row = create_job_record(
        user=user,
        asin=orig.asin,
        batch_size=len(job_specs),
        orig_pk=orig.pk,
        job_specs=job_specs,
        user_notes=user_notes,
        parent_job_id=parent_job_id,
        retry_count=retry_count,
    )
    job_id = row.job_id
    try:
        set_job_baseline(job_id, orig)
        user_id = int(getattr(user, "pk", None) or 0)
        task_args = [job_id, orig.pk, job_specs, user_id, user_notes]
        celery_task_id = "local-thread"

        use_local_thread = bool(
            getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False) or not celery_workers_available()
        )
        if use_local_thread:
            _start_local_batch_thread(job_id=job_id, task_args=task_args)
        else:
            try:
                async_result = run_jobs_batch_async.apply_async(
                    args=task_args,
                    queue=row.queue_name,
                )
                celery_task_id = async_result.id or ""
                ImageGenJob.objects.filter(pk=row.pk).update(celery_task_id=celery_task_id)
            except Exception as exc:
                logger.warning(
                    "apply_async failed for job_id=%s (%s), fallback to local thread",
                    job_id,
                    exc,
                )
                _start_local_batch_thread(job_id=job_id, task_args=task_args)

        from .image_gen_metrics import increment_counter

        increment_counter("jobs_enqueued")
        job_payload = get_job(job_id) or {}
        return job_id, job_payload, celery_task_id
    except Exception as exc:
        logger.exception("start_batch_job_background failed job_id=%s", job_id)
        mark_job_dead(job_id, f"任务启动失败：{exc}")
        raise


def enqueue_batch_job(
    *,
    user,
    orig: OriginalAsinData,
    job_specs: list[dict[str, Any]],
    user_notes: str = "",
    parent_job_id: str = "",
    retry_count: int = 0,
) -> tuple[str, str, dict[str, Any]]:
    """创建任务并入队 Celery（或本地后台线程），返回 (job_id, celery_task_id, snapshot)。"""
    job_id, job_payload, celery_task_id = start_batch_job_background(
        user=user,
        orig=orig,
        job_specs=job_specs,
        user_notes=user_notes,
        parent_job_id=parent_job_id,
        retry_count=retry_count,
    )
    return job_id, celery_task_id, job_payload


def mark_job_dead(job_id: str, error: str) -> None:
    payload = get_job(job_id) or {"job_id": job_id}
    payload["status"] = ImageGenJob.STATUS_DEAD
    payload["error"] = error
    payload["ok"] = False
    save_job(job_id, payload)
    from .image_gen_metrics import increment_counter

    increment_counter("jobs_dead")


def retry_job_from_db(job_id: str, *, operator) -> str:
    """从 DLQ/失败任务重新入队，返回新 job_id。"""
    row = ImageGenJob.objects.filter(job_id=job_id).first()
    if not row:
        raise ValueError("任务不存在")
    if row.status not in (ImageGenJob.STATUS_FAILED, ImageGenJob.STATUS_DEAD):
        raise ValueError("仅失败或死信任务可重试")
    specs = row.job_specs_json
    if not isinstance(specs, list) or not specs:
        raise ValueError("任务规格为空，无法重试")
    orig = OriginalAsinData.objects.filter(pk=row.orig_pk).first()
    if not orig:
        orig = OriginalAsinData.objects.filter(asin__iexact=row.asin).first()
    if not orig:
        raise ValueError("ASIN 记录不存在")
    owner = row.user
    if owner is None and row.user_id:
        owner = get_user_model().objects.filter(pk=row.user_id).first()
    if owner is None:
        owner = operator
    new_id, _, _ = enqueue_batch_job(
        user=owner,
        orig=orig,
        job_specs=specs,
        user_notes=row.user_notes,
        parent_job_id=row.job_id,
        retry_count=row.retry_count + 1,
    )
    from .image_gen_metrics import increment_counter

    increment_counter("jobs_retried")
    increment_counter("jobs_enqueued")
    return new_id


# 兼容旧调用
def create_job(*, user_id: int, asin: str, batch_size: int, ttl: int = _DEFAULT_TTL) -> str:
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    row = create_job_record(
        user=user,
        asin=asin,
        batch_size=batch_size,
        orig_pk=0,
        job_specs=[],
    )
    return row.job_id
