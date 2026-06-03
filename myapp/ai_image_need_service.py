"""AI 生图页：主图 / A+ 图需 GPT 视觉分析（逻辑来自 script/chat_gpt_*_image_txt.py）。"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import threading
from pathlib import Path
from typing import Any, Literal, Optional

from django.conf import settings

from .gpt_analysis_service import _extract_json_object, _openai_settings
from .models import OriginalAsinData

ImageNeedKind = Literal["main", "aplus"]

_SCRIPT_DIR = Path(settings.BASE_DIR) / "script"

_MAIN_IMAGE_SECTION_LABELS: dict[str, str] = {
    "main_image": "主图",
    "image_2_core_selling_point": "副图2 · 核心卖点",
    "image_3_size_comparison": "副图3 · 尺寸对比",
    "image_4_detail_closeup": "副图4 · 细节特写",
    "image_5_usage_steps": "副图5 · 使用步骤",
    "image_6_package_contents": "副图6 · 包装清单",
    "image_7_function_breakdown": "副图7 · 功能拆解",
    "image_8_extended_scene": "副图8 · 延伸场景",
}


def _read_script_system_prompt(filename: str) -> str:
    path = _SCRIPT_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"未找到脚本：{path}")
    text = path.read_text(encoding="utf-8")
    for pat in (
        r'system_prompt\s*=\s*"""([\s\S]*?)"""',
        r"system_prompt\s*=\s*'''([\s\S]*?)'''",
        r'system_prompt\s*=\s*"([\s\S]*?)"',
    ):
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    raise ValueError(f"无法从 {filename} 解析 system_prompt")


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

    def _from_section(section_raw: Any) -> list[dict[str, str]]:
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
        "main": _from_section(main_raw),
        "aplus": _from_section(aplus_raw),
        "native": _from_section(native_raw),
        "optimized": _from_section(optimized_raw),
    }


def _media_base_url() -> str:
    media_url = settings.MEDIA_URL or "/media/"
    if not str(media_url).startswith("/"):
        media_url = f"/{media_url}"
    return media_url if media_url.endswith("/") else f"{media_url}/"


def _item_public_url(item: dict[str, str]) -> str:
    url = (item.get("url") or "").strip()
    if url:
        return url
    path = (item.get("path") or "").strip()
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base = _media_base_url()
    return f"{base}{path.lstrip('/')}"


_api_ref_b64_cache: dict[str, tuple[float, str]] = {}
_api_ref_b64_cache_lock = threading.Lock()


def _media_public_base() -> str:
    """生图 API 可拉取的媒体根 URL（须公网或 API 能访问的内网地址）。"""
    for key in ("NANO_BANANA_MEDIA_PUBLIC_BASE", "SITE_BASE_URL"):
        base = (getattr(settings, key, None) or "").strip()
        if base:
            return base.rstrip("/")
    return ""


def _encode_local_image_base64(full: Path) -> str:
    path_key = str(full.resolve())
    mtime = full.stat().st_mtime
    with _api_ref_b64_cache_lock:
        cached = _api_ref_b64_cache.get(path_key)
        if cached and cached[0] == mtime:
            return cached[1]
    mime = mimetypes.guess_type(full.name)[0] or "image/jpeg"
    data = base64.standard_b64encode(full.read_bytes()).decode("ascii")
    ref = f"data:{mime};base64,{data}"
    with _api_ref_b64_cache_lock:
        _api_ref_b64_cache[path_key] = (mtime, ref)
    return ref


def _item_to_vision_url(item: dict[str, str]) -> Optional[str]:
    """GPT 视觉分析：本地图转 base64。"""
    url = (item.get("url") or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    path = (item.get("path") or "").strip()
    if not path and url.startswith("/"):
        media_prefix = _media_base_url().rstrip("/")
        if url == media_prefix or url.startswith(media_prefix + "/"):
            path = url[len(media_prefix) :].lstrip("/")
    if not path:
        return None
    media_root = Path(settings.MEDIA_ROOT)
    full = media_root / path.lstrip("/")
    if not full.is_file():
        return None
    return _encode_local_image_base64(full)


def _item_http_url_only(item: dict[str, str]) -> Optional[str]:
    """仅接受公网/远程 http(s) URL（主图-副图、A+ 场景参考）。"""
    for raw in ((item.get("url") or "").strip(), (item.get("path") or "").strip()):
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
    return None


def _item_to_native_api_ref(item: dict[str, str]) -> Optional[str]:
    """产品原生图：支持 https URL、可访问的 media 公网地址，或本地 base64。"""
    url = (item.get("url") or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    path = (item.get("path") or "").strip()
    if not path and url.startswith("/"):
        media_prefix = _media_base_url().rstrip("/")
        if url == media_prefix or url.startswith(media_prefix + "/"):
            path = url[len(media_prefix) :].lstrip("/")
    if not path:
        return None
    prefer_url = bool(getattr(settings, "NANO_BANANA_PREFER_URL_REFS", True))
    public_base = _media_public_base()
    if prefer_url and public_base:
        return f"{public_base}/{path.lstrip('/')}"
    media_root = Path(settings.MEDIA_ROOT)
    full = media_root / path.lstrip("/")
    if not full.is_file():
        return None
    return _encode_local_image_base64(full)


def collect_section_api_refs(
    orig: OriginalAsinData,
    section: ImageNeedKind,
    *,
    max_images: Optional[int] = None,
) -> list[str]:
    """主图-副图 / A+：仅使用远程 URL 参考（Listing 图），不走本地 base64。"""
    struct = _normalize_original_images_struct(getattr(orig, "original_images", None))
    items = struct.get(section) or []
    cap = max_images
    if cap is None:
        cap = int(
            getattr(settings, "AI_IMAGE_NEED_MAX_IMAGES_MAIN", 8)
            if section == "main"
            else getattr(settings, "AI_IMAGE_NEED_MAX_IMAGES_APLUS", 15)
        )
    urls: list[str] = []
    for item in items[:cap]:
        ref = _item_http_url_only(item)
        if ref:
            urls.append(ref)
    return urls


def collect_native_api_refs(
    orig: OriginalAsinData,
    *,
    max_images: Optional[int] = None,
) -> list[str]:
    """产品原生图：本地文件或 URL，用于替换 Listing 图中的商品主体。"""
    struct = _normalize_original_images_struct(getattr(orig, "original_images", None))
    items = struct.get("native") or []
    cap = max_images
    if cap is None:
        cap = int(getattr(settings, "NANO_BANANA_NATIVE_MAX_REFS", 4))
    urls: list[str] = []
    for item in items[:cap]:
        ref = _item_to_native_api_ref(item)
        if ref:
            urls.append(ref)
    return urls


def collect_section_vision_urls(
    orig: OriginalAsinData,
    section: ImageNeedKind,
    *,
    max_images: Optional[int] = None,
) -> list[str]:
    struct = _normalize_original_images_struct(getattr(orig, "original_images", None))
    items = struct.get(section) or []
    cap = max_images
    if cap is None:
        cap = int(getattr(settings, "AI_IMAGE_NEED_MAX_IMAGES_MAIN", 8) if section == "main"
                  else getattr(settings, "AI_IMAGE_NEED_MAX_IMAGES_APLUS", 15))
    urls: list[str] = []
    for item in items[:cap]:
        vu = _item_to_vision_url(item)
        if vu:
            urls.append(vu)
    return urls


def collect_native_vision_urls(
    orig: OriginalAsinData,
    *,
    max_images: Optional[int] = None,
) -> list[str]:
    """产品原生图：批量生图时用于替换 Listing 图中的商品主体。"""
    struct = _normalize_original_images_struct(getattr(orig, "original_images", None))
    items = struct.get("native") or []
    cap = max_images
    if cap is None:
        cap = int(getattr(settings, "NANO_BANANA_NATIVE_MAX_REFS", 4))
    urls: list[str] = []
    for item in items[:cap]:
        vu = _item_to_vision_url(item)
        if vu:
            urls.append(vu)
    return urls


def _vision_model() -> str:
    return getattr(settings, "OPENAI_VISION_MODEL", "gpt-4o")


def _call_vision_json(system_prompt: str, image_urls: list[str], *, max_tokens: int) -> dict[str, Any]:
    from openai import OpenAI

    if not image_urls:
        raise ValueError("没有可用于分析的图片 URL。")
    key, base, _ = _openai_settings()
    if not key:
        raise RuntimeError("未配置 OPENAI_API_KEY。")
    read_timeout = float(getattr(settings, "OPENAI_READ_TIMEOUT", 1200))
    client = OpenAI(
        base_url=base,
        api_key=key,
        timeout=read_timeout,
    )
    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": "请详细分析以下商品图片的每一处细节，按系统指令生成图需简报。"},
    ]
    detail = "low" if len(image_urls) > 6 else "auto"
    for url in image_urls:
        user_content.append({"type": "image_url", "image_url": {"url": url, "detail": detail}})

    resp = client.chat.completions.create(
        model=_vision_model(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_tokens=max_tokens,
        temperature=0.3,
    )
    raw = (resp.choices[0].message.content or "").strip()
    print(raw)
    parsed = _extract_json_object(raw)
    if not parsed:
        raise ValueError(f"模型返回无法解析为 JSON：{raw[:400]}")
    return parsed


def _format_value(val: Any, indent: int = 0) -> list[str]:
    pad = "  " * indent
    lines: list[str] = []
    if val is None:
        return lines
    if isinstance(val, str):
        s = val.strip()
        if s:
            lines.append(f"{pad}{s}")
        return lines
    if isinstance(val, (int, float, bool)):
        lines.append(f"{pad}{val}")
        return lines
    if isinstance(val, list):
        for i, x in enumerate(val, 1):
            if isinstance(x, dict):
                lines.append(f"{pad}{i}.")
                lines.extend(_format_value(x, indent + 1))
            else:
                xs = str(x).strip()
                if xs:
                    lines.append(f"{pad}- {xs}")
        return lines
    if isinstance(val, dict):
        for k, v in val.items():
            if v in (None, "", [], {}):
                continue
            label = str(k).replace("_", " ")
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}【{label}】")
                lines.extend(_format_value(v, indent + 1))
            else:
                vs = str(v).strip()
                if vs:
                    lines.append(f"{pad}{label}：{vs}")
        return lines
    lines.append(f"{pad}{val}")
    return lines


def format_main_image_need_text(data: dict[str, Any]) -> str:
    report = data.get("report") if isinstance(data.get("report"), dict) else data
    if not isinstance(report, dict):
        return json.dumps(data, ensure_ascii=False, indent=2)
    lines: list[str] = ["【亚马逊主图与副图设计需求】", ""]
    for key, label in _MAIN_IMAGE_SECTION_LABELS.items():
        block = report.get(key)
        if not block:
            continue
        lines.append(f"## {label}")
        lines.extend(_format_value(block))
        lines.append("")
    return "\n".join(lines).strip()


def format_aplus_image_need_text(data: dict[str, Any]) -> str:
    modules = data.get("modules")
    if not isinstance(modules, list):
        return json.dumps(data, ensure_ascii=False, indent=2)
    lines: list[str] = ["【亚马逊 A+ 模块设计需求】", ""]
    for i, mod in enumerate(modules, 1):
        if not isinstance(mod, dict):
            continue
        mid = mod.get("module_id") or f"module_{i}"
        mtype = mod.get("module_type") or ""
        lines.append(f"## 模块 {i} · {mid}" + (f"（{mtype}）" if mtype else ""))
        lines.extend(_format_value(mod))
        lines.append("")
    return "\n".join(lines).strip()


def analyze_main_image_need(image_urls: list[str]) -> tuple[dict[str, Any], str]:
    prompt = _read_script_system_prompt("chat_gpt_main_image_txt.py")
    data = _call_vision_json(prompt, image_urls, max_tokens=int(getattr(settings, "AI_IMAGE_NEED_MAX_TOKENS_MAIN", 3500)))
    return data, format_main_image_need_text(data)


def analyze_aplus_image_need(image_urls: list[str]) -> tuple[dict[str, Any], str]:
    prompt = _read_script_system_prompt("chat_gpt_aplus_image_txt.py")
    data = _call_vision_json(prompt, image_urls, max_tokens=int(getattr(settings, "AI_IMAGE_NEED_MAX_TOKENS_APLUS", 4500)))
    return data, format_aplus_image_need_text(data)


def generate_image_need_for_asin(
    orig: OriginalAsinData,
    kind: ImageNeedKind,
) -> dict[str, Any]:
    section = "main" if kind == "main" else "aplus"
    urls = collect_section_vision_urls(orig, section)
    if not urls:
        label = "主图-副图" if kind == "main" else "A+ 图"
        raise ValueError(f"{orig.asin} 原图列暂无{label}，请先获取或上传图片。")

    if kind == "main":
        raw_json, text = analyze_main_image_need(urls)
        field = "main_image_requirements"
    else:
        raw_json, text = analyze_aplus_image_need(urls)
        field = "aplus_image_requirements"

    setattr(orig, field, text)
    orig.save(update_fields=[field, "updated_at"])
    return {
        "asin": orig.asin,
        "kind": kind,
        "field": field,
        "text": text,
        "image_count": len(urls),
        "raw_json": raw_json,
    }
