"""Listing / 分析 / 原文本 / 数据获取相关视图。"""
from __future__ import annotations

import asyncio
import json
import logging
import operator
import zipfile
from functools import reduce
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..asin_access import (
    asin_analysis_qs_for_user,
    filter_analysis_by_uploader_id,
    filter_original_by_uploader_id,
    filter_original_by_user_id,
    get_active_users_for_assign,
    is_asin_admin,
    original_asin_qs_for_user,
    parse_uploader_filter_user_id,
    stamp_created_by_if_empty,
    stamp_created_by_on_new_rows,
    uploader_filter_context,
    user_can_access_asin,
)
from ..gpt_analysis_service import (
    extract_markdown_h3_body,
    listing_raw_to_markdown,
    record_ai_listing_generation,
    run_ai_listing_for_asin,
    run_gpt_for_asin,
)
from ..models import AiListingGenerationHistory, AsinAnalysis, AsinAnalysisLock, OriginalAsinData
from ..pagination import LIST_PAGE_SIZE, paginate, pagination_querystring
from .common import (
    ASIN_ANALYSIS_LOCK_STALE,
    _deny_asin_access,
    _originals_list_for_upper_asins,
    _parse_keywords_lines,
    _purge_stale_asin_analysis_locks,
    _redirect_with_q,
)

logger = logging.getLogger(__name__)

def analysis_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    filter_user_id = parse_uploader_filter_user_id(request.user, request)
    records = asin_analysis_qs_for_user(request.user).prefetch_related("details").order_by("-created_at")
    if filter_user_id:
        records = filter_analysis_by_uploader_id(records, filter_user_id)
    if q:
        records = records.filter(asin__icontains=q)
    page_obj = paginate(request, records)
    page_records = list(page_obj.object_list)
    asins = [item.asin for item in page_records]
    originals_list = _originals_list_for_upper_asins(asins, request.user)
    cluster_needed: set[str] = set()
    for o in originals_list:
        for ca in _normalize_asin_cluster(getattr(o, "asin_cluster", None)):
            cluster_needed.add(ca)
    by_asin_upper: dict[str, OriginalAsinData] = {
        (o.asin or "").strip().upper(): o for o in originals_list
    }
    if cluster_needed:
        for o in _originals_list_for_upper_asins(cluster_needed, request.user):
            k = (o.asin or "").strip().upper()
            if k not in by_asin_upper:
                by_asin_upper[k] = o

    compare_payload: dict[str, Any] = {}
    analysis_table_rows: list[dict[str, Any]] = []
    for item in page_records:
        o = by_asin_upper.get((item.asin or "").strip().upper())
        details_map: dict[str, Any] = {}
        for d in item.details.all():
            details_map[d.category] = {
                "label": d.get_category_display(),
                "gpt_summary": d.gpt_summary or "",
                "satisfy_condition": d.satisfy_condition or "",
            }
        diff_d = details_map.get("differentiation")
        diff_text = ((diff_d or {}).get("gpt_summary") or "").strip()
        preview = (diff_text[:100] + "…") if len(diff_text) > 100 else (diff_text or "（暂无差异化分析）")
        analysis_table_rows.append({
            "analysis": item,
            "differentiation_preview": preview,
            "orig": o,
        })

        voc_bundle: Any = None
        if o:
            voc_bundle = _build_voc_bundle(o, by_asin_upper)
        kw: list[Any] = []
        if o and o.keywords is not None:
            if isinstance(o.keywords, list):
                kw = o.keywords
            else:
                kw = list(o.keywords) if o.keywords else []
        ask: Any = o.ask_rufus if o and o.ask_rufus is not None else {}
        if not isinstance(ask, dict):
            ask = {}
        compare_payload[item.asin] = {
            "keywords": kw,
            "ask_rufus": ask,
            "voc_bundle": voc_bundle,
            "listing": item.listing or "",
            "details": details_map,
        }
    context = {
        "analysis_table_rows": analysis_table_rows,
        "search_q": q,
        "compare_payload": compare_payload,
        "page_obj": page_obj,
        "pagination_qs": pagination_querystring(request),
        **uploader_filter_context(request.user, request),
    }
    return render(request, "analysis_list.html", context)


@login_required
def listing_panel(request: HttpRequest) -> HttpResponse:
    """
    Listing 面板：汇总原文本（关键词、Rufus）与差异化分析中的 VOC定位、差评改进方向及生成 Listing。
    """
    q = (request.GET.get("q") or "").strip()
    filter_user_id = parse_uploader_filter_user_id(request.user, request)
    qs = asin_analysis_qs_for_user(request.user).prefetch_related("details").order_by("-created_at")
    if filter_user_id:
        qs = filter_analysis_by_uploader_id(qs, filter_user_id)
    if q:
        qs = qs.filter(asin__icontains=q)
    page_obj = paginate(request, qs)
    rows: list[dict[str, Any]] = []
    for a in page_obj.object_list:
        orig = (
            original_asin_qs_for_user(request.user)
            .select_related("created_by")
            .filter(asin__iexact=a.asin)
            .first()
        )
        dm = {d.category: d for d in a.details.all()}
        diff = dm.get("differentiation")
        diff_md = (diff.gpt_summary or "").strip() if diff else ""
        kw_raw = getattr(orig, "keywords", None) if orig else None
        keywords_list: list[str] = []
        if isinstance(kw_raw, list):
            for x in kw_raw:
                if isinstance(x, (list, tuple)) and x:
                    keywords_list.append(str(x[0]).strip())
                elif x is not None and str(x).strip():
                    keywords_list.append(str(x).strip())
        elif isinstance(kw_raw, str) and kw_raw.strip():
            keywords_list = [kw_raw.strip()]
        ar = getattr(orig, "ask_rufus", None) if orig else None
        if not isinstance(ar, dict):
            ar = {}
        listing_raw = a.listing or ""
        rows.append(
            {
                "asin": a.asin,
                "orig": orig,
                "keywords_list": keywords_list,
                "ask_rufus": ar,
                "listing": listing_raw,
                "listing_md": listing_raw_to_markdown(listing_raw),
                "voc_positioning": extract_markdown_h3_body(
                    diff_md, ["VOC定位", "VOC 定位", "voc定位"]
                ),
                "negative_direction": extract_markdown_h3_body(
                    diff_md, ["差评改进方向", "差评改进", "差评与改进方向"]
                ),
            }
        )
    return render(
        request,
        "listing_panel.html",
        {
            "rows": rows,
            "search_q": q,
            "page_obj": page_obj,
            "pagination_qs": pagination_querystring(request),
            **uploader_filter_context(request.user, request),
        },
    )


@login_required
def ai_listing(request: HttpRequest) -> HttpResponse:
    """基于原文本与分析概览摘要生成 Listing，写入 AsinAnalysis.listing。"""
    _purge_stale_asin_analysis_locks()

    def _acquire_lock(asin: str) -> bool:
        stale_before = timezone.now() - ASIN_ANALYSIS_LOCK_STALE
        AsinAnalysisLock.objects.filter(asin=asin, started_at__lt=stale_before).delete()
        try:
            AsinAnalysisLock.objects.create(asin=asin, started_by=request.user)
            return True
        except IntegrityError:
            return False

    def _release_lock(asin: str) -> None:
        AsinAnalysisLock.objects.filter(asin=asin).delete()

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        q_post = (request.POST.get("q") or "").strip()
        next_params: dict[str, str] = {}
        if q_post:
            next_params["q"] = q_post
        pres = (request.POST.get("preserve_query") or "").strip()
        if pres:
            next_url = f"{reverse('ai_listing')}?{pres}"
        else:
            page_post = (request.POST.get("page") or "").strip()
            if page_post.isdigit() and int(page_post) > 1:
                next_params["page"] = page_post
            next_url = reverse("ai_listing")
            if next_params:
                next_url = f"{next_url}?{urlencode(next_params)}"

        if action == "save_listing_inputs":
            asin = (request.POST.get("asin") or "").strip()
            if not asin:
                messages.error(request, "缺少 ASIN。")
                return redirect(next_url)
            if not user_can_access_asin(request.user, asin):
                messages.error(request, f"无权操作 ASIN：{asin}")
                return redirect(next_url)
            orig, _ = OriginalAsinData.objects.get_or_create(asin=asin)
            stamp_created_by_if_empty(orig, request.user)
            kw_text = request.POST.get("keywords_text", "")
            orig.keywords = _parse_keywords_lines(kw_text)
            ruf_raw = (request.POST.get("ask_rufus_json") or "").strip()
            if ruf_raw:
                try:
                    parsed = json.loads(ruf_raw)
                    if not isinstance(parsed, dict):
                        raise ValueError("Rufus 须为 JSON 对象")
                    orig.ask_rufus = parsed
                except (json.JSONDecodeError, ValueError) as e:
                    messages.error(request, f"Ask Rufus JSON 无效：{e}")
                    return redirect(next_url)
            else:
                orig.ask_rufus = {}
            orig.voc_positioning_edited = (request.POST.get("voc_positioning") or "").strip()
            orig.negative_direction_edited = (request.POST.get("negative_direction") or "").strip()
            orig.cluster_suggestion_edited = (request.POST.get("cluster_suggestion") or "").strip()
            ms = (request.POST.get("material_supplement") or "").strip()
            orig.material_supplement = ms if ms else "待定"
            orig.save(
                update_fields=[
                    "keywords",
                    "ask_rufus",
                    "voc_positioning_edited",
                    "negative_direction_edited",
                    "cluster_suggestion_edited",
                    "material_supplement",
                    "updated_at",
                ]
            )
            messages.success(request, f"{asin} 已保存。")
            return redirect(next_url)

        asin = (request.POST.get("asin") or "").strip()
        user_notes = (request.POST.get("user_notes") or "").strip()
        if not asin:
            messages.error(request, "请选择 ASIN。")
            return redirect(next_url)
        if not user_can_access_asin(request.user, asin):
            messages.error(request, f"无权操作 ASIN：{asin}")
            return redirect(next_url)
        if not _acquire_lock(asin):
            messages.warning(request, f"{asin} 正在处理中，请勿重复提交。")
            return redirect(next_url)
        try:
            # 生成前写入「材质与补充」：表格 hidden 同步行内框 + 底部「补充说明」一并落库（原逻辑未保存 user_notes）
            orig_gen, _ = OriginalAsinData.objects.get_or_create(asin=asin)
            stamp_created_by_if_empty(orig_gen, request.user)
            ms_row = (request.POST.get("material_supplement") or "").strip()
            notes = (user_notes or "").strip()
            base = ms_row if ms_row and ms_row != "待定" else ""
            if notes:
                if base:
                    if base.strip() == notes.strip() or base.endswith("\n\n" + notes):
                        to_save = base
                    else:
                        to_save = f"{base}\n\n{notes}".strip()
                else:
                    to_save = notes
            else:
                to_save = base if base else "待定"
            orig_gen.material_supplement = to_save
            orig_gen.save(update_fields=["material_supplement", "updated_at"])
            # 补充说明已并入 material_supplement，避免提示词中「材质与补充」与「用户补充说明」重复两段
            analysis = run_ai_listing_for_asin(asin, "")
            record_ai_listing_generation(analysis.asin, analysis.listing or "", request.user)
            messages.success(request, f"{asin} Listing 已生成并写入差异化分析概览。")
        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f"生成失败：{e}")
        finally:
            _release_lock(asin)
        return redirect(next_url)

    q = (request.GET.get("q") or "").strip()
    filter_user_id = parse_uploader_filter_user_id(request.user, request)
    qs = asin_analysis_qs_for_user(request.user).prefetch_related("details").order_by("-created_at")
    if filter_user_id:
        qs = filter_analysis_by_uploader_id(qs, filter_user_id)
    if q:
        qs = qs.filter(asin__icontains=q)

    page_obj = paginate(request, qs)

    rows: List[Dict[str, Any]] = []
    for a in page_obj.object_list:
        orig = (
            original_asin_qs_for_user(request.user)
            .select_related("created_by")
            .filter(asin__iexact=a.asin)
            .first()
        )
        dm = {d.category: d for d in a.details.all()}
        diff = dm.get("differentiation")
        cluster = dm.get("cluster")
        diff_md = (diff.gpt_summary or "").strip() if diff else ""
        cluster_md = (cluster.gpt_summary or "").strip() if cluster else ""

        keywords_list: list[str] = []
        if orig:
            kw_raw = getattr(orig, "keywords", None)
            if isinstance(kw_raw, list):
                for x in kw_raw:
                    if isinstance(x, (list, tuple)) and x:
                        keywords_list.append(str(x[0]).strip())
                    elif x is not None and str(x).strip():
                        keywords_list.append(str(x).strip())
            elif isinstance(kw_raw, str) and kw_raw.strip():
                keywords_list = [kw_raw.strip()]
        ar = getattr(orig, "ask_rufus", None) if orig else None
        if not isinstance(ar, dict):
            ar = {}

        voc_ext = extract_markdown_h3_body(diff_md, ["VOC定位", "VOC 定位", "voc定位"])
        neg_ext = extract_markdown_h3_body(
            diff_md, ["差评改进方向", "差评改进", "差评与改进方向"]
        )
        sug_ext = extract_markdown_h3_body(
            cluster_md,
            ["建议和总结", "建议与总结", "总结与建议", "集群建议与总结"],
        )
        voc_e = (getattr(orig, "voc_positioning_edited", None) or "").strip() if orig else ""
        neg_e = (getattr(orig, "negative_direction_edited", None) or "").strip() if orig else ""
        sug_e = (getattr(orig, "cluster_suggestion_edited", None) or "").strip() if orig else ""
        ms = (getattr(orig, "material_supplement", None) or "").strip() if orig else ""
        if not ms:
            ms = "待定"

        try:
            ask_rufus_json = json.dumps(ar, ensure_ascii=False, indent=2) if ar else "{}"
        except TypeError:
            ask_rufus_json = "{}"

        rows.append(
            {
                "asin": a.asin,
                "orig": orig,
                "keywords_list": keywords_list,
                "keywords_text": "\n".join(keywords_list),
                "ask_rufus": ar,
                "ask_rufus_json": ask_rufus_json,
                "voc_positioning": voc_e if voc_e else voc_ext,
                "negative_direction": neg_e if neg_e else neg_ext,
                "cluster_suggestion": sug_e if sug_e else sug_ext,
                "material_supplement": ms,
            }
        )

    return render(
        request,
        "ai_listing.html",
        {
            "rows": rows,
            "search_q": q,
            "page_obj": page_obj,
            "pagination_qs": pagination_querystring(request),
            **uploader_filter_context(request.user, request),
        },
    )


@login_required
@require_GET
def ai_listing_history_json(request: HttpRequest, asin: str) -> JsonResponse:
    """返回某 ASIN 在 AI-Listing 页每次成功「生成 Listing」的快照列表（JSON）。"""
    key = (asin or "").strip()
    if not key:
        return JsonResponse({"ok": False, "error": "缺少 ASIN"}, status=400)
    if not user_can_access_asin(request.user, key):
        return JsonResponse({"ok": False, "error": "无权查看该 ASIN 的历史"}, status=403)
    qs = (
        AiListingGenerationHistory.objects.filter(asin__iexact=key)
        .select_related("generated_by")
        .order_by("-created_at")[:200]
    )
    items: list[dict[str, Any]] = []
    canon = key.upper()
    for h in qs:
        if not items:
            canon = h.asin
        uname = (h.generated_by_username or "").strip()
        if not uname and h.generated_by_id:
            u = h.generated_by
            if u is not None:
                uname = u.get_username()
        items.append(
            {
                "id": h.pk,
                "created_at": timezone.localtime(h.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                "generated_by_username": uname or "（未知）",
                "keywords": h.keywords,
                "ask_rufus": h.ask_rufus,
                "voc_positioning": h.voc_positioning,
                "negative_direction": h.negative_direction,
                "cluster_suggestion": h.cluster_suggestion,
                "material_supplement": h.material_supplement,
                "listing": h.listing,
            }
        )
    return JsonResponse({"ok": True, "asin": canon, "items": items})


_AI_IMAGE_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
def _guess_asin_and_payload(filename: str, payload: Any) -> Tuple[Optional[str], Any]:
    """
    支持两种常见 VOC JSON 结构：
    1) 文件名: B0F6MTPQVG_VOC.json，内容为 voc 数据（dict/list/str 均可）
    2) 内容为 { "<ASIN>": {...} } 单键包裹（ASIN 为 10 位字母数字，含 B01/B09 等，不限于 B0 开头）
    """
    asin: Optional[str] = None
    if filename:
        base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if "_VOC" in base:
            asin = base.split("_VOC", 1)[0].strip()
    if isinstance(payload, dict) and len(payload) == 1:
        k = next(iter(payload.keys()))
        if isinstance(k, str) and _asin_10_from_stem(k):
            asin = asin or _asin_10_from_stem(k) or k.strip()
            payload = payload[k]
    return asin, payload


def _asin_10_from_stem(stem: str) -> Optional[str]:
    """10 位字母数字 ASIN（大写）。"""
    x = (stem or "").strip().upper()
    if len(x) == 10 and x.isalnum():
        return x
    return None


def _cluster_entries_from_payload(payload: Any, leaf_basename: str = "") -> list[tuple[str, Any]]:
    """
    从 JSON 解析 (集群 ASIN, VOC) 列表。
    支持：多键均为 ASIN 的 dict；单键 ASIN 包裹；或裸 VOC（dict/list/str）时从 leaf_basename 的 *_VOC 解析集群 ASIN。
    """
    leaf = (leaf_basename or "").rsplit("/", 1)[-1].strip()

    def _nonempty_payload(v: Any) -> bool:
        if v is None:
            return False
        if isinstance(v, str):
            return bool(v.strip())
        if isinstance(v, dict):
            return len(v) > 0
        if isinstance(v, list):
            return len(v) > 0
        return True

    cluster_from_leaf: Optional[str] = None
    if leaf:
        stem = leaf.rsplit(".", 1)[0].strip()
        for sep in ("_VOC", "_voc"):
            if sep in stem:
                head = stem.split(sep, 1)[0].strip()
                cluster_from_leaf = _asin_10_from_stem(head)
                break

    if isinstance(payload, dict) and len(payload) > 0:
        all_keys_are_asin = all(
            isinstance(k, str) and _asin_10_from_stem(k) is not None for k in payload.keys()
        )
        if len(payload) > 1 and all_keys_are_asin:
            return [(str(k).strip().upper(), payload[k]) for k in payload.keys()]
        a, pl = _guess_asin_and_payload("", payload)
        if a and _asin_10_from_stem(a):
            return [(a.strip().upper(), pl)]
        if cluster_from_leaf and _nonempty_payload(payload):
            return [(cluster_from_leaf, payload)]
        return []

    if cluster_from_leaf and _nonempty_payload(payload):
        return [(cluster_from_leaf, payload)]
    return []


def _upload_path_parts(name: str) -> list[str]:
    norm = (name or "").replace("\\", "/").strip()
    return [p for p in norm.split("/") if p]


def _is_voc_cluster_leaf(leaf: str) -> bool:
    """簇内 VOC 文件：<任意>_VOC.json（大小写不敏感）。"""
    if not leaf or not leaf.lower().endswith(".json"):
        return False
    stem = leaf.rsplit(".", 1)[0]
    return "_voc" in stem.lower()


def _target_asin_from_cluster_folder_path(parts: list[str]) -> Optional[str]:
    """路径 …/对标ASIN/xxx.json 时，取倒数第二段为对标。"""
    if len(parts) < 2:
        return None
    parent = parts[-2].rsplit(".", 1)[0].strip()
    return _asin_10_from_stem(parent)


def _leaf_stem_from_upload_fname(name: str) -> str:
    norm = (name or "").replace("\\", "/").strip()
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return ""
    return parts[-1].rsplit(".", 1)[0].strip()


def _cluster_asin_hint_from_voc_filename(name: str) -> Optional[str]:
    """从 xxx_VOC.json 文件名得到 _VOC 前的 10 位 ASIN（通常为集群 ASIN，用于反查对标行）。"""
    stem = _leaf_stem_from_upload_fname(name)
    for sep in ("_VOC", "_voc"):
        if sep in stem:
            head = stem.split(sep, 1)[0].strip()
            return _asin_10_from_stem(head)
    return None


def _find_benchmark_row_containing_cluster_asin(
    cluster_au: str, user: Optional[Any] = None
) -> Optional[OriginalAsinData]:
    """查找 asin_cluster 中包含该集群 ASIN 的对标行（OriginalAsinData）。"""
    cu = (cluster_au or "").strip().upper()
    if len(cu) != 10 or not cu.isalnum():
        return None
    base = (
        original_asin_qs_for_user(user)
        if user is not None and user.is_authenticated
        else OriginalAsinData.objects.all()
    )
    for o in base.iterator():
        raw = getattr(o, "asin_cluster", None)
        if raw is None:
            continue
        items = raw if isinstance(raw, list) else [raw]
        for x in items:
            if not isinstance(x, str):
                continue
            a = x.strip().upper()
            if len(a) == 10 and a.isalnum() and a == cu:
                return o
    return None


@login_required
def original_text_list(request: HttpRequest) -> HttpResponse:
    """
    原文本页的数据源以 OriginalAsinData 为主（导入即展示），
    同时把 AsinAnalysis 里存在但 OriginalAsinData 缺失的 asin 补齐出来，避免“分析页有、原文本页没有”。
    """
    if is_asin_admin(request.user):
        analysis_asins = list(AsinAnalysis.objects.values_list("asin", flat=True))
        if analysis_asins:
            OriginalAsinData.objects.bulk_create(
                [OriginalAsinData(asin=a) for a in analysis_asins],
                ignore_conflicts=True,
            )
    q = (request.GET.get("q") or "").strip()
    filter_user_id = parse_uploader_filter_user_id(request.user, request)
    qs = (
        original_asin_qs_for_user(request.user)
        .select_related("created_by", "assigned_to")
        .order_by("-updated_at", "-created_at")
    )
    if filter_user_id:
        qs = filter_original_by_uploader_id(qs, filter_user_id)
    if q:
        qs = qs.filter(asin__icontains=q)
    page_obj = paginate(request, qs)
    rows_objs = list(page_obj.object_list)
    cluster_needed: set[str] = set()
    for o in rows_objs:
        for ca in _normalize_asin_cluster(getattr(o, "asin_cluster", None)):
            cluster_needed.add(ca)
    by_asin_upper: dict[str, OriginalAsinData] = {
        (o.asin or "").strip().upper(): o for o in rows_objs
    }
    if cluster_needed:
        for o in _originals_list_for_upper_asins(cluster_needed, request.user):
            k = (o.asin or "").strip().upper()
            if k not in by_asin_upper:
                by_asin_upper[k] = o
    rows = [
        {"asin": o.asin, "obj": o, "voc_bundle": _build_voc_bundle(o, by_asin_upper)}
        for o in rows_objs
    ]
    ctx: dict[str, Any] = {
        "rows": rows,
        "search_q": q,
        "page_obj": page_obj,
        "pagination_qs": pagination_querystring(request),
        "is_asin_admin": is_asin_admin(request.user),
    }
    if request.user.is_superuser:
        ctx.update(uploader_filter_context(request.user, request))
        ctx["assignable_users"] = ctx.get("uploader_filter_users") or list(get_active_users_for_assign())
    return render(request, "original_text_list.html", ctx)


@login_required
def import_voc(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect(reverse("original_text_list"))

    files = request.FILES.getlist("files")
    if not files:
        messages.error(request, "未选择任何文件。")
        return redirect(reverse("original_text_list"))

    ok, fail = 0, 0
    for f in files:
        try:
            raw = f.read()
            # 允许带 BOM 的 JSON
            text = raw.decode("utf-8-sig")
            payload = json.loads(text)
            asin, voc_payload = _guess_asin_and_payload(getattr(f, "name", ""), payload)
            if not asin:
                raise ValueError("无法从文件名或 JSON 结构推断 ASIN（期望 <ASIN>_VOC.json 或 {ASIN: {...}}）。")
            au = _asin_10_from_stem(asin)
            if au:
                asin = au
            if isinstance(voc_payload, dict):
                voc_payload = _normalize_voc_for_storage(voc_payload)
            obj, created = OriginalAsinData.objects.update_or_create(
                asin=asin,
                defaults={"voc": voc_payload},
            )
            if not created and not user_can_access_asin(request.user, asin):
                raise ValueError("无权更新该 ASIN（非您上传且未分配给您）。")
            stamp_created_by_if_empty(obj, request.user)
            ok += 1
        except Exception as e:
            fail += 1
            messages.error(request, f"导入失败：{getattr(f, 'name', '')}，原因：{e}")

    if ok:
        messages.success(request, f"导入成功：{ok} 个文件。")
    if fail and not ok:
        messages.error(request, f"全部失败：{fail} 个文件。")
    return _redirect_with_q("original_text_list", request)


@login_required
def import_voc_cluster(request: HttpRequest) -> HttpResponse:
    """
    VOC 集群：仅处理「选择文件夹」上传（webkitdirectory）中的 *_VOC.json。
    推荐路径：…/对标ASIN/<集群ASIN>_VOC.json（父文件夹名 = 对标）；
    JSON 可为裸 VOC（与常规 VOC 导出一致），集群 ASIN 由文件名 _VOC 前 10 位解析。
    若仅选了对标文件夹本身导致路径无父级 ASIN，则依赖「ASIN 集群」反查对标行。
    """
    if request.method != "POST":
        return redirect(reverse("original_text_list"))

    files = request.FILES.getlist("cluster_files")
    if not files:
        messages.error(request, "未选择文件夹。请使用「选择 VOC 集群文件夹」。")
        return _redirect_with_q("original_text_list", request)

    ok_files = 0
    fail = 0
    touched_targets: set[str] = set()

    for f in files:
        fname = getattr(f, "name", "") or ""
        parts = _upload_path_parts(fname)
        leaf = parts[-1] if parts else ""

        if not leaf or not _is_voc_cluster_leaf(leaf):
            continue

        try:
            target_u = _target_asin_from_cluster_folder_path(parts)
            obj: Optional[OriginalAsinData] = None
            if target_u:
                obj = original_asin_qs_for_user(request.user).filter(asin__iexact=target_u).first()

            voc_stem_cluster_hint = _cluster_asin_hint_from_voc_filename(fname)
            if not obj and voc_stem_cluster_hint:
                obj = _find_benchmark_row_containing_cluster_asin(voc_stem_cluster_hint, request.user)

            if not obj:
                parts_hint = " / ".join(parts) if parts else fname
                msg = "未找到可写入的对标行。"
                if target_u:
                    msg += f" 路径中解析的对标「{target_u}」无原文本记录。"
                else:
                    msg += " 当前文件相对路径无「父文件夹=对标」段（常见于只选了对标文件夹本身）。"
                if voc_stem_cluster_hint:
                    msg += f" 已从文件名推断集群 ASIN「{voc_stem_cluster_hint}」，但在各行的「ASIN 集群」中未找到包含该 ASIN 的对标行。"
                msg += (
                    " 建议：选中**对标文件夹的上一级目录**再上传，使路径形如「对标ASIN/B07…_VOC.json」；"
                    "或先在目标对标行的「ASIN 集群」中加入该集群 ASIN。"
                    f"（路径：{parts_hint}）"
                )
                raise ValueError(msg)

            row_u = (obj.asin or "").strip().upper()
            raw = f.read()
            text = raw.decode("utf-8-sig")
            payload = json.loads(text)
            entries = _cluster_entries_from_payload(payload, leaf)
            if not entries:
                raise ValueError(
                    f"无法从 JSON 与文件名「{leaf}」解析出集群 VOC；请确认文件为有效 JSON 且非空。"
                )
            current_vc = dict(_normalized_voc_cluster_dict(obj))
            for imp_u, voc_payload in entries:
                if imp_u == row_u:
                    raise ValueError(
                        f"集群 ASIN「{imp_u}」与对标 ASIN 相同；对标主 VOC 请用「上传」。"
                    )
                if isinstance(voc_payload, dict):
                    voc_payload = _normalize_voc_for_storage(voc_payload)
                current_vc[imp_u] = voc_payload
            if not user_can_access_asin(request.user, obj.asin):
                raise ValueError("无权更新该对标 ASIN（非您上传且未分配给您）。")
            obj.voc_cluster = current_vc
            obj.save(update_fields=["voc_cluster"])
            touched_targets.add(obj.asin)
            ok_files += 1
        except Exception as e:
            fail += 1
            messages.error(request, f"VOC 集群导入失败：{fname}，原因：{e}")

    if ok_files:
        nt = len(touched_targets)
        messages.success(
            request,
            f"VOC 集群已更新：成功 {ok_files} 个 *_VOC.json，涉及 {nt} 个对标 ASIN。",
        )
    elif fail:
        messages.error(request, f"VOC 集群全部失败：{fail} 个文件。")
    else:
        messages.error(
            request,
            "所选目录内没有符合命名的 *_VOC.json 文件。请确认使用「选择文件夹」，且文件名为 <集群ASIN>_VOC.json。",
        )
    return _redirect_with_q("original_text_list", request)


@login_required
def refresh_ask_rufus(request: HttpRequest, asin: str) -> HttpResponse:
    if request.method != "POST":
        return _redirect_with_q("original_text_list", request)
    if not user_can_access_asin(request.user, asin):
        return _deny_asin_access(request, asin)
    try:
        from script.get_ask_rufus import main_ask_rufus

        result = asyncio.run(main_ask_rufus([asin]))
        obj, created = OriginalAsinData.objects.update_or_create(
            asin=asin, defaults={"ask_rufus": result.get(asin, {})}
        )
        stamp_created_by_if_empty(obj, request.user)
        messages.success(request, f"{asin} Ask Rufus 已更新。")
    except Exception as e:
        messages.error(request, f"{asin} Ask Rufus 更新失败：{e}")
    return _redirect_with_q("original_text_list", request)


@login_required
def refresh_keywords(request: HttpRequest, asin: str) -> HttpResponse:
    if request.method != "POST":
        return _redirect_with_q("original_text_list", request)
    if not user_can_access_asin(request.user, asin):
        return _deny_asin_access(request, asin)
    try:
        from script.get_h10_keyword import h10_main

        kw_map = asyncio.run(h10_main([asin]))
        obj, created = OriginalAsinData.objects.update_or_create(
            asin=asin, defaults={"keywords": kw_map.get(asin, [])}
        )
        stamp_created_by_if_empty(obj, request.user)
        messages.success(request, f"{asin} 关键词已更新。")
    except Exception as e:
        messages.error(request, f"{asin} 关键词更新失败：{e}")
    return _redirect_with_q("original_text_list", request)


@login_required
def refresh_asin_cluster(request: HttpRequest, asin: str) -> HttpResponse:
    if request.method != "POST":
        return _redirect_with_q("original_text_list", request)
    if not user_can_access_asin(request.user, asin):
        return _deny_asin_access(request, asin)
    try:
        from script.get_asin import get_asins

        o = original_asin_qs_for_user(request.user).filter(asin__iexact=asin).first()
        if not o:
            messages.error(request, f"未找到 ASIN：{asin}")
            return _redirect_with_q("original_text_list", request)
        kw = _first_cluster_keyword(o.keywords)
        if not kw:
            messages.error(
                request,
                f"{asin} 无法获取搜索词：请先「刷新关键词」，且关键词需为非空列表（或二维列表的首个词）。",
            )
            return _redirect_with_q("original_text_list", request)
        # print(kw,'23333')
        found = get_asins(kw, verbose=False)
        # print(found,'222122')
        row_upper = (asin or "").strip().upper()
        cleaned: list[str] = []
        seen: set[str] = set()
        for a in found:
            if not isinstance(a, str):
                continue
            x = a.strip().upper()
            if len(x) != 10 or not x.isalnum() or x == row_upper:
                continue
            if x not in seen:
                seen.add(x)
                cleaned.append(x)
        max_n = 80
        cleaned = cleaned[:max_n]
        obj, _ = OriginalAsinData.objects.update_or_create(asin=asin, defaults={"asin_cluster": cleaned})
        stamp_created_by_if_empty(obj, request.user)
        messages.success(request, f"{asin} ASIN 集群已更新（搜索词：{kw}，共 {len(cleaned)} 个）。")
    except Exception as e:
        messages.error(request, f"{asin} ASIN 集群更新失败：{e}")
    return _redirect_with_q("original_text_list", request)


@login_required
def save_asin_cluster(request: HttpRequest, asin: str) -> HttpResponse:
    if request.method != "POST":
        return _redirect_with_q("original_text_list", request)
    if not user_can_access_asin(request.user, asin):
        return _deny_asin_access(request, asin)
    raw = (request.POST.get("cluster_raw") or "").strip()
    parsed = _parse_cluster_from_text(raw)
    row_upper = (asin or "").strip().upper()
    parsed = [a for a in parsed if a != row_upper]
    obj, _ = OriginalAsinData.objects.update_or_create(asin=asin, defaults={"asin_cluster": parsed})
    stamp_created_by_if_empty(obj, request.user)
    messages.success(request, f"{asin} ASIN 集群已保存（{len(parsed)} 个）。")
    return _redirect_with_q("original_text_list", request)


@login_required
@user_passes_test(lambda u: u.is_superuser)
def assign_original_asin(request: HttpRequest, asin: str) -> HttpResponse:
    if request.method != "POST":
        return _redirect_with_q("original_text_list", request)
    obj = OriginalAsinData.objects.filter(asin__iexact=(asin or "").strip()).first()
    if not obj:
        messages.error(request, f"未找到 ASIN：{asin}")
        return _redirect_with_q("original_text_list", request)
    raw_uid = (request.POST.get("assigned_to") or "").strip()
    if not raw_uid:
        obj.assigned_to = None
        obj.save(update_fields=["assigned_to", "updated_at"])
        messages.success(request, f"{obj.asin} 已取消分配。")
    else:
        try:
            target = User.objects.get(pk=int(raw_uid), is_active=True)
        except (ValueError, User.DoesNotExist):
            messages.error(request, "请选择有效的用户。")
            return _redirect_with_q("original_text_list", request)
        obj.assigned_to = target
        obj.save(update_fields=["assigned_to", "updated_at"])
        messages.success(request, f"{obj.asin} 已分配给 {target.username}。")
    return _redirect_with_q("original_text_list", request)


@login_required
def delete_original_text_row(request: HttpRequest, asin: str) -> HttpResponse:
    if request.method != "POST":
        return _redirect_with_q("original_text_list", request)
    if not user_can_access_asin(request.user, asin):
        return _deny_asin_access(request, asin)
    OriginalAsinData.objects.filter(asin__iexact=(asin or "").strip()).delete()
    messages.success(request, f"已删除原文本记录：{asin}")
    return _redirect_with_q("original_text_list", request)


@login_required
def batch_delete_original_text(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return _redirect_with_q("original_text_list", request)
    asins = request.POST.getlist("asins")
    if not asins:
        messages.error(request, "请至少勾选一条记录。")
        return _redirect_with_q("original_text_list", request)
    allowed = [a for a in asins if user_can_access_asin(request.user, a)]
    if not allowed:
        messages.error(request, "所选记录均无权删除。")
        return _redirect_with_q("original_text_list", request)
    q_del = Q()
    for a in allowed:
        q_del |= Q(asin__iexact=(a or "").strip())
    OriginalAsinData.objects.filter(q_del).delete()
    messages.success(request, f"已批量删除 {len(allowed)} 条原文本记录。")
    return _redirect_with_q("original_text_list", request)


@login_required
def delete_analysis_row(request: HttpRequest, asin: str) -> HttpResponse:
    if request.method != "POST":
        return _redirect_with_q("analysis_list", request)
    if not user_can_access_asin(request.user, asin):
        messages.error(request, f"无权删除 ASIN：{asin}")
        return _redirect_with_q("analysis_list", request)
    asin_analysis_qs_for_user(request.user).filter(asin__iexact=(asin or "").strip()).delete()
    messages.success(request, f"已删除分析记录：{asin}")
    if (request.POST.get("redirect_to") or "").strip() == "listing_panel":
        return _redirect_with_q("listing_panel", request)
    return _redirect_with_q("analysis_list", request)


@login_required
def batch_delete_analysis(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return _redirect_with_q("analysis_list", request)
    asins = request.POST.getlist("asins")
    if not asins:
        messages.error(request, "请至少勾选一条记录。")
        if (request.POST.get("redirect_to") or "").strip() == "listing_panel":
            return _redirect_with_q("listing_panel", request)
        return _redirect_with_q("analysis_list", request)
    allowed = [a for a in asins if user_can_access_asin(request.user, a)]
    if not allowed:
        messages.error(request, "所选记录均无权删除。")
        if (request.POST.get("redirect_to") or "").strip() == "listing_panel":
            return _redirect_with_q("listing_panel", request)
        return _redirect_with_q("analysis_list", request)
    q_del = Q()
    for a in allowed:
        q_del |= Q(asin__iexact=(a or "").strip())
    asin_analysis_qs_for_user(request.user).filter(q_del).delete()
    messages.success(request, f"已批量删除 {len(allowed)} 条分析记录。")
    if (request.POST.get("redirect_to") or "").strip() == "listing_panel":
        return _redirect_with_q("listing_panel", request)
    return _redirect_with_q("analysis_list", request)


def _parse_asins(raw: str) -> list[str]:
    parts = []
    for chunk in (raw or "").replace("\r", "\n").replace(",", "\n").split("\n"):
        x = chunk.strip()
        if not x:
            continue
        parts.append(x)
    # 去重保持顺序
    seen = set()
    out: list[str] = []
    for a in parts:
        if a not in seen:
            out.append(a)
            seen.add(a)
    return out


def _first_cluster_keyword(keywords: Any) -> Optional[str]:
    """取「关键词集群」的第一个词：支持 [[词,...], ...] 或 [词, ...]。"""
    if not keywords or not isinstance(keywords, list) or len(keywords) == 0:
        return None
    first = keywords[0]
    if isinstance(first, (list, tuple)) and len(first) > 0:
        k = str(first[0]).strip()
        return k or None
    if isinstance(first, str):
        k = first.strip()
        return k or None
    return None


def _normalize_asin_cluster(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        items: list[Any] = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for x in items:
        if not isinstance(x, str):
            continue
        a = x.strip().upper()
        if len(a) == 10 and a.isalnum():
            if a not in seen:
                seen.add(a)
                out.append(a)
    return out


def _build_voc_bundle(row: OriginalAsinData, by_asin_upper: dict[str, OriginalAsinData]) -> dict[str, Any]:
    vc_map = _normalized_voc_cluster_dict(row)
    cluster: dict[str, Any] = {}
    cluster_meta: dict[str, str] = {}
    for ca in _normalize_asin_cluster(getattr(row, "asin_cluster", None)):
        row_key = (row.asin or "").strip().upper()
        if ca == row_key:
            continue
        emb = vc_map.get(ca)
        if _voc_payload_nonempty(emb):
            cluster[ca] = _normalize_voc_for_storage(emb) if isinstance(emb, dict) else emb
            cluster_meta[ca] = "voc_cluster"
            continue
        other = by_asin_upper.get(ca)
        voc_val: Any = None
        if other is not None and other.voc is not None:
            if isinstance(other.voc, (dict, list)) and other.voc:
                voc_val = other.voc
            elif isinstance(other.voc, str) and other.voc.strip():
                voc_val = other.voc
        if _voc_payload_nonempty(voc_val):
            cluster[ca] = (
                _normalize_voc_for_storage(voc_val) if isinstance(voc_val, dict) else voc_val
            )
            cluster_meta[ca] = "original_row"
        else:
            cluster[ca] = None
            cluster_meta[ca] = "empty"
    raw_target = row.voc if row.voc is not None else {}
    target_voc: Any = (
        _normalize_voc_for_storage(raw_target) if isinstance(raw_target, dict) else raw_target
    )
    return {
        "_voc_bundle": True,
        "row_asin": row.asin,
        "target": target_voc,
        "cluster": cluster,
        "cluster_meta": cluster_meta,
    }


def _normalized_voc_cluster_dict(row: OriginalAsinData) -> dict[str, Any]:
    raw = getattr(row, "voc_cluster", None) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        ku = k.strip().upper()
        if len(ku) == 10 and ku.isalnum():
            out[ku] = v
    return out


def _voc_payload_nonempty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, dict):
        return len(v) > 0
    if isinstance(v, list):
        return len(v) > 0
    return bool(v)


_VOC_SECTION_KEYS_CN = frozenset(
    {"消费者画像", "使用场景", "未被满足的需求", "好评", "差评", "购买动机"}
)
_VOC_WRAPPER_KEYS = (
    "data",
    "result",
    "voc",
    "VOC",
    "voiceOfCustomer",
    "content",
    "body",
    "detail",
    "Details",
    "payload",
    "response",
)
_VOC_EN_TO_CN: dict[str, str] = {
    "usage_scenarios": "使用场景",
    "usageScenarios": "使用场景",
    "unmet_needs": "未被满足的需求",
    "unmetNeeds": "未被满足的需求",
    "positive_reviews": "好评",
    "praise": "好评",
    "negative_reviews": "差评",
    "negativeReviews": "差评",
    "purchase_motivation": "购买动机",
    "purchaseMotives": "购买动机",
    "consumer_profile": "消费者画像",
}


def _voc_dict_has_sections(d: dict) -> bool:
    return bool(_VOC_SECTION_KEYS_CN.intersection(d.keys()))


def _unwrap_nested_voc_dict(payload: Any, depth: int = 0) -> Any:
    """剥掉 {data:{...}}、{result:{...}} 等外壳，使「使用场景」等键出现在顶层。"""
    if not isinstance(payload, dict) or depth > 8:
        return payload
    if _voc_dict_has_sections(payload):
        return payload
    for w in _VOC_WRAPPER_KEYS:
        inner = payload.get(w)
        if isinstance(inner, dict):
            got = _unwrap_nested_voc_dict(inner, depth + 1)
            if isinstance(got, dict) and _voc_dict_has_sections(got):
                return got
    if len(payload) == 1:
        inner = next(iter(payload.values()))
        if isinstance(inner, dict):
            if _voc_dict_has_sections(inner):
                return inner
            return _unwrap_nested_voc_dict(inner, depth + 1)
    return payload


def _apply_voc_en_aliases(d: dict) -> dict:
    out = dict(d)
    for en, cn in _VOC_EN_TO_CN.items():
        if cn not in out and en in out:
            out[cn] = out[en]
    return out


def _normalize_voc_for_storage(v: Any) -> Any:
    """导入与展示前统一 VOC 形状，避免外层包裹或英文键导致页面只有空模板。"""
    if not isinstance(v, dict):
        return v
    u = _unwrap_nested_voc_dict(v)
    if not isinstance(u, dict):
        return u
    return _apply_voc_en_aliases(u)


def _parse_cluster_from_text(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for chunk in (raw or "").replace("\r", "\n").replace(",", "\n").replace(";", "\n").split():
        a = chunk.strip().upper()
        if len(a) == 10 and a.isalnum():
            if a not in seen:
                seen.add(a)
                out.append(a)
    return out


@login_required
def data_fetch(request: HttpRequest) -> HttpResponse:
    """
    数据获取页面：按 ASIN 批量获取 keywords / ask_rufus，可单独或同时执行。
    写入 OriginalAsinData 后，原文本页面自动展示更新。
    """
    if request.method == "GET":
        return render(request, "data_fetch.html")

    raw_asins = request.POST.get("asins", "")
    action = (request.POST.get("action") or "").strip()
    asins = _parse_asins(raw_asins)
    if not asins:
        messages.error(request, "请输入至少 1 个 ASIN。")
        return redirect(reverse("data_fetch"))

    need_kw = action in ("keywords", "both")
    need_rufus = action in ("ask_rufus", "both")
    if not (need_kw or need_rufus):
        messages.error(request, "请选择要执行的功能。")
        return redirect(reverse("data_fetch"))

    # 先确保存在记录（导入即展示）
    OriginalAsinData.objects.bulk_create([OriginalAsinData(asin=a) for a in asins], ignore_conflicts=True)
    stamp_created_by_on_new_rows(asins, request.user)

    try:
        async def runner():
            kw_task = None
            rufus_task = None
            if need_kw:
                from script.get_h10_keyword import h10_main
                kw_task = asyncio.create_task(h10_main(asins))
            if need_rufus:
                from script.get_ask_rufus import main_ask_rufus
                rufus_task = asyncio.create_task(main_ask_rufus(asins))
            results = await asyncio.gather(
                *[t for t in (kw_task, rufus_task) if t is not None],
                return_exceptions=True
            )
            # 按创建顺序取回
            idx = 0
            kw_map = None
            rufus_map = None
            if need_kw:
                kw_map = results[idx]
                idx += 1
            if need_rufus:
                rufus_map = results[idx] if idx < len(results) else None
            return kw_map, rufus_map

        kw_map, rufus_map = asyncio.run(runner())

        if isinstance(kw_map, Exception):
            raise kw_map
        if isinstance(rufus_map, Exception):
            raise rufus_map

        # 写库
        ok_kw, ok_rufus = 0, 0
        for asin in asins:
            defaults = {}
            if need_kw and isinstance(kw_map, dict):
                defaults["keywords"] = kw_map.get(asin, [])
            if need_rufus and isinstance(rufus_map, dict):
                defaults["ask_rufus"] = rufus_map.get(asin, {})
            if defaults:
                obj, created = OriginalAsinData.objects.update_or_create(asin=asin, defaults=defaults)
                stamp_created_by_if_empty(obj, request.user)
                if "keywords" in defaults:
                    ok_kw += 1
                if "ask_rufus" in defaults:
                    ok_rufus += 1

        if need_kw:
            messages.success(request, f"关键词获取完成：{ok_kw}/{len(asins)}")
        if need_rufus:
            messages.success(request, f"Ask Rufus 获取完成：{ok_rufus}/{len(asins)}")
    except Exception as e:
        messages.error(request, f"执行失败：{e}")

    return redirect(reverse("original_text_list"))
def gpt_analysis(request: HttpRequest) -> HttpResponse:
    """基于原文本库数据调用 ChatGPT，结果写入 AsinAnalysis / AnalysisDetail（差异化分析概览）。"""
    _purge_stale_asin_analysis_locks()

    def _acquire_lock(asin: str) -> bool:
        stale_before = timezone.now() - ASIN_ANALYSIS_LOCK_STALE
        AsinAnalysisLock.objects.filter(asin=asin, started_at__lt=stale_before).delete()
        try:
            AsinAnalysisLock.objects.create(asin=asin, started_by=request.user)
            return True
        except IntegrityError:
            return False

    def _release_lock(asin: str) -> None:
        AsinAnalysisLock.objects.filter(asin=asin).delete()

    show_calculated = request.GET.get("show_calculated") == "1"
    if request.method == "POST":
        show_calculated = request.POST.get("show_calculated") == "1"
        q_post = (request.POST.get("q") or "").strip()
        pres = (request.POST.get("preserve_query") or "").strip()
        if pres:
            next_url = f"{reverse('gpt_analysis')}?{pres}"
        else:
            next_params = {}
            if show_calculated:
                next_params["show_calculated"] = "1"
            if q_post:
                next_params["q"] = q_post
            page_post = (request.POST.get("page") or "").strip()
            if page_post.isdigit() and int(page_post) > 1:
                next_params["page"] = page_post
            next_url = reverse("gpt_analysis")
            if next_params:
                next_url = f"{next_url}?{urlencode(next_params)}"

        action = (request.POST.get("action") or "").strip()
        if action == "single":
            asin = (request.POST.get("asin") or "").strip()
            if not asin:
                messages.error(request, "未指定 ASIN。")
            else:
                if not user_can_access_asin(request.user, asin):
                    messages.error(request, f"无权分析 ASIN：{asin}")
                    return redirect(next_url)
                if not _acquire_lock(asin):
                    messages.warning(request, f"{asin} 正在分析中，请勿重复点击。")
                    return redirect(next_url)
                try:
                    run_gpt_for_asin(asin)
                    messages.success(request, f"{asin} 分析完成，已写入「差异化分析概览」。")
                except OriginalAsinData.DoesNotExist:
                    messages.error(request, f"原文本库中不存在 ASIN：{asin}")
                except ValueError as e:
                    messages.error(request, str(e))
                except Exception as e:
                    messages.error(request, f"调用失败：{e}")
                finally:
                    _release_lock(asin)
            return redirect(next_url)

        if action == "batch":
            asins = [a.strip() for a in request.POST.getlist("asins") if a.strip()]
            if not asins:
                messages.error(request, "请至少勾选一个 ASIN。")
            else:
                ok_n = 0
                for asin in asins:
                    if not user_can_access_asin(request.user, asin):
                        messages.warning(request, f"{asin} 无权分析，已跳过。")
                        continue
                    if not _acquire_lock(asin):
                        messages.warning(request, f"{asin} 正在分析中，已跳过重复计算。")
                        continue
                    try:
                        run_gpt_for_asin(asin)
                        ok_n += 1
                    except ValueError as e:
                        messages.error(request, f"{asin}：{e}")
                    except OriginalAsinData.DoesNotExist:
                        messages.error(request, f"{asin}：原文本库中不存在该 ASIN。")
                    except Exception as e:
                        messages.error(request, f"{asin}：{e}")
                    finally:
                        _release_lock(asin)
                if ok_n:
                    messages.success(request, f"成功完成 {ok_n} 个 ASIN 的分析，已同步至差异化分析概览。")
            return redirect(next_url)

        messages.error(request, "未知操作。")
        return redirect(next_url)

    q = (request.GET.get("q") or "").strip()
    filter_user_id = parse_uploader_filter_user_id(request.user, request)
    analyzed_set = set(
        asin_analysis_qs_for_user(request.user).values_list("asin", flat=True)
    )
    processing_set = set(AsinAnalysisLock.objects.values_list("asin", flat=True))
    qs = (
        original_asin_qs_for_user(request.user)
        .select_related("created_by")
        .order_by("-updated_at", "-created_at")
    )
    if filter_user_id:
        qs = filter_original_by_uploader_id(qs, filter_user_id)
    if not show_calculated:
        qs = qs.exclude(asin__in=analyzed_set)
    if q:
        qs = qs.filter(asin__icontains=q)

    page_obj = paginate(request, qs)
    rows = [
        {"orig": o, "is_analyzed": o.asin in analyzed_set, "is_processing": o.asin in processing_set}
        for o in page_obj.object_list
    ]
    return render(
        request,
        "gpt_analysis.html",
        {
            "rows": rows,
            "show_calculated": show_calculated,
            "search_q": q,
            "page_obj": page_obj,
            "pagination_qs": pagination_querystring(request),
            **uploader_filter_context(request.user, request),
        },
    )
