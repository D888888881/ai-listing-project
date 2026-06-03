"""Nano Banana 生图：按图需模块逐块调用 API，每模块并行生成多张成品图。"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from .ai_image_need_service import _normalize_original_images_struct
from .models import OriginalAsinData

logger = logging.getLogger(__name__)

_generation_user_id: ContextVar[Optional[int]] = ContextVar("nano_banana_user_id", default=None)

_user_api_semaphores: dict[int, threading.Semaphore] = {}
_user_api_semaphores_guard = threading.Lock()
_global_api_semaphore: Optional[threading.Semaphore] = None
_global_api_semaphore_limit = -1


def _per_user_api_limit() -> int:
    per_user = int(getattr(settings, "NANO_BANANA_API_SEMAPHORE_PER_USER", 0))
    if per_user > 0:
        return per_user
    return int(getattr(settings, "NANO_BANANA_API_SEMAPHORE", 6))


def _global_api_limit() -> int:
    return max(1, int(getattr(settings, "NANO_BANANA_API_SEMAPHORE_GLOBAL", 24)))


def _global_api_semaphore_get() -> threading.Semaphore:
    global _global_api_semaphore, _global_api_semaphore_limit
    limit = _global_api_limit()
    if _global_api_semaphore is None or _global_api_semaphore_limit != limit:
        _global_api_semaphore = threading.Semaphore(limit)
        _global_api_semaphore_limit = limit
    return _global_api_semaphore


def _user_api_semaphore(user_id: int) -> threading.Semaphore:
    with _user_api_semaphores_guard:
        if user_id not in _user_api_semaphores:
            _user_api_semaphores[user_id] = threading.Semaphore(_per_user_api_limit())
        return _user_api_semaphores[user_id]


@contextmanager
def nano_banana_user_scope(user_id: Optional[int]):
    """标记当前请求所属用户，用于按用户分配 API 并行槽位。"""
    uid = int(user_id) if user_id else 0
    token = _generation_user_id.set(uid)
    try:
        yield
    finally:
        _generation_user_id.reset(token)


class _ApiConcurrencyGuard:
    """先占全站槽位，再占用户槽位；满则排队。每用户默认 6 路，互不挤占。"""

    def __enter__(self) -> "_ApiConcurrencyGuard":
        uid = _generation_user_id.get() or 0
        self._global_sem = _global_api_semaphore_get()
        self._user_sem = _user_api_semaphore(uid)
        self._global_sem.acquire()
        self._user_sem.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._user_sem.release()
        self._global_sem.release()


_asin_generation_locks: dict[str, threading.Lock] = {}
_asin_generation_locks_guard = threading.Lock()


def _asin_generation_lock(asin: str) -> threading.Lock:
    key = (asin or "").strip().upper()
    with _asin_generation_locks_guard:
        if key not in _asin_generation_locks:
            _asin_generation_locks[key] = threading.Lock()
        return _asin_generation_locks[key]


_IMAGES_PER_MODULE = int(getattr(settings, "NANO_BANANA_IMAGES_PER_MODULE", 3))
_MODULE_WORKERS = int(getattr(settings, "NANO_BANANA_MODULE_WORKERS", 6))
_VARIANT_MAX_RETRIES = int(getattr(settings, "NANO_BANANA_VARIANT_MAX_RETRIES", 1))
_VARIANT_RETRY_DELAY = float(getattr(settings, "NANO_BANANA_VARIANT_RETRY_DELAY", 2.0))
_PARALLEL_VARIANTS = bool(getattr(settings, "NANO_BANANA_PARALLEL_VARIANTS", True))
_POLL_INTERVAL = float(getattr(settings, "NANO_BANANA_POLL_INTERVAL", 2.0))
_POLL_MAX_WAIT = float(getattr(settings, "NANO_BANANA_POLL_MAX_WAIT", 300))


def _nano_api_url() -> str:
    return (getattr(settings, "NANO_BANANA_API_URL", None) or "https://grsai.dakka.com.cn/v1/api/generate").strip()


def _nano_api_key() -> str:
    key = (getattr(settings, "NANO_BANANA_API_KEY", None) or "").strip()
    if not key:
        key = (getattr(settings, "NANO_BANANA_BEARER_TOKEN", None) or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    return key


def _is_transient_api_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if isinstance(exc, (TimeoutError, URLError, ConnectionResetError, BrokenPipeError, OSError)):
        return True
    if isinstance(exc, RuntimeError) and "http 429" in msg:
        return True
    if isinstance(exc, RuntimeError) and "http 5" in msg:
        return True
    return any(
        k in msg
        for k in (
            "timeout",
            "timed out",
            "connection",
            "remote end closed",
            "without response",
            "temporarily",
            "rate limit",
            "too many",
            "connection reset",
            "interpreter shutdown",
        )
    )


def _http_post_json(url: str, payload: dict[str, Any], *, timeout: float = 120) -> dict[str, Any]:
    api_key = _nano_api_key()
    if not api_key:
        raise RuntimeError("未配置 NANO_BANANA_API_KEY")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # print(body,'2222222')
    last_err: Optional[BaseException] = None
    max_attempts = int(getattr(settings, "NANO_BANANA_HTTP_RETRIES", 1))
    for attempt in range(max_attempts):
        req = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Connection": "close",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError(f"API 返回非 JSON 对象：{raw[:300]}")
            return data
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_body)
                if isinstance(err_json, dict):
                    msg = err_json.get("error") or err_json.get("message") or err_json.get("msg") or err_body
                else:
                    msg = err_body
            except json.JSONDecodeError:
                msg = err_body
            err = RuntimeError(f"Nano Banana API HTTP {exc.code}: {str(msg)[:500]}")
            if exc.code >= 500 and attempt < max_attempts - 1:
                time.sleep(_VARIANT_RETRY_DELAY * (attempt + 1))
                last_err = err
                continue
            raise err from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt < max_attempts - 1 and _is_transient_api_error(exc):
                time.sleep(_VARIANT_RETRY_DELAY * (attempt + 1) * 1.5)
                continue
            if isinstance(exc, json.JSONDecodeError):
                raise ValueError(f"API 返回非 JSON：{exc}") from exc
            raise RuntimeError(f"Nano Banana API 请求失败：{exc}") from exc
    raise RuntimeError(f"Nano Banana API 请求失败：{last_err}") from last_err


def _extract_image_urls(data: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def _maybe_add(u: str) -> None:
        u = (u or "").strip()
        if not u.startswith("http"):
            return
        if u in seen:
            return
        seen.add(u)
        urls.append(u)

    def _walk(obj: Any) -> None:
        if isinstance(obj, str):
            if obj.startswith("http") and (
                re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", obj, re.I) or "image" in obj.lower()
            ):
                _maybe_add(obj)
            return
        if isinstance(obj, dict):
            for key in (
                "url",
                "imageUrl",
                "image_url",
                "resultImageUrl",
                "result_url",
                "originImageUrl",
                "output",
            ):
                val = obj.get(key)
                if isinstance(val, str):
                    _maybe_add(val)
            for val in obj.values():
                _walk(val)
            return
        if isinstance(obj, list):
            for x in obj:
                _walk(x)

    _walk(data)
    return urls


def _poll_task_if_needed(initial: dict[str, Any]) -> dict[str, Any]:
    """若返回 task id，则轮询结果接口（兼容 GrsAi 异步模式）。"""
    if _extract_image_urls(initial):
        return initial
    status = (initial.get("status") or "").lower()
    if status in ("failed", "error"):
        err = initial.get("error") or initial.get("message") or initial.get("msg") or "生图失败"
        raise RuntimeError(str(err))
    task_id = (initial.get("id") or initial.get("taskId") or initial.get("task_id") or "").strip()
    if not task_id:
        inner = initial.get("data")
        if isinstance(inner, dict):
            task_id = (inner.get("id") or inner.get("taskId") or inner.get("task_id") or "").strip()
    if not task_id:
        return initial

    base = _nano_api_url().rstrip("/")
    if base.endswith("/generate"):
        result_url = base.rsplit("/", 1)[0] + "/result"
    else:
        result_url = base + "/result"
    deadline = time.time() + _POLL_MAX_WAIT
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        try:
            polled = _http_post_json(result_url, {"id": task_id}, timeout=60)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            logger.warning("nano banana poll error: %s", e)
            continue
        if _extract_image_urls(polled):
            return polled
        st = (polled.get("status") or "").lower()
        progress = polled.get("progress")
        if progress == 100 or st in ("success", "succeeded", "completed", "done"):
            return polled
        if st in ("failed", "error"):
            err = polled.get("error") or polled.get("failure_reason") or polled.get("message") or "生图失败"
            raise RuntimeError(str(err))
    raise TimeoutError(f"生图任务 {task_id} 超时（>{int(_POLL_MAX_WAIT)}s）")


def _normalize_api_image_ref(ref: str) -> str:
    """API images 字段：公网 URL 或 base64 字符串（不含 data: 前缀）。"""
    ref = (ref or "").strip()
    if not ref:
        return ref
    if ref.startswith("data:") and ";base64," in ref:
        return ref.split(";base64,", 1)[1]
    return ref


def nano_banana_create_image(prompt: str, images: list[str], aspect_ratio: str = "1:1") -> str:
    with _ApiConcurrencyGuard():
        api_images = [_normalize_api_image_ref(img) for img in (images or []) if (img or "").strip()]
        payload = {
            "model": getattr(settings, "NANO_BANANA_MODEL", "nano-banana-2"),
            "prompt": prompt,
            "images": api_images,
            "aspectRatio": aspect_ratio,
            "imageSize": getattr(settings, "NANO_BANANA_IMAGE_SIZE", "2K"),
            "replyType": "json",
        }
        data = _http_post_json(
            _nano_api_url(),
            payload,
            timeout=float(getattr(settings, "NANO_BANANA_HTTP_TIMEOUT", 360)),
        )
        data = _poll_task_if_needed(data)
        urls = _extract_image_urls(data)
        if not urls:
            raise ValueError(f"API 未返回图片 URL：{json.dumps(data, ensure_ascii=False)[:500]}")
        return urls[0]


def parse_markdown_modules(text: str) -> list[dict[str, str]]:
    """将图需文本按 ## 标题拆分为模块（每次 API 只传一个模块）。"""
    raw = (text or "").strip()
    if not raw:
        return []
    parts = re.split(r"\n(?=##\s+)", raw)
    modules: list[dict[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part or not part.startswith("##"):
            # 跳过 ## 前的总标题/说明（如「【亚马逊主图与副图设计需求】」），不生成默认模块
            continue
        lines = part.split("\n", 1)
        title = lines[0].lstrip("#").strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        prompt = f"## {title}\n{body}" if body else f"## {title}"
        modules.append({"title": title, "prompt": prompt.strip()})
    return modules


def _slug(s: str) -> str:
    x = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (s or "").strip())[:40]
    return x.strip("_") or "module"


_PRODUCT_SWAP_INSTRUCTION = """【核心任务：产品替换式生图】
参考图分为两类：
1. 产品原生图（优先）：成品图中必须呈现的真实商品外观（颜色、造型、材质、配件以原生图为准）；
2. Listing 参考图：对应当前模块在亚马逊 Listing 中的主图或 A+ 的构图、场景、排版、文案区与光影。

请务必：
- 保留 Listing 参考图的场景、构图、排版、文字区域、模块结构，不要整体重设计；
- 仅将画面中的商品替换为产品原生图中的商品，可做适度质感与边缘优化；
- 不要换成其他产品，不要大幅改动版式与文案布局。"""


def _build_generation_full_prompt(module_prompt: str, section: str, user_notes: str = "") -> str:
    section_label = "主图-副图" if section == "main" else "A+ 图"
    parts = [
        _PRODUCT_SWAP_INSTRUCTION,
        f"\n\n【目标分区】{section_label}",
        f"\n【本模块图需】\n{(module_prompt or '').strip()}",
    ]
    if (user_notes or "").strip():
        parts.append(f"\n\n【补充说明】\n{user_notes.strip()}")
    return "".join(parts)


def _collect_generation_ref_images(orig: OriginalAsinData, section: str) -> list[str]:
    from .ai_image_need_service import collect_native_api_refs, collect_section_api_refs

    native_max = int(getattr(settings, "NANO_BANANA_NATIVE_MAX_REFS", 4))
    scene_max = int(getattr(settings, "NANO_BANANA_SCENE_MAX_REFS", 6))

    native_refs = collect_native_api_refs(orig, max_images=native_max)
    if not native_refs:
        raise ValueError(
            "请先在原图列上传「产品原生图」。批量生图将用其替换 Listing 图中的产品，场景与排版保持不变。"
        )

    scene_refs = collect_section_api_refs(
        orig,
        "main" if section == "main" else "aplus",
        max_images=scene_max,
    )
    if not scene_refs:
        label = "主图-副图" if section == "main" else "A+ 图"
        raise ValueError(
            f"原图列暂无{label}的远程参考 URL，请先「获取图片」（Listing 图须为 https 链接）。"
        )

    return native_refs + scene_refs


class _GenerationRefCache:
    """同一 ASIN 批量生图时按分区缓存参考图，避免每线程重复读盘/编码。"""

    def __init__(self, orig: OriginalAsinData) -> None:
        self.orig = orig
        self._refs_by_section: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def refs_for_section(self, section: str) -> list[str]:
        with self._lock:
            if section not in self._refs_by_section:
                self._refs_by_section[section] = _collect_generation_ref_images(self.orig, section)
            return self._refs_by_section[section]


def _collect_reference_urls(orig: OriginalAsinData, section: str) -> list[str]:
    return _collect_generation_ref_images(orig, section)


def _download_to_storage(url: str, rel_path: str) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=120) as resp:
                data = resp.read()
            ext = ".jpg"
            ctype = resp.headers.get("Content-Type", "")
            if "png" in ctype:
                ext = ".png"
            elif "webp" in ctype:
                ext = ".webp"
            if not rel_path.lower().endswith(ext):
                rel_path = re.sub(r"\.[a-zA-Z0-9]+$", "", rel_path) + ext
            default_storage.save(rel_path, ContentFile(data))
            return rel_path
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < 2:
                time.sleep(_VARIANT_RETRY_DELAY * (attempt + 1))
    raise RuntimeError(f"下载生成图失败：{last_err}") from last_err


def _generate_one_variant(
    *,
    asin: str,
    section: str,
    module_key: str,
    module_title: str,
    prompt: str,
    ref_images: list[str],
    aspect_ratio: str,
    variant_idx: int,
    user_notes: str,
) -> dict[str, str]:
    image_url = nano_banana_create_image(prompt, ref_images, aspect_ratio)
    asin_key = asin.strip().upper()
    mod_slug = _slug(module_title)
    rel = f"ai_images/{asin_key}/finished/{section}/{mod_slug}/{uuid.uuid4().hex[:10]}_v{variant_idx + 1}"
    saved = _download_to_storage(image_url, rel)
    return {
        "path": saved,
        "name": f"{module_title}-{variant_idx + 1}{Path(saved).suffix}",
        "module": module_title,
        "module_key": module_key,
        "section": section,
        "variant": variant_idx + 1,
    }


def _generate_variant_with_retry(
    *,
    asin: str,
    section: str,
    module_key: str,
    module_title: str,
    prompt: str,
    ref_images: list[str],
    aspect_ratio: str,
    variant_idx: int,
    user_notes: str,
) -> dict[str, str]:
    """单次生图，失败直接抛出，不重试。"""
    try:
        return _generate_one_variant(
            asin=asin,
            section=section,
            module_key=module_key,
            module_title=module_title,
            prompt=prompt,
            ref_images=ref_images,
            aspect_ratio=aspect_ratio,
            variant_idx=variant_idx,
            user_notes=user_notes,
        )
    except Exception as e:
        logger.warning(
            "nano banana variant failed asin=%s module=%s v=%s: %s",
            asin,
            module_title,
            variant_idx + 1,
            e,
        )
        raise RuntimeError(f"第 {variant_idx + 1} 张生图失败：{e}") from e


def _norm_module_label(value: str) -> str:
    return re.sub(r"^#+\s*", "", (value or "").strip())


def _item_is_successful_image(item: dict[str, str]) -> bool:
    path = (item.get("path") or "").strip()
    if not path:
        return False
    status = (item.get("status") or "").strip().lower()
    return status not in ("failed", "pending", "generating")


def _item_matches_module(
    item: dict[str, str],
    *,
    module_key: str,
    module_title: str,
) -> bool:
    key = (module_key or "").strip()
    title = _norm_module_label(module_title)
    ik = (item.get("module_key") or "").strip()
    if key and ik == key:
        return True
    if not ik and title and _norm_module_label(item.get("module") or "") == title:
        return True
    return False


def _group_key_for_finished_item(item: dict[str, str]) -> str:
    ik = (item.get("module_key") or "").strip()
    if ik:
        return ik
    return "_t:" + _norm_module_label(item.get("module") or "未命名模块")


def _sync_module_titles_from_plan(
    orig: OriginalAsinData,
    plan: list[dict[str, Any]],
    struct: dict[str, list[dict[str, str]]],
) -> bool:
    """将已有成品图的 module 标题同步为当前计划（不删图）。"""
    title_by_key: dict[tuple[str, str], str] = {}
    for mod in plan:
        section = mod.get("section") or ""
        mk = mod.get("key") or ""
        if not section or not mk:
            continue
        try:
            _, module = _module_by_key(orig, mk)
        except ValueError:
            continue
        label = (module.get("title") or "模块").strip()
        if label:
            title_by_key[(section, mk)] = label

    changed = False
    for section in ("main", "aplus"):
        for item in struct.get(section) or []:
            ik = (item.get("module_key") or "").strip()
            if not ik:
                continue
            plan_title = title_by_key.get((section, ik))
            if plan_title and (item.get("module") or "").strip() != plan_title:
                item["module"] = plan_title
                changed = True
    return changed


def _realign_finished_module_keys(
    orig: OriginalAsinData,
    plan: list[dict[str, Any]],
    struct: dict[str, list[dict[str, str]]],
) -> bool:
    """按模块标题把成品图 module_key 对齐到当前计划（修复去掉默认模块后的错位）。"""
    title_to_key: dict[tuple[str, str], str] = {}
    for mod in plan:
        section = mod.get("section") or ""
        mk = mod.get("key") or ""
        if not section or not mk:
            continue
        try:
            _, module = _module_by_key(orig, mk)
        except ValueError:
            continue
        label = _norm_module_label(module.get("title") or "")
        if label:
            title_to_key[(section, label)] = mk

    changed = False
    for section in ("main", "aplus"):
        for item in struct.get(section) or []:
            label = _norm_module_label(item.get("module") or "")
            if not label:
                continue
            target_key = title_to_key.get((section, label))
            if not target_key:
                continue
            current_key = (item.get("module_key") or "").strip()
            if current_key != target_key:
                item["module_key"] = target_key
                changed = True
    return changed


def _count_module_images(
    struct: dict[str, list[dict[str, str]]],
    section: str,
    module_title: str,
    *,
    module_key: str = "",
    successful_only: bool = False,
) -> int:
    """统计模块成品图张数；successful_only 时不计失败/占位条目。"""
    items = struct.get(section) or []
    n = 0
    for item in items:
        if not _item_matches_module(item, module_key=module_key, module_title=module_title):
            continue
        if successful_only and not _item_is_successful_image(item):
            continue
        n += 1
    return n


def _module_has_variant(
    struct: dict[str, list[dict[str, str]]],
    section: str,
    module_key: str,
    variant_index: int,
    *,
    module_title: str = "",
) -> bool:
    """该 variant 槽位是否已有记录（含成功、失败、生成中占位）。"""
    vno = variant_index + 1
    for item in struct.get(section) or []:
        if not _item_matches_module(item, module_key=module_key, module_title=module_title):
            continue
        try:
            iv = int(item.get("variant") or 0)
        except (TypeError, ValueError):
            iv = 0
        if iv == vno:
            return True
    return False


def _make_slot_placeholder(
    *,
    module_key: str,
    section: str,
    module_title: str,
    variant_index: int,
    status: str = "generating",
    error: str = "",
) -> dict[str, str]:
    vno = variant_index + 1
    title = module_title or "模块"
    return {
        "module_key": module_key,
        "module": title,
        "section": section,
        "variant": vno,
        "status": status,
        "path": "",
        "name": f"{title}-{vno}",
        "error": (error or "")[:300],
    }


def _remove_module_variant_items(
    struct: dict[str, list[dict[str, str]]],
    section: str,
    module_key: str,
    variant_index: int,
    *,
    module_title: str = "",
) -> None:
    vno = variant_index + 1
    kept: list[dict[str, str]] = []
    for item in struct.get(section) or []:
        if not _item_matches_module(item, module_key=module_key, module_title=module_title):
            kept.append(item)
            continue
        try:
            iv = int(item.get("variant") or 0)
        except (TypeError, ValueError):
            iv = 0
        if iv != vno:
            kept.append(item)
    struct[section] = kept


def _next_variant_index(
    struct: dict[str, list[dict[str, str]]],
    section: str,
    module_key: str,
    module_title: str,
) -> int:
    """返回下一可用 variant 序号（0-based，对应成品图 variant 字段为 index+1）。"""
    max_vno = 0
    for item in struct.get(section) or []:
        if not _item_matches_module(item, module_key=module_key, module_title=module_title):
            continue
        try:
            iv = int(item.get("variant") or 0)
        except (TypeError, ValueError):
            iv = 0
        max_vno = max(max_vno, iv)
    return max_vno


def _find_or_make_module_key(orig: OriginalAsinData, section: str, title: str) -> str:
    """按标题匹配计划内 module_key；否则使用 custom 键（单模块生图）。"""
    norm = _norm_module_label(title)
    try:
        plan = build_generation_plan(orig)
    except ValueError:
        plan = []
    for mod in plan:
        if mod.get("section") != section:
            continue
        try:
            _, module = _module_by_key(orig, mod["key"])
        except ValueError:
            continue
        if _norm_module_label(module.get("title") or "") == norm:
            return mod["key"]
    return f"{section}:custom_{_slug(title)[:24]}"


def _cap_finished_images_struct(
    orig: OriginalAsinData,
    plan: list[dict[str, Any]],
    *,
    target: Optional[int] = None,
    limit_per_module: bool = False,
) -> dict[str, list[dict[str, str]]]:
    """对齐 module_key、补全 variant、按槽位去重；默认保留全部历史成品图（仅 topup 时裁剪）。"""
    from .ai_image_need_service import _normalize_original_images_struct

    cap = max(1, target if target is not None else _IMAGES_PER_MODULE)
    struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
    changed = _realign_finished_module_keys(orig, plan, struct)
    if _sync_module_titles_from_plan(orig, plan, struct):
        changed = True

    for section_name in ("main", "aplus"):
        items = list(struct.get(section_name) or [])
        if not items:
            continue
        groups: dict[str, list[dict[str, str]]] = {}
        for item in items:
            groups.setdefault(_group_key_for_finished_item(item), []).append(item)

        kept: list[dict[str, str]] = []
        for _gk, group in groups.items():
            by_variant: dict[int, dict[str, str]] = {}
            no_variant: list[dict[str, str]] = []
            for item in group:
                try:
                    vno = int(item.get("variant") or 0)
                except (TypeError, ValueError):
                    vno = 0
                if vno <= 0:
                    no_variant.append(item)
                    continue
                prev = by_variant.get(vno)
                if prev is None:
                    by_variant[vno] = item
                elif _item_is_successful_image(item) and not _item_is_successful_image(prev):
                    by_variant[vno] = item

            max_vno = max(by_variant.keys(), default=0)
            for item in no_variant:
                if not _item_is_successful_image(item):
                    continue
                max_vno += 1
                item["variant"] = max_vno
                by_variant[max_vno] = item
                changed = True

            group_kept = [by_variant[v] for v in sorted(by_variant)]
            if limit_per_module:
                group_kept = group_kept[:cap]
            kept.extend(group_kept)

        if kept != items:
            changed = True
        struct[section_name] = kept

    if changed:
        orig.finished_images = struct
        orig.save(update_fields=["finished_images", "updated_at"])
    return struct


def _module_generation_common(
    orig: OriginalAsinData,
    *,
    section: str,
    module: dict[str, str],
    module_key: str,
    user_notes: str = "",
    ref_images: Optional[list[str]] = None,
    ref_cache: Optional[_GenerationRefCache] = None,
) -> tuple[str, dict[str, Any]]:
    """单模块生图公共参数（prompt、参考图等）。"""
    aspect = "1:1" if section == "main" else "21:9"
    title = module.get("title") or "模块"
    module_prompt = module.get("prompt") or title
    full_prompt = _build_generation_full_prompt(module_prompt, section, user_notes)
    if ref_images is None:
        if ref_cache is not None:
            ref_images = ref_cache.refs_for_section(section)
        else:
            ref_images = _collect_generation_ref_images(orig, section)
    common = dict(
        asin=orig.asin,
        section=section,
        module_key=module_key,
        module_title=title,
        prompt=full_prompt,
        ref_images=ref_images,
        aspect_ratio=aspect,
        user_notes="",
    )
    return title, common


def _run_variant_indices_parallel(
    common: dict[str, Any],
    variant_indices: list[int],
    *,
    module_title: str = "模块",
) -> tuple[list[dict[str, str]], list[str]]:
    """并行生成指定序号的多张图，全局并发不超过 _MODULE_WORKERS。"""
    items: list[dict[str, str]] = []
    errors: list[str] = []
    indices = variant_indices or []
    if not indices:
        return items, errors
    print(len(indices),_PARALLEL_VARIANTS,'232323')
    if _PARALLEL_VARIANTS and len(indices) > 1:
        workers = min(_MODULE_WORKERS, len(indices))
        results: dict[int, Optional[dict[str, str]]] = {i: None for i in indices}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                idx: pool.submit(
                    copy_context().run,
                    _generate_variant_with_retry,
                    variant_idx=idx,
                    **common,
                )
                for idx in indices
            }
            for idx, fut in futures.items():
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    errors.append(f"第{idx + 1}张：{e}")
        for idx in indices:
            if results[idx] is not None:
                items.append(results[idx])
    else:
        for n, idx in enumerate(indices):
            if n > 0:
                time.sleep(_VARIANT_RETRY_DELAY)
            try:
                items.append(_generate_variant_with_retry(variant_idx=idx, **common))
            except Exception as e:
                errors.append(f"第{idx + 1}张：{e}")

    if not items and indices:
        detail = "；".join(errors[:4]) if errors else "未知错误"
        raise RuntimeError(f"模块「{module_title}」未生成任何图片。{detail}")
    return items, errors


def _generate_module_variants(
    orig: OriginalAsinData,
    *,
    section: str,
    module: dict[str, str],
    module_key: str,
    user_notes: str = "",
    variant_indices: list[int],
) -> dict[str, Any]:
    """按指定 variant 序号生成图片，允许部分成功。"""
    title, common = _module_generation_common(
        orig,
        section=section,
        module=module,
        module_key=module_key,
        user_notes=user_notes,
    )
    indices = variant_indices or []
    items, errors = _run_variant_indices_parallel(common, indices, module_title=title)
    return {
        "items": items,
        "target": len(indices),
        "added": len(items),
        "complete": len(items) >= len(indices),
        "errors": errors,
    }


def _modules_per_parallel_wave() -> int:
    """每波可同时处理的模块数（例如 6 并发 ÷ 每模块 3 张 = 2 个模块）。"""
    per_mod = max(1, _IMAGES_PER_MODULE)
    return max(1, _MODULE_WORKERS // per_mod)


GenerationJob = tuple[str, str, dict[str, str], dict[str, Any], int]


def pending_jobs_payload(
    orig: OriginalAsinData,
    plan: list[dict[str, Any]],
    user_notes: str = "",
) -> list[dict[str, Any]]:
    """供前端并行调度的待生成任务列表（每项一张图）。"""
    jobs = collect_append_generation_jobs(orig, plan, user_notes=user_notes)
    out: list[dict[str, Any]] = []
    for module_key, section, module, _common, variant_index in jobs:
        out.append(
            {
                "module_key": module_key,
                "variant_index": variant_index,
                "section": section,
                "title": (module.get("title") or "模块"),
            }
        )
    return out


def _run_single_generation_job_unlocked(
    orig: OriginalAsinData,
    module_key: str,
    variant_index: int,
    user_notes: str = "",
    *,
    ref_cache: Optional[_GenerationRefCache] = None,
    generation_common: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """单张成品图：API 调用不持锁，写入成品图时由调用方加 ASIN 锁。"""
    from .ai_image_need_service import _normalize_original_images_struct

    target = max(1, _IMAGES_PER_MODULE)
    if generation_common:
        section = generation_common["section"]
        title = generation_common["module_title"]
        module_key = generation_common["module_key"]
    else:
        section, module = _module_by_key(orig, module_key)
        title = module.get("title") or "模块"
    lock = _asin_generation_lock(orig.asin)

    with lock:
        orig.refresh_from_db(fields=["finished_images"])
        struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
        if _module_has_variant(struct, section, module_key, variant_index, module_title=title):
            existing = _count_module_images(
                struct, section, title, module_key=module_key, successful_only=True
            )
            return {
                "added": 0,
                "skipped": True,
                "module_key": module_key,
                "variant_index": variant_index,
                "title": title,
                "module_total": existing,
                "target": target,
                "complete": _module_slots_filled(struct, section, module_key, title, target),
                "finished_images": struct,
            }
        _remove_module_variant_items(
            struct, section, module_key, variant_index, module_title=title
        )
        struct[section] = list(struct.get(section) or []) + [
            _make_slot_placeholder(
                module_key=module_key,
                section=section,
                module_title=title,
                variant_index=variant_index,
                status="generating",
            )
        ]
        orig.finished_images = struct
        orig.save(update_fields=["finished_images", "updated_at"])

    if generation_common is None:
        _title, common = _module_generation_common(
            orig,
            section=section,
            module=module,
            module_key=module_key,
            user_notes=user_notes,
            ref_cache=ref_cache,
        )
    else:
        common = generation_common
    try:
        item = _generate_variant_with_retry(variant_idx=variant_index, **common)
    except Exception as e:
        logger.warning(
            "generation job failed %s %s v%s: %s",
            orig.asin,
            module_key,
            variant_index + 1,
            e,
        )
        with lock:
            orig.refresh_from_db(fields=["finished_images"])
            struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
            _remove_module_variant_items(
                struct, section, module_key, variant_index, module_title=title
            )
            struct[section] = list(struct.get(section) or []) + [
                _make_slot_placeholder(
                    module_key=module_key,
                    section=section,
                    module_title=title,
                    variant_index=variant_index,
                    status="failed",
                    error=str(e),
                )
            ]
            orig.finished_images = struct
            orig.save(update_fields=["finished_images", "updated_at"])
            existing = _count_module_images(
                struct, section, title, module_key=module_key, successful_only=True
            )
        return {
            "added": 0,
            "error": str(e),
            "failed": True,
            "module_key": module_key,
            "variant_index": variant_index,
            "title": title,
            "module_total": existing,
            "target": target,
            "complete": _module_slots_filled(struct, section, module_key, title, target),
            "finished_images": struct,
        }

    with lock:
        orig.refresh_from_db(fields=["finished_images"])
        struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
        _remove_module_variant_items(
            struct, section, module_key, variant_index, module_title=title
        )
        struct[section] = list(struct.get(section) or []) + [item]
        orig.finished_images = struct
        orig.save(update_fields=["finished_images", "updated_at"])
        module_total = _count_module_images(
            struct, section, title, module_key=module_key, successful_only=True
        )
        return {
            "added": 1,
            "module_key": module_key,
            "variant_index": variant_index,
            "title": title,
            "module_total": module_total,
            "target": target,
            "complete": _module_slots_filled(struct, section, module_key, title, target),
            "finished_images": struct,
        }


def _module_slots_filled(
    struct: dict[str, list[dict[str, str]]],
    section: str,
    module_key: str,
    module_title: str,
    target: int,
) -> bool:
    return all(
        _module_has_variant(struct, section, module_key, v, module_title=module_title)
        for v in range(max(1, target))
    )


def _batch_finished_images_struct(batch: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """从 run_jobs_batch 结果取完整成品图（含历史），避免仅返回展示用子集。"""
    from .ai_image_need_service import _normalize_original_images_struct

    raw = batch.get("finished_images_all") or batch.get("finished_images")
    return _normalize_original_images_struct(raw)


def _display_finished_images_struct(
    struct: dict[str, list[dict[str, str]]],
) -> dict[str, list[dict[str, str]]]:
    """前端只展示成功落盘的图片，失败占位不显示。"""
    out = dict(struct)
    for sec in ("main", "aplus"):
        out[sec] = [
            item
            for item in (out.get(sec) or [])
            if _item_is_successful_image(item)
        ]
    if "optimized" in struct:
        out["optimized"] = list(struct.get("optimized") or [])
    return out


def run_single_generation_job(
    orig: OriginalAsinData,
    module_key: str,
    variant_index: int,
    user_notes: str = "",
) -> dict[str, Any]:
    return _run_single_generation_job_unlocked(orig, module_key, variant_index, user_notes)


def _run_generation_job_by_pk(
    orig_pk: int,
    module_key: str,
    variant_index: int,
    user_notes: str,
    ref_cache: Optional[_GenerationRefCache] = None,
    generation_common: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    orig = OriginalAsinData.objects.get(pk=orig_pk)
    return _run_single_generation_job_unlocked(
        orig,
        module_key,
        variant_index,
        user_notes,
        ref_cache=ref_cache,
        generation_common=generation_common,
    )


def _run_one_batch_job(
    orig_pk: int,
    spec: dict[str, Any],
    user_notes: str,
    ref_cache: _GenerationRefCache,
    common_by_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return _run_generation_job_by_pk(
        orig_pk,
        spec["module_key"],
        int(spec["variant_index"]),
        user_notes,
        ref_cache,
        common_by_key.get(spec.get("module_key") or ""),
    )


def run_jobs_batch(
    orig: OriginalAsinData,
    job_specs: list[dict[str, Any]],
    user_notes: str = "",
) -> dict[str, Any]:
    """服务端一批并行生图（单 HTTP 连接），避免浏览器多连接 Broken pipe。"""

    if not job_specs:
        struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
        return {"added": 0, "results": [], "errors": [], "finished_images": struct}

    orig_pk = orig.pk
    workers = min(_MODULE_WORKERS, len(job_specs))
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    added_total = 0
    ref_cache = _GenerationRefCache(orig)
    common_by_key: dict[str, dict[str, Any]] = {}
    for spec in job_specs:
        mk = spec.get("module_key") or ""
        if not mk or mk in common_by_key:
            continue
        inline_module = spec.get("inline_module")
        if isinstance(inline_module, dict) and inline_module.get("title"):
            section = (spec.get("section") or mk.split(":", 1)[0] or "main").strip()
            if section not in ("main", "aplus"):
                section = "main"
            _title, common = _module_generation_common(
                orig,
                section=section,
                module=inline_module,
                module_key=mk,
                user_notes=user_notes,
                ref_cache=ref_cache,
            )
        else:
            section, module = _module_by_key(orig, mk)
            _title, common = _module_generation_common(
                orig,
                section=section,
                module=module,
                module_key=mk,
                user_notes=user_notes,
                ref_cache=ref_cache,
            )
        common_by_key[mk] = common

    stagger = float(getattr(settings, "NANO_BANANA_BATCH_STAGGER_SEC", 0.5))
    use_threads = bool(getattr(settings, "NANO_BANANA_BATCH_USE_THREADS", True))

    def _collect_payload(spec: dict[str, Any], payload: dict[str, Any]) -> None:
        nonlocal added_total
        title = spec.get("title") or spec.get("module_key") or "模块"
        results.append(payload)
        if payload.get("added"):
            added_total += 1
        elif payload.get("error"):
            errors.append(f"{title} 第{int(spec['variant_index']) + 1}张：{payload['error']}")

    def _collect_exception(spec: dict[str, Any], exc: BaseException) -> None:
        title = spec.get("title") or spec.get("module_key") or "模块"
        msg = f"{title} 第{int(spec['variant_index']) + 1}张：{exc}"
        errors.append(msg)
        results.append({"error": str(exc), "module_key": spec.get("module_key")})
        logger.warning("batch job exception: %s", msg)

    ran_parallel = False
    if use_threads and workers > 1:
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        copy_context().run,
                        _run_one_batch_job,
                        orig_pk,
                        spec,
                        user_notes,
                        ref_cache,
                        common_by_key,
                    ): spec
                    for spec in job_specs
                }
                for fut in as_completed(futures):
                    spec = futures[fut]
                    try:
                        _collect_payload(spec, fut.result())
                    except Exception as e:
                        _collect_exception(spec, e)
            ran_parallel = True
        except RuntimeError as e:
            if "interpreter shutdown" not in str(e).lower():
                raise
            logger.warning("ThreadPool unavailable (%s), falling back to sequential batch", e)
            errors.append("服务热重载中，本批已改为顺序执行；请勿在生图时保存代码文件")

    if not ran_parallel:
        for spec in job_specs:
            if stagger > 0 and use_threads:
                time.sleep(stagger)
            try:
                payload = _run_one_batch_job(
                    orig_pk, spec, user_notes, ref_cache, common_by_key
                )
                _collect_payload(spec, payload)
            except Exception as e:
                _collect_exception(spec, e)

    orig.refresh_from_db(fields=["finished_images"])
    struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
    return {
        "added": added_total,
        "results": results,
        "errors": errors[:20],
        "finished_images": _display_finished_images_struct(struct),
        "finished_images_all": struct,
    }


def collect_append_generation_jobs(
    orig: OriginalAsinData,
    plan: list[dict[str, Any]],
    user_notes: str = "",
) -> list[GenerationJob]:
    """每模块追加 target 张新图（使用新 variant 序号，不覆盖已有成品图）。"""
    from .ai_image_need_service import _normalize_original_images_struct

    target = max(1, _IMAGES_PER_MODULE)
    _cap_finished_images_struct(orig, plan, limit_per_module=False)
    orig.refresh_from_db(fields=["finished_images"])
    struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
    jobs: list[GenerationJob] = []
    ref_cache = _GenerationRefCache(orig)
    for mod in plan:
        module_key = mod["key"]
        section, module = _module_by_key(orig, module_key)
        title = module.get("title") or "模块"
        _title, common = _module_generation_common(
            orig,
            section=section,
            module=module,
            module_key=module_key,
            user_notes=user_notes,
            ref_cache=ref_cache,
        )
        start = _next_variant_index(struct, section, module_key, title)
        for i in range(target):
            jobs.append((module_key, section, module, common, start + i))
    return jobs


def collect_missing_generation_jobs(
    orig: OriginalAsinData,
    plan: list[dict[str, Any]],
    user_notes: str = "",
) -> list[GenerationJob]:
    """兼容旧逻辑：仅补未满槽位（topup 等场景）。"""
    from .ai_image_need_service import _normalize_original_images_struct

    target = max(1, _IMAGES_PER_MODULE)
    _cap_finished_images_struct(orig, plan, target=target, limit_per_module=True)
    orig.refresh_from_db(fields=["finished_images"])
    struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
    jobs: list[GenerationJob] = []
    ref_cache = _GenerationRefCache(orig)
    for mod in plan:
        module_key = mod["key"]
        section, module = _module_by_key(orig, module_key)
        title = module.get("title") or "模块"
        _title, common = _module_generation_common(
            orig,
            section=section,
            module=module,
            module_key=module_key,
            user_notes=user_notes,
            ref_cache=ref_cache,
        )
        for v in range(target):
            if _module_has_variant(struct, section, module_key, v, module_title=title):
                continue
            jobs.append((module_key, section, module, common, v))
    return jobs


def compute_generation_estimate(
    orig: OriginalAsinData,
    plan: list[dict[str, Any]],
) -> dict[str, Any]:
    """统计计划生图张数（含已有张数与待补张数）。"""
    from .ai_image_need_service import _normalize_original_images_struct

    target = max(1, _IMAGES_PER_MODULE)
    _cap_finished_images_struct(orig, plan, limit_per_module=False)
    orig.refresh_from_db(fields=["finished_images"])
    struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
    module_count = len(plan)
    missing = module_count * target
    complete_modules = 0
    main_have = len(struct.get("main") or [])
    aplus_have = len(struct.get("aplus") or [])
    for mod in plan:
        section, module = _module_by_key(orig, mod["key"])
        title = module.get("title") or "模块"
        mk = mod["key"]
        ok_n = _count_module_images(struct, section, title, module_key=mk, successful_only=True)
        if ok_n >= target:
            complete_modules += 1
    total_waves = (missing + _MODULE_WORKERS - 1) // _MODULE_WORKERS if missing > 0 else 0
    return {
        "images_per_module": target,
        "module_count": module_count,
        "target_total": module_count * target,
        "missing_to_generate": missing,
        "append_per_run": missing,
        "already_have_main": main_have,
        "already_have_aplus": aplus_have,
        "complete_modules": complete_modules,
        "parallel_workers": _MODULE_WORKERS,
        "total_waves": total_waves,
    }


def generation_wave_count(
    orig: OriginalAsinData,
    plan: list[dict[str, Any]],
    user_notes: str = "",
) -> int:
    jobs = collect_missing_generation_jobs(orig, plan, user_notes=user_notes)
    if not jobs:
        return 0
    return (len(jobs) + _MODULE_WORKERS - 1) // _MODULE_WORKERS


def _execute_generation_jobs(
    orig: OriginalAsinData,
    jobs: list[GenerationJob],
    user_notes: str = "",
) -> tuple[int, list[dict[str, Any]], list[str], dict[str, list[dict[str, str]]]]:
    """并行执行一批 variant 任务（走 run_jobs_batch 安全写入）。"""
    from .ai_image_need_service import _normalize_original_images_struct

    target = max(1, _IMAGES_PER_MODULE)
    if not jobs:
        struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
        return 0, [], [], struct

    specs: list[dict[str, Any]] = []
    for module_key, _section, module, _common, variant_index in jobs:
        specs.append(
            {
                "module_key": module_key,
                "variant_index": variant_index,
                "title": module.get("title") or "模块",
            }
        )
    batch = run_jobs_batch(orig, specs, user_notes=user_notes)
    struct = _batch_finished_images_struct(batch) or _normalize_original_images_struct(
        getattr(orig, "finished_images", None)
    )
    added_total = int(batch.get("added") or 0)
    errors = batch.get("errors") or []
    touched_keys = {spec["module_key"] for spec in specs}

    module_results: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for key in touched_keys:
        section, module = _module_by_key(orig, key)
        title = module.get("title") or "模块"
        module_total = _count_module_images(struct, section, title, module_key=key)
        complete = module_total >= target
        key_errors = [e for e in errors if title in e or key in e]
        module_results.append(
            {
                "module_key": key,
                "title": title,
                "section": section,
                "added": added_total if len(touched_keys) == 1 else 0,
                "target": target,
                "module_total": module_total,
                "complete": complete,
                "errors": key_errors,
            }
        )
        if not complete:
            incomplete.append(f"{title}（{module_total}/{target}）")

    for mod in build_generation_plan(orig):
        section, module = _module_by_key(orig, mod["key"])
        title = module.get("title") or "模块"
        module_total = _count_module_images(struct, section, title, module_key=mod["key"])
        if module_total < target:
            label = f"{title}（{module_total}/{target}）"
            if label not in incomplete:
                incomplete.append(label)

    return added_total, module_results, incomplete, struct


def run_generation_wave(
    orig: OriginalAsinData,
    user_notes: str,
    wave_index: int,
) -> dict[str, Any]:
    """按「待生成张数」分波，每波最多 _MODULE_WORKERS 路 API 并行。"""
    from .ai_image_need_service import _normalize_original_images_struct

    plan = build_generation_plan(orig)
    estimate = compute_generation_estimate(orig, plan)
    jobs = collect_missing_generation_jobs(orig, plan, user_notes=user_notes)
    total_waves = generation_wave_count(orig, plan, user_notes=user_notes)
    if wave_index >= total_waves or not jobs:
        struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
        still: list[str] = []
        target = max(1, _IMAGES_PER_MODULE)
        for mod in plan:
            section, module = _module_by_key(orig, mod["key"])
            title = module.get("title") or "模块"
            total = _count_module_images(struct, section, title, module_key=mod["key"])
            if total < target:
                still.append(f"{title}（{total}/{target}）")
        return {
            "wave_index": wave_index,
            "total_waves": total_waves,
            "done": True,
            "added": 0,
            "modules": [],
            "incomplete": still,
            "finished_images": struct,
            "estimate": estimate,
            "remaining_jobs": len(jobs),
        }
    chunk = jobs[wave_index * _MODULE_WORKERS : (wave_index + 1) * _MODULE_WORKERS]
    added, module_results, incomplete, struct = _execute_generation_jobs(orig, chunk, user_notes)
    remaining_after = max(0, len(jobs) - (wave_index + 1) * _MODULE_WORKERS)
    return {
        "wave_index": wave_index,
        "total_waves": total_waves,
        "done": wave_index + 1 >= total_waves,
        "added": added,
        "modules": module_results,
        "incomplete": incomplete,
        "finished_images": struct,
        "estimate": estimate,
        "remaining_jobs": remaining_after,
        "parallel_workers": min(_MODULE_WORKERS, len(chunk)),
    }


def run_all_modules_generation(
    orig: OriginalAsinData,
    user_notes: str = "",
) -> dict[str, Any]:
    """按待生成张数分波并行生图，每波最多 _MODULE_WORKERS 路 API。"""
    plan = build_generation_plan(orig)
    total_waves = generation_wave_count(orig, plan, user_notes=user_notes)
    module_results: list[dict[str, Any]] = []
    incomplete: list[str] = []
    struct: dict[str, list[dict[str, str]]] = {"main": [], "aplus": [], "optimized": []}
    added_total = 0

    for wave_index in range(total_waves):
        payload = run_generation_wave(orig, user_notes, wave_index)
        added_total += int(payload.get("added") or 0)
        module_results.extend(payload.get("modules") or [])
        incomplete = payload.get("incomplete") or incomplete
        struct = payload.get("finished_images") or struct

    return {
        "modules": module_results,
        "incomplete": incomplete,
        "finished_images": struct,
        "added": added_total,
    }


def generate_module_images(
    orig: OriginalAsinData,
    *,
    section: str,
    module: dict[str, str],
    module_key: str = "",
    user_notes: str = "",
) -> dict[str, Any]:
    target = max(1, _IMAGES_PER_MODULE)
    return _generate_module_variants(
        orig,
        section=section,
        module=module,
        module_key=module_key or f"{section}:0",
        user_notes=user_notes,
        variant_indices=list(range(target)),
    )


def build_generation_plan(orig: OriginalAsinData) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    main_mods = parse_markdown_modules(getattr(orig, "main_image_requirements", None) or "")
    aplus_mods = parse_markdown_modules(getattr(orig, "aplus_image_requirements", None) or "")
    if not main_mods and not aplus_mods:
        raise ValueError("请先填写主图图需或 APlus 图需")
    for i, m in enumerate(main_mods):
        plan.append(
            {
                "key": f"main:{i}",
                "title": m["title"],
                "section": "main",
                "aspect_ratio": "1:1",
                "order": i,
            }
        )
    offset = len(main_mods)
    for i, m in enumerate(aplus_mods):
        plan.append(
            {
                "key": f"aplus:{i}",
                "title": m["title"],
                "section": "aplus",
                "aspect_ratio": "21:9",
                "order": offset + i,
            }
        )
    return plan


def _module_by_key(orig: OriginalAsinData, module_key: str) -> tuple[str, dict[str, str]]:
    parts = (module_key or "").split(":", 1)
    if len(parts) != 2 or parts[0] not in ("main", "aplus") or not parts[1].isdigit():
        raise ValueError(f"无效的 module_key：{module_key}")
    section, idx_s = parts[0], int(parts[1])
    text = (
        getattr(orig, "main_image_requirements", None) or ""
        if section == "main"
        else getattr(orig, "aplus_image_requirements", None) or ""
    )
    modules = parse_markdown_modules(text)
    if idx_s < 0 or idx_s >= len(modules):
        raise ValueError(f"模块不存在：{module_key}")
    return section, modules[idx_s]


def run_one_module_generation(
    orig: OriginalAsinData,
    module_key: str,
    user_notes: str = "",
) -> dict[str, Any]:
    from .ai_image_need_service import _normalize_original_images_struct

    target = max(1, _IMAGES_PER_MODULE)
    section, module = _module_by_key(orig, module_key)
    title = module.get("title") or "模块"
    struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
    start = _next_variant_index(struct, section, module_key, title)
    specs: list[dict[str, Any]] = [
        {"module_key": module_key, "variant_index": start + i, "title": title}
        for i in range(target)
    ]
    batch = run_jobs_batch(orig, specs, user_notes=user_notes)
    struct = _batch_finished_images_struct(batch) or struct
    module_total = _count_module_images(struct, section, title, module_key=module_key)
    return {
        "module_key": module_key,
        "section": section,
        "title": title,
        "added": int(batch.get("added") or 0),
        "target": target,
        "module_total": module_total,
        "complete": False,
        "errors": batch.get("errors") or [],
        "finished_images": struct,
    }


def parse_single_module_prompt(module_prompt: str) -> dict[str, str]:
    """解析单模块图需文本（建议以 ## 标题 开头）。"""
    mods = parse_markdown_modules(module_prompt)
    if not mods:
        text = (module_prompt or "").strip()
        if not text:
            raise ValueError("请填写模块图需内容（建议以 ## 模块名 开头）")
        return {"title": "自定义模块", "prompt": text}
    if len(mods) > 1:
        raise ValueError("单模块生图只能包含一个 ## 板块，请只保留一个模块")
    return mods[0]


def run_custom_module_generation(
    orig: OriginalAsinData,
    *,
    section: str,
    module_prompt: str,
    user_notes: str = "",
) -> dict[str, Any]:
    """使用输入框中的模块图需追加生图，不读取表格中的图需字段。"""
    from .ai_image_need_service import _normalize_original_images_struct

    if section not in ("main", "aplus"):
        raise ValueError("section 须为 main 或 aplus")
    module = parse_single_module_prompt(module_prompt)
    title = module.get("title") or "模块"
    module_key = _find_or_make_module_key(orig, section, title)
    target = max(1, _IMAGES_PER_MODULE)
    struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
    start = _next_variant_index(struct, section, module_key, title)
    specs: list[dict[str, Any]] = [
        {
            "module_key": module_key,
            "variant_index": start + i,
            "title": title,
            "section": section,
            "inline_module": module,
        }
        for i in range(target)
    ]
    batch = run_jobs_batch(orig, specs, user_notes=user_notes)
    struct = _batch_finished_images_struct(batch) or struct
    module_total = _count_module_images(struct, section, title, module_key=module_key)
    return {
        "module_key": module_key,
        "section": section,
        "title": title,
        "added": int(batch.get("added") or 0),
        "target": target,
        "module_total": module_total,
        "errors": batch.get("errors") or [],
        "finished_images": struct,
    }


def _collect_missing_variant_jobs(
    orig: OriginalAsinData,
    plan: list[dict[str, Any]],
    *,
    user_notes: str,
    target: int,
) -> list[tuple[str, str, dict[str, str], dict[str, Any], int]]:
    """收集所有未满模块的待生成 variant 任务。"""
    return collect_missing_generation_jobs(orig, plan, user_notes=user_notes)


def _list_still_incomplete(
    orig: OriginalAsinData,
    plan: list[dict[str, Any]],
    target: int,
) -> tuple[list[str], dict[str, list[dict[str, str]]]]:
    from .ai_image_need_service import _normalize_original_images_struct

    struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
    still_incomplete: list[str] = []
    for mod in plan:
        section, module = _module_by_key(orig, mod["key"])
        title = module.get("title") or "模块"
        mk = mod["key"]
        ok_n = _count_module_images(struct, section, title, module_key=mk, successful_only=True)
        if not _module_slots_filled(struct, section, mk, title, target):
            still_incomplete.append(f"{title}（成功 {ok_n}/{target}）")
    return still_incomplete, struct


_TOPUP_MAX_ROUNDS = int(getattr(settings, "NANO_BANANA_TOPUP_MAX_ROUNDS", 30))


def topup_one_chunk(
    orig: OriginalAsinData,
    user_notes: str = "",
) -> dict[str, Any]:
    """补全一轮：走 run_jobs_batch，严格每模块最多 target 张。"""
    target = max(1, _IMAGES_PER_MODULE)
    plan = build_generation_plan(orig)
    _cap_finished_images_struct(orig, plan, target=target, limit_per_module=True)
    jobs = _collect_missing_variant_jobs(orig, plan, user_notes=user_notes, target=target)
    topped_up: list[dict[str, Any]] = []
    added_total = 0

    if jobs:
        chunk = jobs[:_MODULE_WORKERS]
        specs = [
            {
                "module_key": module_key,
                "variant_index": variant_index,
                "title": module.get("title") or "模块",
            }
            for module_key, _section, module, _common, variant_index in chunk
        ]
        batch = run_jobs_batch(orig, specs, user_notes=user_notes)
        added_total = int(batch.get("added") or 0)
        struct = batch.get("finished_images") or _normalize_original_images_struct(
            getattr(orig, "finished_images", None)
        )
        touched = {spec["module_key"] for spec in specs}
        for module_key in touched:
            section, module = _module_by_key(orig, module_key)
            title = module.get("title") or "模块"
            module_total = _count_module_images(struct, section, title, module_key=module_key)
            topped_up.append(
                {
                    "module_key": module_key,
                    "title": title,
                    "added": added_total if len(touched) == 1 else 0,
                    "module_total": module_total,
                    "complete": module_total >= target,
                }
            )
    else:
        struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))

    still_incomplete, struct = _list_still_incomplete(orig, plan, target)
    has_more = bool(
        _collect_missing_variant_jobs(orig, plan, user_notes=user_notes, target=target)
    )
    return {
        "topped_up": topped_up,
        "still_incomplete": still_incomplete,
        "has_more": has_more,
        "added": added_total,
        "finished_images": struct,
    }


def topup_incomplete_modules(
    orig: OriginalAsinData,
    user_notes: str = "",
    *,
    max_rounds: int = 2,
) -> dict[str, Any]:
    """全部模块首轮完成后，补生成未满 target 张的模块（全局最多 _MODULE_WORKERS 并行）。"""
    target = max(1, _IMAGES_PER_MODULE)
    plan = build_generation_plan(orig)
    topped_up: list[dict[str, Any]] = []
    cap = min(_TOPUP_MAX_ROUNDS, max(1, max_rounds) * 100)

    for _ in range(cap):
        payload = topup_one_chunk(orig, user_notes=user_notes)
        topped_up.extend(payload.get("topped_up") or [])
        if not payload.get("has_more"):
            break
        if int(payload.get("added") or 0) <= 0:
            break

    still_incomplete, struct = _list_still_incomplete(orig, plan, target)
    return {
        "topped_up": topped_up,
        "still_incomplete": still_incomplete,
        "finished_images": struct,
    }


def _finished_item_key(item: dict[str, str]) -> str:
    return (item.get("url") or item.get("path") or "").strip()


def _finished_items_to_api_images(items: list[dict[str, str]]) -> list[str]:
    """将成品图项转为 Nano Banana 可接受的参考图（公网 URL 或 base64）。"""
    from .ai_image_need_service import _item_to_vision_url

    refs: list[str] = []
    for item in items:
        ref = _item_to_vision_url(item)
        if ref:
            refs.append(ref)
    return refs


def _find_finished_items_by_keys(
    struct: dict[str, list[dict[str, str]]],
    source_keys: list[str],
) -> list[tuple[str, dict[str, str]]]:
    wanted = {k.strip() for k in source_keys if k.strip()}
    if not wanted:
        raise ValueError("请先选择要优化的图片")
    found: list[tuple[str, dict[str, str]]] = []
    seen: set[str] = set()
    for section in ("main", "aplus", "optimized"):
        for item in struct.get(section) or []:
            key = _finished_item_key(item)
            if key in wanted and key not in seen:
                found.append((section, item))
                seen.add(key)
    if not found:
        raise ValueError("未找到所选成品图，请刷新后重试")
    missing = wanted - seen
    if missing:
        raise ValueError(f"部分图片未找到：{', '.join(sorted(missing)[:3])}")
    return found


def optimize_finished_images(
    orig: OriginalAsinData,
    *,
    source_keys: list[str],
    optimization_plan: str,
    user_notes: str = "",
) -> dict[str, Any]:
    """根据选定成品图与优化方案生成一张优化图，写入 finished_images.optimized。"""
    from .ai_image_need_service import _normalize_original_images_struct

    plan = (optimization_plan or "").strip()
    if not plan:
        raise ValueError("请填写优化内容与方案")

    struct = _normalize_original_images_struct(getattr(orig, "finished_images", None))
    picked = _find_finished_items_by_keys(struct, source_keys)
    picked_items = [item for _section, item in picked]
    ref_images = _finished_items_to_api_images(picked_items)
    from .ai_image_need_service import collect_native_vision_urls

    native_refs = collect_native_vision_urls(orig)
    if native_refs:
        ref_images = native_refs + ref_images
    if not ref_images:
        raise ValueError("所选图片无法读取为参考图，请确认文件仍存在或改用公网 URL")

    sections = {s for s, _ in picked}
    if "aplus" in sections:
        aspect = "21:9"
    else:
        aspect = "1:1"

    prompt = f"请根据以下优化方案优化图片，输出符合电商 Listing 要求的高质量成品图：\n\n{plan}"
    if user_notes:
        prompt = f"{prompt}\n\n【补充说明】\n{user_notes}"

    image_url = nano_banana_create_image(prompt, ref_images, aspect)
    asin_key = (orig.asin or "").strip().upper()
    rel = f"ai_images/{asin_key}/finished/optimized/{uuid.uuid4().hex[:10]}_opt"
    saved_path = _download_to_storage(image_url, rel)
    source_labels = ", ".join(
        dict.fromkeys((item.get("module") or item.get("name") or s for s, item in picked))
    )[:80]
    new_item: dict[str, str] = {
        "path": saved_path,
        "name": f"已优化-{source_labels or '成品图'}{Path(saved_path).suffix}",
        "module": "已优化",
        "source_keys": ",".join(source_keys),
        "optimization_plan": plan[:500],
    }
    struct["optimized"] = list(struct.get("optimized") or []) + [new_item]
    orig.finished_images = struct
    orig.save(update_fields=["finished_images", "updated_at"])
    return {
        "added": 1,
        "item": new_item,
        "finished_images": struct,
    }
