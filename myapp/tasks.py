"""Celery 异步任务。"""
from __future__ import annotations

import logging
import time
from typing import Any

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="myapp.run_jobs_batch_async",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0,
    ignore_result=True,
)
def run_jobs_batch_async(
    self,
    job_id: str,
    orig_pk: int,
    job_specs: list[dict[str, Any]],
    user_id: int,
    user_notes: str = "",
) -> dict[str, Any]:
    from myapp.ai_image_payload import build_batch_result_payload
    from myapp.image_gen_jobs import (
        clear_job_cancel,
        finalize_job_cancelled,
        get_job,
        is_job_stopped,
        save_job,
        set_job_baseline,
    )
    from myapp.image_gen_metrics import increment_counter, record_timing
    from myapp.models import ImageGenJob, OriginalAsinData
    from myapp.nano_banana_service import nano_banana_user_scope, run_jobs_batch

    started = time.time()
    job = get_job(job_id) or {}
    if is_job_stopped(job_id):
        clear_job_cancel(job_id)
        return finalize_job_cancelled(job_id) if job.get("status") != ImageGenJob.STATUS_CANCELLED else job

    from myapp.redis_concurrency import touch_semaphore_limits

    touch_semaphore_limits(user_id)

    job["status"] = ImageGenJob.STATUS_RUNNING
    save_job(job_id, job)
    ImageGenJob.objects.filter(job_id=job_id).update(
        celery_task_id=self.request.id or "",
        started_at=timezone.now(),
    )

    try:
        orig = OriginalAsinData.objects.filter(pk=orig_pk).first()
        if not orig:
            raise RuntimeError("ASIN 记录不存在或已被删除")

        set_job_baseline(job_id, orig)

        with nano_banana_user_scope(user_id):
            if is_job_stopped(job_id):
                clear_job_cancel(job_id)
                increment_counter("jobs_cancelled")
                record_timing("batch", time.time() - started)
                return finalize_job_cancelled(job_id)
            payload = run_jobs_batch(
                orig,
                job_specs,
                user_notes=user_notes,
                progress_job_id=job_id,
            )

        existing = get_job(job_id) or {}
        if is_job_stopped(job_id):
            clear_job_cancel(job_id)
            increment_counter("jobs_cancelled")
            record_timing("batch", time.time() - started)
            existing.setdefault("job_id", job_id)
            return existing

        result = build_batch_result_payload(
            job_id=job_id,
            user_id=user_id,
            asin=orig.asin,
            batch_size=len(job_specs),
            run_payload=payload,
        )
        save_job(job_id, result)
        clear_job_cancel(job_id)
        if result.get("cancelled"):
            increment_counter("jobs_cancelled")
        else:
            increment_counter("jobs_completed")
        if result.get("partial"):
            increment_counter("jobs_partial")
        record_timing("batch", time.time() - started)
        return result
    except Exception as exc:
        logger.exception("run_jobs_batch_async failed job_id=%s orig_pk=%s", job_id, orig_pk)
        increment_counter("jobs_failed")
        record_timing("batch", time.time() - started)
        failed = {
            "job_id": job_id,
            "status": ImageGenJob.STATUS_DEAD,
            "user_id": int(user_id),
            "asin": job.get("asin") or "",
            "batch_size": len(job_specs),
            "added": 0,
            "errors": [],
            "error": str(exc),
            "finished_images": None,
            "data": None,
            "ok": False,
        }
        save_job(job_id, failed)
        increment_counter("jobs_dead")
        return failed
