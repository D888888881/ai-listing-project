"""AI 生图相关视图。"""
from __future__ import annotations

import io
import json
import logging
import os
import uuid
import zipfile
from typing import Any, Iterable, Optional

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ..ai_image_need_service import generate_image_need_for_asin
from ..asin_access import (
    filter_original_by_uploader_id,
    filter_original_by_user_id,
    is_asin_admin,
    original_asin_qs_for_user,
    parse_uploader_filter_user_id,
    stamp_created_by_if_empty,
    uploader_filter_context,
    user_can_access_asin,
)
from ..image_gen_jobs import (
    async_batch_enabled,
    cancel_job,
    enqueue_batch_job,
    get_active_job_for_user,
    job_payload_for_poll,
    reconcile_job_status,
    retry_job_from_db,
    start_batch_job_background,
)
from ..models import AsinAnalysis, OriginalAsinData
from ..nano_banana_service import (
    build_generation_plan,
    compute_generation_estimate,
    generation_wave_count,
    nano_banana_user_scope,
    optimize_finished_images,
    pending_jobs_payload,
    run_all_modules_generation,
    run_custom_module_generation,
    run_generation_wave,
    run_jobs_batch,
    run_one_module_generation,
    run_single_generation_job,
    topup_incomplete_modules,
    topup_one_chunk,
)
from ..pagination import paginate, pagination_querystring
from ..redis_concurrency import get_redis_client
from .common import _deny_asin_access, _redirect_with_q

logger = logging.getLogger(__name__)

_AI_IMAGE_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

def _normalize_stored_images(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            path = (item.get("path") or "").strip()
            if path:
                out.append({"path": path, "name": (item.get("name") or "").strip()})
        elif isinstance(item, str) and item.strip():
            out.append({"path": item.strip(), "name": ""})
    return out


def _normalize_original_image_item(item: Any) -> Optional[dict[str, str]]:
    if isinstance(item, str):
        s = item.strip()
        if not s:
            return None
        if s.startswith("http://") or s.startswith("https://"):
            return {"url": s}
        return {"path": s, "name": ""}
    if isinstance(item, dict):
        url = (item.get("url") or "").strip()
        if url:
            return {"url": url}
        path = (item.get("path") or "").strip()
        if path:
            return {"path": path, "name": (item.get("name") or "").strip()}
    return None


def _normalize_original_images_struct(raw: Any) -> dict[str, list[dict[str, str]]]:
    """原图/成品图：主图-副图 / A+ / 产品原生图 / 已优化；兼容旧版平铺列表。"""
    if isinstance(raw, dict) and (
        "main" in raw or "aplus" in raw or "native" in raw or "optimized" in raw
    ):
        main_raw = raw.get("main")
        aplus_raw = raw.get("aplus")
        native_raw = raw.get("native")
        optimized_raw = raw.get("optimized")
    elif isinstance(raw, list):
        main_raw, aplus_raw, native_raw, optimized_raw = raw, [], [], []
    else:
        main_raw, aplus_raw, native_raw, optimized_raw = [], [], [], []

    def _items_from_section(section_raw: Any) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        if isinstance(section_raw, list):
            for x in section_raw:
                n = _normalize_original_image_item(x)
                if n:
                    items.append(n)
        elif isinstance(section_raw, dict):
            for u in section_raw.get("images") or []:
                n = _normalize_original_image_item(u)
                if n:
                    items.append(n)
        return items

    return {
        "main": _items_from_section(main_raw),
        "aplus": _items_from_section(aplus_raw),
        "native": _items_from_section(native_raw),
        "optimized": _items_from_section(optimized_raw),
    }


def _original_images_flat(struct: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    return list(struct.get("main") or []) + list(struct.get("aplus") or [])


def _original_image_item_key(item: dict[str, str]) -> str:
    return (item.get("url") or item.get("path") or "").strip()


def _image_item_display_src(item: dict[str, str], media_url: str) -> str:
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


def _api_asin_block_to_url_items(block: Any) -> list[dict[str, str]]:
    if not isinstance(block, dict):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for u in block.get("images") or []:
        if isinstance(u, str):
            s = u.strip()
            if s and s not in seen:
                seen.add(s)
                out.append({"url": s})
    return out


def _merge_fetched_into_original(
    existing: dict[str, list[dict[str, str]]],
    fetched_main: list[dict[str, str]],
    fetched_aplus: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    """保留本地上传，用接口返回的 URL 列表覆盖远程 URL 项。"""

    def _merge_section(old_items: list[dict[str, str]], new_urls: list[dict[str, str]]) -> list[dict[str, str]]:
        local_items = [i for i in old_items if i.get("path")]
        seen = {_original_image_item_key(i) for i in local_items}
        merged = list(local_items)
        for item in new_urls:
            key = _original_image_item_key(item)
            if key and key not in seen:
                seen.add(key)
                merged.append(item)
        return merged

    return {
        "main": _merge_section(existing.get("main") or [], fetched_main),
        "aplus": _merge_section(existing.get("aplus") or [], fetched_aplus),
        "native": list(existing.get("native") or []),
    }


def _fetch_amazon_images_webhook(asins: list[str]) -> dict[str, Any]:
    asins_clean = []
    seen: set[str] = set()
    for a in asins:
        key = (a or "").strip().upper()
        if key and key not in seen:
            seen.add(key)
            asins_clean.append(key)
    if not asins_clean:
        return {}
    base = (getattr(settings, "AMAZON_IMAGE_WEBHOOK_URL", None) or "").strip().rstrip("/")
    if not base:
        raise ValueError("未配置 AMAZON_IMAGE_WEBHOOK_URL")
    url = f"{base}?message={quote(','.join(asins_clean))}"
    req = Request(url, headers={"Accept": "application/json"})
    timeout = float(getattr(settings, "AMAZON_IMAGE_FETCH_TIMEOUT", 120))
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        return {}
    return data


def _item_storage_path(item: dict[str, str]) -> Optional[str]:
    """本地 media 相对路径（仅 ai_images/ 下，排除外链 URL-only 项）。"""
    path = (item.get("path") or "").strip().replace("\\", "/")
    if not path or path.startswith("http://") or path.startswith("https://"):
        return None
    if ".." in path or path.startswith("/"):
        return None
    if not path.startswith("ai_images/"):
        return None
    return path


def _storage_paths_in_struct(struct: dict[str, list[dict[str, str]]]) -> set[str]:
    paths: set[str] = set()
    for section in ("main", "aplus", "native", "optimized"):
        for item in struct.get(section) or []:
            p = _item_storage_path(item)
            if p:
                paths.add(p)
    return paths


def _delete_storage_paths(paths: Iterable[str]) -> int:
    """删除已从 JSON 中移除的本地图片文件。"""
    deleted = 0
    for rel in paths:
        try:
            if default_storage.exists(rel):
                default_storage.delete(rel)
                deleted += 1
        except OSError as e:
            logger.warning("delete storage file failed %s: %s", rel, e)
    return deleted


def _save_uploaded_ai_images(files: Any, asin: str, kind: str, section: str = "") -> list[dict[str, str]]:
    saved: list[dict[str, str]] = []
    asin_key = (asin or "").strip().upper()
    if not asin_key:
        return saved
    sub = (section or "").strip().lower()
    if sub in ("main", "aplus", "optimized", "native"):
        folder = f"ai_images/{asin_key}/{kind}/{sub}"
    else:
        folder = f"ai_images/{asin_key}/{kind}"
    for f in files or []:
        if not f or not getattr(f, "name", None):
            continue
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in _AI_IMAGE_ALLOWED_EXT:
            continue
        rel = f"{folder}/{uuid.uuid4().hex[:12]}{ext}"
        default_storage.save(rel, f)
        saved.append({"path": rel, "name": os.path.basename(f.name)})
    return saved


def _original_images_row_payload(struct: dict[str, list[dict[str, str]]], media_url: str) -> dict[str, Any]:
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
            {"src": _image_item_display_src(i, media_url), "key": _original_image_item_key(i)}
            for i in main_items[:6]
        ],
        "aplus_previews": [
            {"src": _image_item_display_src(i, media_url), "key": _original_image_item_key(i)}
            for i in aplus_items[:6]
        ],
    }


def _ai_image_gen_next_url(request: HttpRequest) -> str:
    q_post = (request.POST.get("q") or "").strip()
    next_params: dict[str, str] = {}
    if q_post:
        next_params["q"] = q_post
    pres = (request.POST.get("preserve_query") or "").strip()
    if pres:
        return f"{reverse('ai_image_gen')}?{pres}"
    page_post = (request.POST.get("page") or "").strip()
    if page_post.isdigit() and int(page_post) > 1:
        next_params["page"] = page_post
    base = reverse("ai_image_gen")
    return f"{base}?{urlencode(next_params)}" if next_params else base


@login_required
def ai_image_gen(request: HttpRequest) -> HttpResponse:
    """AI 生图页：与原文本 ASIN 列表一致，可编辑主图/A+ 图需、上传原图与成品图。"""
    if is_asin_admin(request.user):
        analysis_asins = list(AsinAnalysis.objects.values_list("asin", flat=True))
        if analysis_asins:
            OriginalAsinData.objects.bulk_create(
                [OriginalAsinData(asin=a) for a in analysis_asins],
                ignore_conflicts=True,
            )

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        next_url = _ai_image_gen_next_url(request)
        asin = (request.POST.get("asin") or "").strip()
        if action in (
            "save_image_gen_inputs",
            "upload_original_images",
            "upload_finished_images",
            "generate_ai_image",
            "save_original_images_struct",
        ):
            if not asin:
                messages.error(request, "缺少 ASIN。")
                return redirect(next_url)
            if not user_can_access_asin(request.user, asin):
                messages.error(request, f"无权操作 ASIN：{asin}")
                return redirect(next_url)

        if action == "save_image_gen_inputs":
            orig, _ = OriginalAsinData.objects.get_or_create(asin=asin)
            stamp_created_by_if_empty(orig, request.user)
            orig.main_image_requirements = (request.POST.get("main_image_requirements") or "").strip()
            orig.aplus_image_requirements = (request.POST.get("aplus_image_requirements") or "").strip()
            orig.save(
                update_fields=[
                    "main_image_requirements",
                    "aplus_image_requirements",
                    "updated_at",
                ]
            )
            messages.success(request, f"{asin} 图需已保存。")
            return redirect(next_url)

        if action == "save_original_images_struct":
            orig, _ = OriginalAsinData.objects.get_or_create(asin=asin)
            stamp_created_by_if_empty(orig, request.user)
            try:
                payload = json.loads(request.POST.get("original_images_json") or "{}")
            except json.JSONDecodeError:
                messages.error(request, "原图数据格式无效。")
                return redirect(next_url)
            struct = _normalize_original_images_struct(payload)
            orig.original_images = struct
            orig.save(update_fields=["original_images", "updated_at"])
            messages.success(
                request,
                f"{asin} 原图已保存（主图-副图 {len(struct['main'])} 张，A+ {len(struct['aplus'])} 张）。",
            )
            return redirect(next_url)

        if action in ("upload_original_images", "upload_finished_images"):
            orig, _ = OriginalAsinData.objects.get_or_create(asin=asin)
            stamp_created_by_if_empty(orig, request.user)
            kind = "original" if action == "upload_original_images" else "finished"
            field = "original_images" if kind == "original" else "finished_images"
            section = (request.POST.get("image_section") or "").strip().lower()
            if kind == "original" and section not in ("main", "aplus"):
                messages.error(request, "请选择上传到主图-副图或 A+ 图模块。")
                return redirect(next_url)
            if kind == "finished" and section not in ("main", "aplus"):
                messages.error(request, "请选择上传到主图-副图或 A+ 图模块。")
                return redirect(next_url)
            uploaded = _save_uploaded_ai_images(
                request.FILES.getlist("images"),
                asin,
                kind,
                section=section,
            )
            if not uploaded:
                messages.error(request, "请选择有效的图片文件（jpg/png/gif/webp/bmp）。")
                return redirect(next_url)
            if kind == "original":
                struct = _normalize_original_images_struct(getattr(orig, field, None))
                struct[section] = list(struct.get(section) or []) + uploaded
                setattr(orig, field, struct)
            else:
                struct = _normalize_original_images_struct(getattr(orig, field, None))
                if section in ("main", "aplus"):
                    struct[section] = list(struct.get(section) or []) + uploaded
                    setattr(orig, field, struct)
                else:
                    existing = _normalize_stored_images(getattr(orig, field, None))
                    setattr(orig, field, existing + uploaded)
            orig.save(update_fields=[field, "updated_at"])
            if kind == "original":
                label = "主图-副图" if section == "main" else "A+ 图"
            else:
                label = "成品图"
            messages.success(request, f"{asin} 已上传 {len(uploaded)} 张{label}。")
            return redirect(next_url)

        if action == "generate_ai_image":
            user_notes = (request.POST.get("user_notes") or "").strip()
            main_req = (request.POST.get("main_image_requirements") or "").strip()
            aplus_req = (request.POST.get("aplus_image_requirements") or "").strip()
            ok, hint = _submit_ai_image_generation(
                request.user,
                asin,
                main_req=main_req,
                aplus_req=aplus_req,
                user_notes=user_notes,
            )
            if ok:
                messages.info(request, hint)
            else:
                messages.warning(request, hint)
            return redirect(next_url)

    q = (request.GET.get("q") or "").strip()
    filter_user_id = parse_uploader_filter_user_id(request.user, request)
    qs = (
        original_asin_qs_for_user(request.user)
        .select_related("created_by")
        .order_by("-updated_at", "-created_at")
    )
    if filter_user_id:
        qs = filter_original_by_uploader_id(qs, filter_user_id)
    if q:
        qs = qs.filter(asin__icontains=q)
    page_obj = paginate(request, qs)

    rows: list[dict[str, Any]] = []
    original_images_by_asin: dict[str, dict[str, list[dict[str, str]]]] = {}
    finished_images_by_asin: dict[str, dict[str, list[dict[str, str]]]] = {}
    for o in page_obj.object_list:
        orig_struct = _normalize_original_images_struct(getattr(o, "original_images", None))
        fin_struct = _normalize_original_images_struct(getattr(o, "finished_images", None))
        asin_key = (o.asin or "").strip().upper()
        original_images_by_asin[asin_key] = orig_struct
        finished_images_by_asin[asin_key] = fin_struct
        rows.append(
            {
                "asin": o.asin,
                "orig": o,
                "main_image_requirements": (getattr(o, "main_image_requirements", None) or "").strip(),
                "aplus_image_requirements": (getattr(o, "aplus_image_requirements", None) or "").strip(),
                "original_images": orig_struct,
                "original_main_count": len(orig_struct.get("main") or []),
                "original_aplus_count": len(orig_struct.get("aplus") or []),
                "original_native_count": len(orig_struct.get("native") or []),
                "finished_images": fin_struct,
                "finished_main_count": len(fin_struct.get("main") or []),
                "finished_aplus_count": len(fin_struct.get("aplus") or []),
                "finished_optimized_count": len(fin_struct.get("optimized") or []),
            }
        )

    media_url = settings.MEDIA_URL
    if media_url and not str(media_url).startswith("/"):
        media_url = f"/{media_url}"

    return render(
        request,
        "ai_image_gen.html",
        {
            "rows": rows,
            "original_images_by_asin": original_images_by_asin,
            "finished_images_by_asin": finished_images_by_asin,
            "search_q": q,
            "page_obj": page_obj,
            "pagination_qs": pagination_querystring(request),
            "media_url": media_url,
            "images_per_module": int(getattr(settings, "NANO_BANANA_IMAGES_PER_MODULE", 3)),
            "max_images_per_run": int(getattr(settings, "NANO_BANANA_MAX_IMAGES_PER_RUN", 48)),
            "parallel_workers": int(getattr(settings, "NANO_BANANA_MODULE_WORKERS", 6)),
            **uploader_filter_context(request.user, request),
        },
    )


def _ai_image_media_url() -> str:
    media_url = settings.MEDIA_URL
    if media_url and not str(media_url).startswith("/"):
        media_url = f"/{media_url}"
    return media_url or "/media/"


def _payload_finished_images(payload: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    raw = payload.get("finished_images_all") or payload.get("finished_images")
    return _normalize_original_images_struct(raw)


def _with_nano_user(user, fn, *args, **kwargs):
    """在按用户隔离的 API 并发槽位内执行生图逻辑。"""
    uid = getattr(user, "pk", None) or getattr(user, "id", None)
    with nano_banana_user_scope(uid):
        return fn(*args, **kwargs)


def _parse_fetch_asins(request: HttpRequest) -> list[str]:
    asins_raw = (request.POST.get("asins") or "").strip()
    if asins_raw:
        try:
            parsed = json.loads(asins_raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
        return [a.strip() for a in asins_raw.replace("，", ",").split(",") if a.strip()]
    single = (request.POST.get("asin") or "").strip()
    return [single] if single else []


@login_required
@require_POST
def ai_image_fetch_images(request: HttpRequest) -> JsonResponse:
    """从 webhook 批量/单条获取 Amazon 商品图 URL，写入原图 JSON。"""
    asins = _parse_fetch_asins(request)
    if not asins:
        return JsonResponse({"ok": False, "error": "请提供 ASIN。"}, status=400)
    for a in asins:
        if not user_can_access_asin(request.user, a):
            return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{a}"}, status=403)
    try:
        api_data = _fetch_amazon_images_webhook(asins)
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
        return JsonResponse({"ok": False, "error": f"获取图片失败：{e}"}, status=502)

    media_url = _ai_image_media_url()
    results: dict[str, Any] = {}
    errors: list[str] = []
    for asin in asins:
        key = asin.strip().upper()
        block = api_data.get(key) or api_data.get(asin) or api_data.get(asin.strip())
        if not isinstance(block, dict):
            errors.append(f"{key}：接口未返回数据")
            continue
        fetched_main = _api_asin_block_to_url_items(block.get("main"))
        fetched_aplus = _api_asin_block_to_url_items(block.get("aplus"))
        if not fetched_main and not fetched_aplus:
            errors.append(f"{key}：未解析到图片")
            continue
        orig, _ = OriginalAsinData.objects.get_or_create(asin=key)
        stamp_created_by_if_empty(orig, request.user)
        existing = _normalize_original_images_struct(orig.original_images)
        merged = _merge_fetched_into_original(existing, fetched_main, fetched_aplus)
        orig.original_images = merged
        orig.save(update_fields=["original_images", "updated_at"])
        results[key] = _original_images_row_payload(merged, media_url)

    if not results:
        return JsonResponse({"ok": False, "error": "；".join(errors) or "未获取到任何图片"}, status=502)
    return JsonResponse({"ok": True, "results": results, "errors": errors})


@login_required
@require_POST
def ai_image_save_original_json(request: HttpRequest) -> JsonResponse:
    """AJAX 保存原图分区（主图-副图 / A+）。"""
    asin = (request.POST.get("asin") or "").strip()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    try:
        payload = json.loads(request.POST.get("original_images_json") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "原图数据格式无效。"}, status=400)
    new_struct = _normalize_original_images_struct(payload)
    orig, _ = OriginalAsinData.objects.get_or_create(asin=asin)
    stamp_created_by_if_empty(orig, request.user)
    old_struct = _normalize_original_images_struct(orig.original_images)
    removed_paths = _storage_paths_in_struct(old_struct) - _storage_paths_in_struct(new_struct)
    _delete_storage_paths(removed_paths)
    orig.original_images = new_struct
    orig.save(update_fields=["original_images", "updated_at"])
    media_url = _ai_image_media_url()
    return JsonResponse(
        {
            "ok": True,
            "asin": asin,
            "deleted_files": len(removed_paths),
            "data": _original_images_row_payload(new_struct, media_url),
        }
    )


@login_required
@require_POST
def ai_image_upload_original_json(request: HttpRequest) -> JsonResponse:
    """原图弹窗内按模块上传。"""
    asin = (request.POST.get("asin") or "").strip()
    section = (request.POST.get("image_section") or "").strip().lower()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if section not in ("main", "aplus", "native"):
        return JsonResponse(
            {"ok": False, "error": "请指定 image_section=main、aplus 或 native。"},
            status=400,
        )
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    uploaded = _save_uploaded_ai_images(request.FILES.getlist("images"), asin, "original", section=section)
    if not uploaded:
        return JsonResponse({"ok": False, "error": "请选择有效的图片文件。"}, status=400)
    orig, _ = OriginalAsinData.objects.get_or_create(asin=asin)
    stamp_created_by_if_empty(orig, request.user)
    struct = _normalize_original_images_struct(orig.original_images)
    struct[section] = list(struct.get(section) or []) + uploaded
    orig.original_images = struct
    orig.save(update_fields=["original_images", "updated_at"])
    media_url = _ai_image_media_url()
    return JsonResponse(
        {
            "ok": True,
            "asin": asin,
            "section": section,
            "uploaded": len(uploaded),
            "data": _original_images_row_payload(struct, media_url),
        }
    )


@login_required
@require_POST
def ai_image_generate_need(request: HttpRequest) -> JsonResponse:
    """批量/单条生成主图图需或 A+ 图需（GPT 视觉分析）。"""
    kind = (request.POST.get("kind") or "").strip().lower()
    if kind not in ("main", "aplus"):
        return JsonResponse({"ok": False, "error": "参数 kind 须为 main 或 aplus。"}, status=400)
    asins = _parse_fetch_asins(request)
    if not asins:
        return JsonResponse({"ok": False, "error": "请提供 ASIN。"}, status=400)

    results: dict[str, Any] = {}
    errors: list[str] = []
    field = "main_image_requirements" if kind == "main" else "aplus_image_requirements"

    for asin in asins:
        key = asin.strip().upper()
        if not user_can_access_asin(request.user, asin):
            errors.append(f"{key}：无权操作")
            continue
        orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
        if not orig:
            errors.append(f"{key}：ASIN 不存在")
            continue
        try:
            payload = generate_image_need_for_asin(orig, kind)  # type: ignore[arg-type]
            results[key] = {
                "asin": orig.asin,
                "text": payload["text"],
                "image_count": payload["image_count"],
                field: payload["text"],
            }
        except Exception as e:
            errors.append(f"{key}：{e}")

    if not results:
        return JsonResponse({"ok": False, "error": "；".join(errors) or "未生成任何图需", "errors": errors}, status=502)
    return JsonResponse({"ok": True, "kind": kind, "field": field, "results": results, "errors": errors})


def _submit_ai_image_generation(
    user: Any,
    asin: str,
    *,
    main_req: str = "",
    aplus_req: str = "",
    user_notes: str = "",
) -> tuple[bool, str]:
    """校验 ASIN 生图前置条件（实际生图由按模块 API 执行）。"""
    asin_key = (asin or "").strip()
    if not asin_key:
        return False, "缺少 ASIN"
    if not user_can_access_asin(user, asin_key):
        return False, f"无权操作 ASIN：{asin_key}"
    orig, _ = OriginalAsinData.objects.get_or_create(asin=asin_key)
    stamp_created_by_if_empty(orig, user)
    main_req = (main_req or "").strip()
    aplus_req = (aplus_req or "").strip()
    if main_req:
        orig.main_image_requirements = main_req
    if aplus_req:
        orig.aplus_image_requirements = aplus_req
    if main_req or aplus_req:
        orig.save(
            update_fields=[
                "main_image_requirements",
                "aplus_image_requirements",
                "updated_at",
            ]
        )
    orig_struct = _normalize_original_images_struct(orig.original_images)
    if not _original_images_flat(orig_struct):
        return False, f"{asin_key} 尚未上传原图，请先上传后再生图"
    effective_main = main_req or (getattr(orig, "main_image_requirements", None) or "").strip()
    if not effective_main:
        return False, f"{asin_key} 请填写主图图需"
    try:
        build_generation_plan(orig)
    except ValueError as e:
        return False, f"{asin_key}：{e}"
    return True, f"{asin_key} 校验通过，共 {len(build_generation_plan(orig))} 个模块待生图"


@login_required
@require_POST
def ai_image_generate_plan(request: HttpRequest) -> JsonResponse:
    asin = (request.POST.get("asin") or "").strip()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)
    main_req = (request.POST.get("main_image_requirements") or "").strip()
    aplus_req = (request.POST.get("aplus_image_requirements") or "").strip()
    if main_req:
        orig.main_image_requirements = main_req
    if aplus_req:
        orig.aplus_image_requirements = aplus_req
    if main_req or aplus_req:
        orig.save(update_fields=["main_image_requirements", "aplus_image_requirements", "updated_at"])
    try:
        modules = build_generation_plan(orig)
        from ..ai_image_need_service import validate_generation_refs_for_plan

        validate_generation_refs_for_plan(orig, modules)
        estimate = compute_generation_estimate(orig, modules)
        pending = pending_jobs_payload(orig, modules)
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse(
        {
            "ok": True,
            "asin": orig.asin,
            "modules": modules,
            "total": len(modules),
            "estimate": estimate,
            "pending_jobs": pending,
        }
    )


@login_required
@require_POST
def ai_image_run_generation_job(request: HttpRequest) -> JsonResponse:
    """单张成品图生图（浏览器端可 6 路并行，缩短单次 HTTP 等待）。"""
    asin = (request.POST.get("asin") or "").strip()
    module_key = (request.POST.get("module_key") or "").strip()
    user_notes = (request.POST.get("user_notes") or "").strip()
    main_req = (request.POST.get("main_image_requirements") or "").strip()
    aplus_req = (request.POST.get("aplus_image_requirements") or "").strip()
    try:
        variant_index = int(request.POST.get("variant_index") or "0")
    except ValueError:
        variant_index = 0
    if not asin or not module_key:
        return JsonResponse({"ok": False, "error": "缺少 asin 或 module_key。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)
    if main_req:
        orig.main_image_requirements = main_req
    if aplus_req:
        orig.aplus_image_requirements = aplus_req
    if main_req or aplus_req:
        orig.save(update_fields=["main_image_requirements", "aplus_image_requirements", "updated_at"])
    try:
        payload = _with_nano_user(
            request.user,
            run_single_generation_job,
            orig,
            module_key,
            variant_index,
            user_notes=user_notes,
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)
    media_url = _ai_image_media_url()
    fin_struct = _normalize_original_images_struct(payload.get("finished_images"))
    return JsonResponse(
        {
            "ok": True,
            "asin": orig.asin,
            "added": payload.get("added") or 0,
            "skipped": payload.get("skipped"),
            "error": payload.get("error"),
            "module_key": payload.get("module_key"),
            "variant_index": payload.get("variant_index"),
            "title": payload.get("title"),
            "module_total": payload.get("module_total"),
            "target": payload.get("target"),
            "complete": payload.get("complete"),
            "finished_images": fin_struct,
            "data": _original_images_row_payload(fin_struct, media_url),
        }
    )


@login_required
@require_POST
def ai_image_run_jobs_batch(request: HttpRequest) -> JsonResponse:
    """一批成品图生图（服务端并行，单 HTTP 连接，避免 Broken pipe）。"""
    asin = (request.POST.get("asin") or "").strip()
    user_notes = (request.POST.get("user_notes") or "").strip()
    main_req = (request.POST.get("main_image_requirements") or "").strip()
    aplus_req = (request.POST.get("aplus_image_requirements") or "").strip()
    jobs_raw = (request.POST.get("jobs_json") or "").strip()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not jobs_raw:
        return JsonResponse({"ok": False, "error": "缺少 jobs_json。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    try:
        job_specs = json.loads(jobs_raw)
        if not isinstance(job_specs, list):
            raise ValueError("jobs_json 须为数组")
    except (json.JSONDecodeError, ValueError) as e:
        return JsonResponse({"ok": False, "error": f"任务列表无效：{e}"}, status=400)
    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)
    if main_req:
        orig.main_image_requirements = main_req
    if aplus_req:
        orig.aplus_image_requirements = aplus_req
    if main_req or aplus_req:
        orig.save(update_fields=["main_image_requirements", "aplus_image_requirements", "updated_at"])

    user_id = int(getattr(request.user, "pk", None) or 0)

    if async_batch_enabled():
        try:
            job_id, _task_id, job_snapshot = enqueue_batch_job(
                user=request.user,
                orig=orig,
                job_specs=job_specs,
                user_notes=user_notes,
            )
            return JsonResponse(
                {
                    "ok": True,
                    "async": True,
                    "job_id": job_id,
                    "batch_size": len(job_specs),
                    "asin": orig.asin,
                    "baseline_success_count": int(
                        (job_snapshot or {}).get("baseline_success_count") or 0
                    ),
                }
            )
        except Exception as e:
            logger.warning("async batch enqueue failed, falling back to sync: %s", e)

    sync_reason = ""
    if getattr(settings, "NANO_BANANA_ASYNC_BATCH", True) and get_redis_client() is not None:
        from ..image_gen_config import celery_workers_available

        if not celery_workers_available():
            sync_reason = "生图 Worker 未运行，已在 Web 进程后台执行（请启动 worker 以支持多人并发）"

    try:
        job_id, job_snapshot, _task_id = start_batch_job_background(
            user=request.user,
            orig=orig,
            job_specs=job_specs,
            user_notes=user_notes,
        )
    except Exception as e:
        logger.exception("start_batch_job_background failed asin=%s", asin)
        return JsonResponse({"ok": False, "error": str(e)}, status=502)

    return JsonResponse(
        {
            "ok": True,
            "async": True,
            "job_id": job_id,
            "batch_size": len(job_specs),
            "asin": orig.asin,
            "baseline_success_count": int(
                (job_snapshot or {}).get("baseline_success_count") or 0
            ),
            "sync_reason": sync_reason,
        }
    )


@login_required
@require_GET
def ai_image_run_jobs_batch_status(request: HttpRequest) -> JsonResponse:
    """轮询异步批量生图任务状态。"""
    job_id = (request.GET.get("job_id") or "").strip()
    if not job_id:
        return JsonResponse({"ok": False, "error": "缺少 job_id。"}, status=400)
    job = job_payload_for_poll(job_id)
    if not job:
        job = reconcile_job_status(job_id)
    if not job:
        return JsonResponse({"ok": False, "error": "任务不存在或已过期。"}, status=404)
    uid = int(getattr(request.user, "pk", None) or 0)
    if int(job.get("user_id") or 0) != uid:
        return JsonResponse({"ok": False, "error": "无权查看该任务。"}, status=403)
    status = job.get("status") or "pending"
    if status in ("completed", "failed", "dead", "cancelled"):
        return JsonResponse(
            {
                "ok": job.get("ok", status in ("completed", "cancelled")),
                "async": True,
                "job_id": job_id,
                "status": status,
                "added": int(job.get("added") or 0),
                "processed": int(job.get("processed") or job.get("added") or 0),
                "errors": job.get("errors") or [],
                "error": job.get("error") or "",
                "batch_size": int(job.get("batch_size") or 0),
                "asin": job.get("asin") or "",
                "finished_images": job.get("finished_images"),
                "finished_images_all": job.get("finished_images_all") or job.get("finished_images"),
                "data": job.get("data"),
                "partial": bool(job.get("errors") or status == "cancelled")
                and int(job.get("added") or 0) >= 0,
                "cancelled": status == "cancelled",
                "baseline_success_count": int(job.get("baseline_success_count") or 0),
            }
        )
    return JsonResponse(
        {
            "ok": True,
            "async": True,
            "job_id": job_id,
            "status": status,
            "added": int(job.get("added") or 0),
            "processed": int(job.get("processed") or job.get("added") or 0),
            "batch_size": int(job.get("batch_size") or 0),
            "asin": job.get("asin") or "",
            "errors": job.get("errors") or [],
            "finished_images": job.get("finished_images"),
            "finished_images_all": job.get("finished_images_all") or job.get("finished_images"),
            "data": job.get("data"),
            "baseline_success_count": int(job.get("baseline_success_count") or 0),
        }
    )


@login_required
@require_GET
def ai_image_run_jobs_batch_active(request: HttpRequest) -> JsonResponse:
    """当前用户进行中的批量生图任务（页面刷新后恢复轮询）。"""
    uid = int(getattr(request.user, "pk", None) or 0)
    job = get_active_job_for_user(uid)
    if job:
        job = job_payload_for_poll(job.get("job_id") or "") or job
    if not job or job.get("status") not in ("pending", "running"):
        return JsonResponse({"ok": True, "active": False})
    return JsonResponse(
        {
            "ok": True,
            "active": True,
            "job_id": job.get("job_id") or "",
            "status": job.get("status") or "pending",
            "added": int(job.get("added") or 0),
            "processed": int(job.get("processed") or job.get("added") or 0),
            "batch_size": int(job.get("batch_size") or 0),
            "asin": job.get("asin") or "",
            "finished_images": job.get("finished_images"),
            "finished_images_all": job.get("finished_images_all") or job.get("finished_images"),
            "data": job.get("data"),
            "errors": job.get("errors") or [],
            "baseline_success_count": int(job.get("baseline_success_count") or 0),
        }
    )


@login_required
@require_POST
def ai_image_run_jobs_batch_cancel(request: HttpRequest) -> JsonResponse:
    """用户主动停止进行中的批量生图任务。"""
    job_id = (request.POST.get("job_id") or "").strip()
    if not job_id:
        return JsonResponse({"ok": False, "error": "缺少 job_id。"}, status=400)
    uid = int(getattr(request.user, "pk", None) or 0)
    result = cancel_job(job_id, user_id=uid)
    if not result.get("ok"):
        return JsonResponse(result, status=403 if "无权" in (result.get("error") or "") else 404)
    return JsonResponse(result)


@login_required
@require_GET
def ai_image_finished_snapshot(request: HttpRequest) -> JsonResponse:
    """从数据库读取 ASIN 最新成品图（轮询时刷新表格用）。"""
    asin = (request.POST.get("asin") or request.GET.get("asin") or "").strip()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权查看 ASIN：{asin}"}, status=403)
    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)
    from ..ai_image_payload import (
        ai_image_media_url,
        count_generating_finished_images,
        count_successful_finished_images,
        original_images_row_payload,
    )
    from ..nano_banana_service import _display_finished_images_struct

    fin_struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
    display = _display_finished_images_struct(fin_struct)
    media_url = _ai_image_media_url()
    total_success = count_successful_finished_images(fin_struct)
    generating_count = count_generating_finished_images(fin_struct)
    return JsonResponse(
        {
            "ok": True,
            "asin": orig.asin,
            "finished_images": display,
            "finished_images_all": fin_struct,
            "data": original_images_row_payload(display, media_url),
            "total_success": total_success,
            "generating_count": generating_count,
        }
    )


@login_required
@require_POST
def ai_image_generate_wave(request: HttpRequest) -> JsonResponse:
    """执行单波生图，供前端逐波刷新成品图列。"""
    asin = (request.POST.get("asin") or "").strip()
    user_notes = (request.POST.get("user_notes") or "").strip()
    main_req = (request.POST.get("main_image_requirements") or "").strip()
    aplus_req = (request.POST.get("aplus_image_requirements") or "").strip()
    try:
        wave_index = int(request.POST.get("wave_index") or "0")
    except ValueError:
        wave_index = 0
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)
    if main_req:
        orig.main_image_requirements = main_req
    if aplus_req:
        orig.aplus_image_requirements = aplus_req
    if main_req or aplus_req:
        orig.save(update_fields=["main_image_requirements", "aplus_image_requirements", "updated_at"])
    try:
        plan = build_generation_plan(orig)
        payload = _with_nano_user(
            request.user,
            run_generation_wave,
            orig,
            user_notes=user_notes,
            wave_index=wave_index,
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)
    media_url = _ai_image_media_url()
    fin_struct = _normalize_original_images_struct(payload.get("finished_images"))
    estimate = payload.get("estimate") or compute_generation_estimate(orig, plan)
    return JsonResponse(
        {
            "ok": True,
            "asin": orig.asin,
            "wave_index": payload.get("wave_index"),
            "total_waves": payload.get("total_waves") or generation_wave_count(
                orig, plan, user_notes=user_notes
            ),
            "done": payload.get("done"),
            "added": payload.get("added") or 0,
            "remaining_jobs": payload.get("remaining_jobs"),
            "parallel_workers": payload.get("parallel_workers") or estimate.get("parallel_workers"),
            "estimate": estimate,
            "modules": payload.get("modules") or [],
            "incomplete": payload.get("incomplete") or [],
            "data": _original_images_row_payload(fin_struct, media_url),
            "finished_images": fin_struct,
        }
    )


@login_required
@require_POST
def ai_image_topup_chunk(request: HttpRequest) -> JsonResponse:
    """补全一批不足张数的模块，供前端逐批刷新成品图列。"""
    asin = (request.POST.get("asin") or "").strip()
    user_notes = (request.POST.get("user_notes") or "").strip()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)
    try:
        payload = _with_nano_user(request.user, topup_one_chunk, orig, user_notes=user_notes)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)
    media_url = _ai_image_media_url()
    fin_struct = _normalize_original_images_struct(payload.get("finished_images"))
    still = payload.get("still_incomplete") or []
    return JsonResponse(
        {
            "ok": True,
            "asin": orig.asin,
            "topped_up": payload.get("topped_up") or [],
            "still_incomplete": still,
            "has_more": payload.get("has_more"),
            "added": payload.get("added") or 0,
            "all_complete": len(still) == 0,
            "data": _original_images_row_payload(fin_struct, media_url),
            "finished_images": fin_struct,
        }
    )


@login_required
@require_POST
def ai_image_generate_all(request: HttpRequest) -> JsonResponse:
    """按 ASIN 批量生图：仅补未满槽位，单次最多 NANO_BANANA_MAX_IMAGES_PER_RUN 张。"""
    asin = (request.POST.get("asin") or "").strip()
    user_notes = (request.POST.get("user_notes") or "").strip()
    main_req = (request.POST.get("main_image_requirements") or "").strip()
    aplus_req = (request.POST.get("aplus_image_requirements") or "").strip()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)
    if main_req:
        orig.main_image_requirements = main_req
    if aplus_req:
        orig.aplus_image_requirements = aplus_req
    if main_req or aplus_req:
        orig.save(update_fields=["main_image_requirements", "aplus_image_requirements", "updated_at"])
    try:
        with nano_banana_user_scope(getattr(request.user, "pk", None)):
            payload = run_all_modules_generation(orig, user_notes=user_notes)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)
    media_url = _ai_image_media_url()
    fin_struct = _normalize_original_images_struct(payload.get("finished_images"))
    incomplete = payload.get("incomplete") or []
    return JsonResponse(
        {
            "ok": True,
            "asin": orig.asin,
            "modules": payload.get("modules") or [],
            "incomplete": incomplete,
            "added": payload.get("added") or 0,
            "all_complete": len(incomplete) == 0,
            "data": _original_images_row_payload(fin_struct, media_url),
            "finished_images": fin_struct,
        }
    )


@login_required
@require_POST
def ai_image_run_custom_module(request: HttpRequest) -> JsonResponse:
    """单模块生图：使用输入框中的模块图需，不读取表格图需。"""
    asin = (request.POST.get("asin") or "").strip()
    section = (request.POST.get("section") or "main").strip().lower()
    module_prompt = (request.POST.get("module_prompt") or "").strip()
    user_notes = (request.POST.get("user_notes") or "").strip()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not module_prompt:
        return JsonResponse({"ok": False, "error": "请填写模块图需内容。"}, status=400)
    if section not in ("main", "aplus"):
        return JsonResponse({"ok": False, "error": "section 须为 main 或 aplus。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)
    try:
        payload = _with_nano_user(
            request.user,
            run_custom_module_generation,
            orig,
            section=section,
            module_prompt=module_prompt,
            user_notes=user_notes,
        )
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except Exception as e:
        logger.exception("run_custom_module failed asin=%s", asin)
        return JsonResponse({"ok": False, "error": str(e)}, status=502)
    media_url = _ai_image_media_url()
    fin_struct = _payload_finished_images(payload)
    return JsonResponse(
        {
            "ok": True,
            "asin": orig.asin,
            "module_key": payload.get("module_key"),
            "title": payload.get("title"),
            "section": payload.get("section"),
            "added": payload.get("added"),
            "target": payload.get("target"),
            "module_total": payload.get("module_total"),
            "errors": payload.get("errors") or [],
            "data": _original_images_row_payload(fin_struct, media_url),
            "finished_images": fin_struct,
            "finished_images_all": fin_struct,
        }
    )


@login_required
@require_POST
def ai_image_generate_module(request: HttpRequest) -> JsonResponse:
    asin = (request.POST.get("asin") or "").strip()
    module_key = (request.POST.get("module_key") or "").strip()
    user_notes = (request.POST.get("user_notes") or "").strip()
    if not asin or not module_key:
        return JsonResponse({"ok": False, "error": "缺少 asin 或 module_key。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)
    try:
        payload = _with_nano_user(
            request.user,
            run_one_module_generation,
            orig,
            module_key,
            user_notes=user_notes,
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)
    media_url = _ai_image_media_url()
    fin_struct = _normalize_original_images_struct(payload.get("finished_images"))
    return JsonResponse(
        {
            "ok": True,
            "asin": orig.asin,
            "module_key": module_key,
            "title": payload.get("title"),
            "section": payload.get("section"),
            "added": payload.get("added"),
            "target": payload.get("target"),
            "module_total": payload.get("module_total"),
            "complete": payload.get("complete"),
            "errors": payload.get("errors") or [],
            "data": _original_images_row_payload(fin_struct, media_url),
            "finished_images": fin_struct,
        }
    )


@login_required
@require_POST
def ai_image_topup_modules(request: HttpRequest) -> JsonResponse:
    """首轮各模块生图完成后，补全未满张数的模块。"""
    asin = (request.POST.get("asin") or "").strip()
    user_notes = (request.POST.get("user_notes") or "").strip()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)
    try:
        payload = _with_nano_user(request.user, topup_incomplete_modules, orig, user_notes=user_notes)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)
    media_url = _ai_image_media_url()
    fin_struct = _normalize_original_images_struct(payload.get("finished_images"))
    still = payload.get("still_incomplete") or []
    return JsonResponse(
        {
            "ok": True,
            "asin": orig.asin,
            "topped_up": payload.get("topped_up") or [],
            "still_incomplete": still,
            "all_complete": len(still) == 0,
            "data": _original_images_row_payload(fin_struct, media_url),
            "finished_images": fin_struct,
        }
    )


@login_required
@require_POST
def ai_image_save_finished_json(request: HttpRequest) -> JsonResponse:
    asin = (request.POST.get("asin") or "").strip()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    try:
        payload = json.loads(request.POST.get("finished_images_json") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "成品图数据格式无效。"}, status=400)
    new_struct = _normalize_original_images_struct(payload)
    orig, _ = OriginalAsinData.objects.get_or_create(asin=asin)
    stamp_created_by_if_empty(orig, request.user)
    old_struct = _normalize_original_images_struct(orig.finished_images)
    removed_paths = _storage_paths_in_struct(old_struct) - _storage_paths_in_struct(new_struct)
    _delete_storage_paths(removed_paths)
    orig.finished_images = new_struct
    orig.save(update_fields=["finished_images", "updated_at"])
    media_url = _ai_image_media_url()
    return JsonResponse(
        {
            "ok": True,
            "asin": asin,
            "deleted_files": len(removed_paths),
            "data": _original_images_row_payload(new_struct, media_url),
            "finished_images": new_struct,
        }
    )


@login_required
@require_POST
def ai_image_upload_finished_json(request: HttpRequest) -> JsonResponse:
    asin = (request.POST.get("asin") or "").strip()
    section = (request.POST.get("image_section") or "").strip().lower()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if section not in ("main", "aplus", "optimized"):
        return JsonResponse({"ok": False, "error": "请指定 image_section=main、aplus 或 optimized。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)
    uploaded = _save_uploaded_ai_images(request.FILES.getlist("images"), asin, "finished", section=section)
    if not uploaded:
        return JsonResponse({"ok": False, "error": "请选择有效的图片文件。"}, status=400)
    orig, _ = OriginalAsinData.objects.get_or_create(asin=asin)
    stamp_created_by_if_empty(orig, request.user)
    struct = _normalize_original_images_struct(orig.finished_images)
    struct[section] = list(struct.get(section) or []) + uploaded
    orig.finished_images = struct
    orig.save(update_fields=["finished_images", "updated_at"])
    media_url = _ai_image_media_url()
    return JsonResponse(
        {
            "ok": True,
            "asin": asin,
            "section": section,
            "uploaded": len(uploaded),
            "data": _original_images_row_payload(struct, media_url),
            "finished_images": struct,
        }
    )


@login_required
@require_POST
def ai_image_optimize_finished(request: HttpRequest) -> JsonResponse:
    """按选定成品图 + 优化方案调用 Nano Banana，结果写入已优化图。"""
    asin = (request.POST.get("asin") or "").strip()
    optimization_plan = (
        (request.POST.get("optimization_plan") or "").strip()
        or (request.POST.get("optimization_content") or "").strip()
    )
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not optimization_plan:
        return JsonResponse({"ok": False, "error": "请填写优化内容与方案。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)

    source_keys: list[str] = []
    raw_keys = (request.POST.get("source_keys") or "").strip()
    if raw_keys:
        try:
            parsed = json.loads(raw_keys)
            if isinstance(parsed, list):
                source_keys = [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            source_keys = [k.strip() for k in raw_keys.replace("，", ",").split(",") if k.strip()]
    single = (request.POST.get("source_key") or "").strip()
    if single and single not in source_keys:
        source_keys.append(single)
    if not source_keys:
        return JsonResponse({"ok": False, "error": "请先选择要优化的图片。"}, status=400)

    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)
    try:
        payload = _with_nano_user(
            request.user,
            optimize_finished_images,
            orig,
            source_keys=source_keys,
            optimization_plan=optimization_plan,
            user_notes=(request.POST.get("user_notes") or "").strip(),
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)

    media_url = _ai_image_media_url()
    fin_struct = _normalize_original_images_struct(payload.get("finished_images"))
    return JsonResponse(
        {
            "ok": True,
            "asin": orig.asin,
            "added": payload.get("added"),
            "data": _original_images_row_payload(fin_struct, media_url),
            "finished_images": fin_struct,
        }
    )


_FINISHED_LOCAL_FOLDER = {
    "main": "main_image",
    "aplus": "aplus_image",
}


def _sanitize_zip_entry_name(name: str, *, default: str = "image") -> str:
    base = (name or "").strip() or default
    for ch in '<>:"/\\|?*':
        base = base.replace(ch, "_")
    return base[:120]


def _read_finished_image_bytes(item: dict[str, str]) -> tuple[bytes, str]:
    path = (item.get("path") or "").strip()
    if path:
        media_root = getattr(settings, "MEDIA_ROOT", "") or ""
        full = os.path.join(media_root, path.lstrip("/").replace("/", os.sep))
        if os.path.isfile(full):
            ext = os.path.splitext(full)[1].lower()
            if ext not in _AI_IMAGE_ALLOWED_EXT:
                ext = ".jpg"
            with open(full, "rb") as fh:
                return fh.read(), ext
    url = (item.get("url") or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=90) as resp:
            data = resp.read()
        ext = ".jpg"
        lower = url.lower()
        for cand in (".png", ".webp", ".jpeg", ".jpg", ".gif"):
            if cand in lower:
                ext = cand if cand != ".jpeg" else ".jpg"
                break
        return data, ext
    raise ValueError("图片缺少可下载的地址")


def _parse_finished_source_keys(request: HttpRequest) -> list[str]:
    source_keys: list[str] = []
    raw_keys = (request.POST.get("source_keys") or "").strip()
    if raw_keys:
        try:
            parsed = json.loads(raw_keys)
            if isinstance(parsed, list):
                source_keys = [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            source_keys = [k.strip() for k in raw_keys.replace("，", ",").split(",") if k.strip()]
    single = (request.POST.get("source_key") or "").strip()
    if single and single not in source_keys:
        source_keys.append(single)
    return source_keys


@login_required
@require_POST
def ai_image_download_finished(request: HttpRequest) -> HttpResponse:
    """将选中的主图/A+ 成品图打包为 ZIP：ASIN/main_image|aplus_image/文件名。"""
    asin = (request.POST.get("asin") or "").strip()
    if not asin:
        return JsonResponse({"ok": False, "error": "缺少 ASIN。"}, status=400)
    if not user_can_access_asin(request.user, asin):
        return JsonResponse({"ok": False, "error": f"无权操作 ASIN：{asin}"}, status=403)

    source_keys = _parse_finished_source_keys(request)
    if not source_keys:
        return JsonResponse({"ok": False, "error": "请先选择要保存的图片。"}, status=400)

    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        return JsonResponse({"ok": False, "error": "ASIN 不存在。"}, status=404)

    wanted = set(source_keys)
    struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
    asin_dir = (orig.asin or asin).strip().upper()
    buffer = io.BytesIO()
    added = 0
    errors: list[str] = []
    used_names: set[str] = set()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for section in ("main", "aplus"):
            folder = _FINISHED_LOCAL_FOLDER[section]
            for idx, item in enumerate(struct.get(section) or []):
                key = _original_image_item_key(item)
                if key not in wanted:
                    continue
                try:
                    data, ext = _read_finished_image_bytes(item)
                except (OSError, URLError, ValueError) as e:
                    errors.append(str(e))
                    continue
                base = _sanitize_zip_entry_name(
                    item.get("name") or os.path.basename((item.get("path") or item.get("url") or "")),
                    default=f"image_{idx + 1}",
                )
                if not base.lower().endswith(ext):
                    base = os.path.splitext(base)[0] + ext
                arcname = f"{asin_dir}/{folder}/{base}"
                stem, suffix = os.path.splitext(arcname)
                n = 2
                while arcname in used_names:
                    arcname = f"{stem}_{n}{suffix}"
                    n += 1
                used_names.add(arcname)
                zf.writestr(arcname, data)
                added += 1

    if not added:
        err = errors[0] if errors else "未找到可下载的选中图片"
        return JsonResponse({"ok": False, "error": err}, status=400)

    filename = f"{asin_dir}_finished.zip"
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_POST
def ai_image_batch_generate(request: HttpRequest) -> JsonResponse:
    """对勾选的多个 ASIN 批量提交生图请求。"""
    user_notes = (request.POST.get("user_notes") or "").strip()
    items: list[dict[str, str]] = []
    raw_items = (request.POST.get("items") or "").strip()
    if raw_items:
        try:
            parsed = json.loads(raw_items)
            if isinstance(parsed, list):
                for x in parsed:
                    if isinstance(x, dict) and (x.get("asin") or "").strip():
                        items.append(
                            {
                                "asin": str(x.get("asin")).strip(),
                                "main_image_requirements": (x.get("main_image_requirements") or "").strip(),
                                "aplus_image_requirements": (x.get("aplus_image_requirements") or "").strip(),
                            }
                        )
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "items 参数格式无效。"}, status=400)
    if not items:
        for asin in _parse_fetch_asins(request):
            items.append({"asin": asin, "main_image_requirements": "", "aplus_image_requirements": ""})
    if not items:
        return JsonResponse({"ok": False, "error": "请勾选至少一个 ASIN。"}, status=400)

    results: dict[str, Any] = {}
    errors: list[str] = []
    for item in items:
        asin = item["asin"]
        key = asin.strip().upper()
        ok, msg = _submit_ai_image_generation(
            request.user,
            asin,
            main_req=item.get("main_image_requirements") or "",
            aplus_req=item.get("aplus_image_requirements") or "",
            user_notes=user_notes,
        )
        if ok:
            results[key] = {"asin": asin, "message": msg}
        else:
            errors.append(f"{key}：{msg}")

    if not results:
        return JsonResponse({"ok": False, "error": "；".join(errors) or "未提交任何生图请求", "errors": errors}, status=502)
    return JsonResponse({"ok": True, "results": results, "errors": errors})

