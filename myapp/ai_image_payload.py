"""生图 API 响应组装（Web / Celery 共用）。"""
from __future__ import annotations

from typing import Any

from django.conf import settings

from .ai_image_need_service import _normalize_original_images_struct


def ai_image_media_url() -> str:
    media_url = settings.MEDIA_URL
    if media_url and not str(media_url).startswith("/"):
        media_url = f"/{media_url}"
    return media_url or "/media/"


def payload_finished_images(payload: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    raw = payload.get("finished_images_all") or payload.get("finished_images")
    return _normalize_original_images_struct(raw)


def _image_item_key(item: dict[str, str]) -> str:
    return (item.get("url") or item.get("path") or "").strip()


def image_item_display_src(item: dict[str, str], media_url: str) -> str:
    url = (item.get("url") or "").strip()
    if url:
        return url
    path = (item.get("path") or "").strip()
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base = media_url if media_url.endswith("/") else f"{media_url}/"
    return f"{base}{path.lstrip('/')}"


def original_images_row_payload(struct: dict[str, list[dict[str, str]]], media_url: str) -> dict[str, Any]:
    main_items = struct.get("main") or []
    aplus_items = struct.get("aplus") or []
    native_items = struct.get("native") or []
    optimized_items = struct.get("optimized") or []
    return {
        "main": main_items,
        "aplus": aplus_items,
        "native": native_items,
        "optimized": optimized_items,
        "main_count": len(main_items),
        "aplus_count": len(aplus_items),
        "native_count": len(native_items),
        "optimized_count": len(optimized_items),
        "main_previews": [
            {"src": image_item_display_src(i, media_url), "key": _image_item_key(i)}
            for i in main_items[:6]
        ],
        "aplus_previews": [
            {"src": image_item_display_src(i, media_url), "key": _image_item_key(i)}
            for i in aplus_items[:6]
        ],
    }


def count_successful_finished_images(struct: dict[str, list[dict[str, str]]]) -> int:
    from .nano_banana_service import _display_finished_images_struct

    display = _display_finished_images_struct(_normalize_original_images_struct(struct))
    return len(display.get("main") or []) + len(display.get("aplus") or [])


def count_generating_finished_images(struct: dict[str, list[dict[str, str]]]) -> int:
    """统计正在生成中的占位槽（用于进度条 interim 更新）。"""
    normalized = _normalize_original_images_struct(struct)
    total = 0
    for sec in ("main", "aplus"):
        for item in normalized.get(sec) or []:
            status = (item.get("status") or "").strip().lower()
            if status == "generating":
                total += 1
    return total


def build_batch_result_payload(
    *,
    job_id: str,
    user_id: int,
    asin: str,
    batch_size: int,
    run_payload: dict[str, Any],
) -> dict[str, Any]:
    from .nano_banana_service import _display_finished_images_struct

    media_url = ai_image_media_url()
    fin_struct = payload_finished_images(run_payload)
    display = _display_finished_images_struct(fin_struct)
    row_payload = original_images_row_payload(display, media_url)
    from .image_gen_jobs import get_job

    job = get_job(job_id) or {}
    baseline = int(job.get("baseline_success_count") or 0)
    current = count_successful_finished_images(fin_struct)
    added = max(int(run_payload.get("added") or 0), max(0, current - baseline))
    errors = run_payload.get("errors") or []
    cancelled = bool(run_payload.get("cancelled"))
    status = "cancelled" if cancelled else "completed"
    return {
        "job_id": job_id,
        "status": status,
        "user_id": int(user_id),
        "asin": asin,
        "batch_size": batch_size,
        "added": added,
        "processed": int(run_payload.get("processed") or added),
        "errors": errors,
        "error": "",
        "finished_images": display,
        "finished_images_all": fin_struct,
        "data": row_payload,
        "ok": True,
        "partial": bool(errors or cancelled) and added >= 0,
        "cancelled": cancelled,
        "baseline_success_count": baseline,
    }
