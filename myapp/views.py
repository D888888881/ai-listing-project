import asyncio
import io
import json
import logging
import operator
import os
import secrets
import uuid
import zipfile
from functools import reduce
from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django import forms
from django.conf import settings
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.db import IntegrityError
from django.db.models import Q
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

from .gpt_analysis_service import (
    extract_markdown_h3_body,
    listing_raw_to_markdown,
    record_ai_listing_generation,
    run_ai_listing_for_asin,
    run_gpt_for_asin,
)
from .models import OriginalAsinData, AsinAnalysis, AsinAnalysisLock, UserProfile, AiListingGenerationHistory
from .asin_access import (
    asin_analysis_qs_for_user,
    filter_original_by_user_id,
    get_active_users_for_assign,
    is_asin_admin,
    original_asin_qs_for_user,
    stamp_created_by_if_empty,
    stamp_created_by_on_new_rows,
    user_can_access_asin,
)
from .ai_image_need_service import generate_image_need_for_asin
from .nano_banana_service import (
    build_generation_plan,
    compute_generation_estimate,
    generation_wave_count,
    nano_banana_user_scope,
    optimize_finished_images,
    pending_jobs_payload,
    run_all_modules_generation,
    run_generation_wave,
    run_one_module_generation,
    run_custom_module_generation,
    run_jobs_batch,
    run_single_generation_job,
    topup_incomplete_modules,
    topup_one_chunk,
)
from .pagination import LIST_PAGE_SIZE, paginate, pagination_querystring
from .captcha_img import CAPTCHA_SESSION_KEY, verify_captcha_post

# 进程崩溃/请求超时后锁行可能残留；超过此时长视为可回收，避免界面永久「分析中」。
ASIN_ANALYSIS_LOCK_STALE = timedelta(minutes=400)


def _purge_stale_asin_analysis_locks() -> None:
    threshold = timezone.now() - ASIN_ANALYSIS_LOCK_STALE
    AsinAnalysisLock.objects.filter(started_at__lt=threshold).delete()


# Create your views here.





class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True, label="邮箱")

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("该邮箱已被注册。")
        return email


class EmailUpdateForm(forms.Form):
    """修改绑定邮箱（需验证当前密码）。"""

    new_email = forms.EmailField(label="新邮箱地址", required=True)
    current_password = forms.CharField(
        label="当前密码",
        widget=forms.PasswordInput(render_value=False),
        required=True,
    )

    def __init__(self, user: User, *args: Any, **kwargs: Any) -> None:
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["new_email"].widget.attrs.setdefault("placeholder", "name@example.com")
        self.fields["current_password"].widget.attrs.setdefault("placeholder", "验证当前登录密码")

    def clean_new_email(self) -> str:
        email = (self.cleaned_data.get("new_email") or "").strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("该邮箱已被其他账号使用。")
        if email == (self.user.email or "").strip().lower():
            raise forms.ValidationError("新邮箱与当前邮箱相同。")
        return email

    def clean_current_password(self) -> str:
        pwd = self.cleaned_data.get("current_password") or ""
        if not self.user.check_password(pwd):
            raise forms.ValidationError("当前密码不正确。")
        return pwd


class ForgotPasswordRequestForm(forms.Form):
    """忘记密码：填写注册邮箱（与 QQ 邮箱一致）。"""

    email = forms.EmailField(label="注册邮箱", required=True)

    def clean_email(self) -> str:
        return (self.cleaned_data.get("email") or "").strip().lower()


class H10CredentialsForm(forms.Form):
    """Helium10 API 凭证（超级管理员）。"""

    h10_auth_token = forms.CharField(
        label="Authorization Token",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "留空则保留已保存的值"}),
    )
    h10_x_token = forms.CharField(
        label="x-pacvue-token (JWT)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "留空则保留已保存的值"}),
    )

    def clean(self):
        cleaned = super().clean()
        from .h10_config import _normalize_token, get_h10_credentials

        auth = _normalize_token(cleaned.get("h10_auth_token") or "")
        x_token = _normalize_token(cleaned.get("h10_x_token") or "")
        existing_auth, existing_x = get_h10_credentials(allow_empty=True)
        final_auth = auth or existing_auth
        final_x = x_token or existing_x
        if not final_auth or not final_x:
            raise forms.ValidationError(
                "请填写 Authorization Token 与 x-pacvue-token（首次配置两项均必填）。"
            )
        cleaned["h10_auth_token"] = final_auth
        cleaned["h10_x_token"] = final_x
        return cleaned


def _mail_configured() -> bool:
    return bool(
        getattr(settings, "EMAIL_HOST_USER", "").strip()
        and getattr(settings, "EMAIL_HOST_PASSWORD", "").strip()
    )



_INPUT_STYLE = (
    "width:100%;padding:12px 14px;border-radius:12px;border:1px solid #e5e7eb;"
    "background:#fff;box-sizing:border-box;font-size:0.95rem;color:#1d1d1f;"
)


def _style_password_change_form(form: PasswordChangeForm) -> None:
    for field in form.fields.values():
        field.widget.attrs.setdefault("style", _INPUT_STYLE)


def _style_email_update_form(form: EmailUpdateForm) -> None:
    for field in form.fields.values():
        field.widget.attrs.setdefault("style", _INPUT_STYLE)


def _style_h10_credentials_form(form: H10CredentialsForm) -> None:
    for field in form.fields.values():
        field.widget.attrs.setdefault(
            "style",
            _INPUT_STYLE + "font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:0.85rem;",
        )


def _style_forgot_password_request_form(form: ForgotPasswordRequestForm) -> None:
    for field in form.fields.values():
        field.widget.attrs.setdefault("style", _INPUT_STYLE)


def _style_set_password_form(form: SetPasswordForm) -> None:
    for field in form.fields.values():
        field.widget.attrs.setdefault("style", _INPUT_STYLE)


FORGOT_PASSWORD_SIGNER_SALT = "asin-listing-forgot-password"
FORGOT_PASSWORD_MAX_AGE = 600





def index(request):
    return render(request, 'index.html')


def _redirect_with_q(name: str, request: HttpRequest) -> HttpResponseRedirect:
    """列表相关 POST 完成后跳回列表，尽量保留搜索与分页等 GET 参数。"""
    preserve = (request.POST.get("preserve_query") or "").strip()
    if preserve:
        return redirect(f"{reverse(name)}?{preserve}")
    params: dict[str, str] = {}
    q = (request.POST.get("q") or request.GET.get("q") or "").strip()
    if q:
        params["q"] = q
    for key in ("page", "pending_page", "records_page"):
        v = (request.POST.get(key) or "").strip()
        if v.isdigit():
            params[key] = str(int(v))
    if (request.POST.get("show_calculated") or "").strip() == "1":
        params["show_calculated"] = "1"
    base = reverse(name)
    if params:
        return redirect(f"{base}?{urlencode(params)}")
    return redirect(base)


def _parse_keywords_lines(raw: str) -> list[str]:
    out: list[str] = []
    for line in (raw or "").replace(",", "\n").splitlines():
        x = line.strip()
        if x:
            out.append(x)
    return out


def _originals_list_for_upper_asins(
    upper_asins: Iterable[str], user: Optional[Any] = None
) -> list[OriginalAsinData]:
    """按 ASIN 不区分大小写拉取原文本行（asin__in 在默认排序规则下可能漏掉大小写不一致的行）。"""
    cleaned = sorted({(a or "").strip().upper() for a in upper_asins if (a or "").strip()})
    if not cleaned:
        return []
    q_expr = reduce(operator.or_, [Q(asin__iexact=u) for u in cleaned])
    qs = OriginalAsinData.objects.filter(q_expr)
    if user is not None and user.is_authenticated and not is_asin_admin(user):
        qs = qs.filter(Q(created_by=user) | Q(assigned_to=user))
    return list(qs)


def _deny_asin_access(request: HttpRequest, asin: str) -> HttpResponse:
    messages.error(request, f"无权访问 ASIN：{asin}")
    return _redirect_with_q("original_text_list", request)



@login_required
def analysis_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    records = asin_analysis_qs_for_user(request.user).prefetch_related("details").order_by("-created_at")
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
        analysis_table_rows.append({"analysis": item, "differentiation_preview": preview})

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
    }
    return render(request, "analysis_list.html", context)


@login_required
def listing_panel(request: HttpRequest) -> HttpResponse:
    """
    Listing 面板：汇总原文本（关键词、Rufus）与差异化分析中的 VOC定位、差评改进方向及生成 Listing。
    """
    q = (request.GET.get("q") or "").strip()
    qs = asin_analysis_qs_for_user(request.user).prefetch_related("details").order_by("-created_at")
    if q:
        qs = qs.filter(asin__icontains=q)
    page_obj = paginate(request, qs)
    rows: list[dict[str, Any]] = []
    for a in page_obj.object_list:
        orig = original_asin_qs_for_user(request.user).filter(asin__iexact=a.asin).first()
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
    qs = asin_analysis_qs_for_user(request.user).prefetch_related("details").order_by("-created_at")
    if q:
        qs = qs.filter(asin__icontains=q)

    page_obj = paginate(request, qs)

    rows: List[Dict[str, Any]] = []
    for a in page_obj.object_list:
        orig = original_asin_qs_for_user(request.user).filter(asin__iexact=a.asin).first()
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
    qs = original_asin_qs_for_user(request.user).order_by("-updated_at", "-created_at")
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
            "parallel_workers": int(getattr(settings, "NANO_BANANA_MODULE_WORKERS", 6)),
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
    try:
        payload = _with_nano_user(
            request.user,
            run_jobs_batch,
            orig,
            job_specs,
            user_notes=user_notes,
        )
    except Exception as e:
        logger.exception("run_jobs_batch failed asin=%s", asin)
        err_msg = str(e)
        if "interpreter shutdown" in err_msg.lower():
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Django 正在热重载（检测到代码文件变更），请等待服务稳定后重试本批。生图过程中请勿保存 .py 文件。",
                    "reload": True,
                },
                status=503,
            )
        return JsonResponse({"ok": False, "error": err_msg}, status=502)
    media_url = _ai_image_media_url()
    fin_struct = _payload_finished_images(payload)
    added = int(payload.get("added") or 0)
    batch_errors = payload.get("errors") or []
    return JsonResponse(
        {
            "ok": True,
            "partial": bool(batch_errors) and added >= 0,
            "asin": orig.asin,
            "added": added,
            "errors": batch_errors,
            "batch_size": len(job_specs),
            "finished_images": fin_struct,
            "finished_images_all": fin_struct,
            "data": _original_images_row_payload(fin_struct, media_url),
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
    """按 ASIN 批量生图：每模块 3 张，全局最多 6 个 API 并行（可跨模块）。"""
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
            topup = topup_incomplete_modules(orig, user_notes=user_notes)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)
    media_url = _ai_image_media_url()
    fin_struct = _normalize_original_images_struct(topup.get("finished_images") or payload.get("finished_images"))
    still = topup.get("still_incomplete") or []
    incomplete = payload.get("incomplete") or []
    return JsonResponse(
        {
            "ok": True,
            "asin": orig.asin,
            "modules": payload.get("modules") or [],
            "incomplete": incomplete,
            "topped_up": topup.get("topped_up") or [],
            "still_incomplete": still,
            "all_complete": len(still) == 0 and len(incomplete) == 0,
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
    filter_user_id: Optional[int] = None
    if is_asin_admin(request.user):
        raw_uid = (request.GET.get("filter_user") or "").strip()
        if raw_uid.isdigit():
            filter_user_id = int(raw_uid)
    qs = (
        original_asin_qs_for_user(request.user)
        .select_related("created_by", "assigned_to")
        .order_by("-updated_at", "-created_at")
    )
    if filter_user_id:
        qs = filter_original_by_user_id(qs, filter_user_id)
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
    if is_asin_admin(request.user):
        ctx["assignable_users"] = list(get_active_users_for_assign())
        ctx["filter_user_id"] = filter_user_id or ""
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


def register_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect(reverse("original_text_list"))

    if request.method == "GET":
        return render(
            request,
            "register.html",
            {"form": RegisterForm(), "captcha_nonce": secrets.token_hex(6)},
        )

    if not verify_captcha_post(request):
        messages.error(request, "验证码错误或已失效，请点击验证码图片刷新后重试。")
        request.session.pop(CAPTCHA_SESSION_KEY, None)
        return render(
            request,
            "register.html",
            {
                "form": RegisterForm(request.POST),
                "captcha_nonce": secrets.token_hex(6),
            },
        )
    request.session.pop(CAPTCHA_SESSION_KEY, None)

    form = RegisterForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "register.html",
            {"form": form, "captcha_nonce": secrets.token_hex(6)},
        )

    user = form.save(commit=False)
    user.email = form.cleaned_data["email"]
    user.is_active = False  # 必须超级管理员审批后才可登录
    user.save()
    # profile 在信号里自动创建，这里明确设置待审批
    UserProfile.objects.update_or_create(
        user=user,
        defaults={"approval_status": UserProfile.APPROVAL_PENDING},
    )
    messages.success(request, "注册成功，等待超级管理员审批后即可登录。")
    return redirect(reverse("login"))


def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect(reverse("original_text_list"))

    if request.method == "GET":
        return render(
            request,
            "login.html",
            {"form": AuthenticationForm(), "captcha_nonce": secrets.token_hex(6)},
        )

    if not verify_captcha_post(request):
        messages.error(request, "验证码错误或已失效，请点击验证码图片刷新后重试。")
        request.session.pop(CAPTCHA_SESSION_KEY, None)
        return render(
            request,
            "login.html",
            {
                "form": AuthenticationForm(request.POST),
                "captcha_nonce": secrets.token_hex(6),
            },
        )
    request.session.pop(CAPTCHA_SESSION_KEY, None)

    form = AuthenticationForm(request=request, data=request.POST)
    if not form.is_valid():
        messages.error(request, "用户名或密码错误，或账户尚未激活。")
        return render(
            request,
            "login.html",
            {"form": form, "captcha_nonce": secrets.token_hex(6)},
        )

    user = form.get_user()
    profile, _ = UserProfile.objects.get_or_create(
        user=user,
        defaults={"approval_status": UserProfile.APPROVAL_APPROVED if (user.is_staff or user.is_superuser) else UserProfile.APPROVAL_PENDING},
    )
    if profile.approval_status != UserProfile.APPROVAL_APPROVED:
        messages.error(request, "账户尚未审批通过，请联系超级管理员。")
        return render(
            request,
            "login.html",
            {"form": form, "captcha_nonce": secrets.token_hex(6)},
        )

    login(request, user)
    return redirect(reverse("original_text_list"))


@require_GET
def captcha_image(request: HttpRequest) -> HttpResponse:
    """输出 PNG 验证码图；正确答案仅保存在 session，不以明文出现在页面。"""
    try:
        from .captcha_img import generate_captcha_png

        answer, buf = generate_captcha_png()
    except ImportError as e:
        return HttpResponse(
            f"需要安装 Pillow 才能生成图形验证码：{e}。请执行 pip install Pillow",
            status=500,
            content_type="text/plain; charset=utf-8",
        )
    request.session[CAPTCHA_SESSION_KEY] = answer
    request.session.modified = True
    resp = HttpResponse(buf.getvalue(), content_type="image/png")
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    return resp


@login_required
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    messages.success(request, "已退出登录。")
    return redirect(reverse("login"))




def forgot_password_view(request: HttpRequest) -> HttpResponse:
    """通过邮箱接收重置链接（需配置 QQ SMTP 等）。"""
    if request.user.is_authenticated:
        return redirect(reverse("original_text_list"))



    if request.method == "GET":
        form = ForgotPasswordRequestForm()
        _style_forgot_password_request_form(form)
        return render(
            request,
            "forgot_password.html",
            {"form": form, "captcha_nonce": secrets.token_hex(6)},
        )

    if not verify_captcha_post(request):
        messages.error(request, "验证码错误或已失效，请点击验证码图片刷新后重试。")
        request.session.pop(CAPTCHA_SESSION_KEY, None)
        form = ForgotPasswordRequestForm(request.POST)
        _style_forgot_password_request_form(form)
        return render(
            request,
            "forgot_password.html",
            {"form": form, "captcha_nonce": secrets.token_hex(6)},
        )
    request.session.pop(CAPTCHA_SESSION_KEY, None)

    form = ForgotPasswordRequestForm(request.POST)
    _style_forgot_password_request_form(form)
    msg_sent = (
        "若该邮箱已在本系统注册，我们将向该邮箱发送重置密码邮件，请查收收件箱与垃圾箱（邮件可能延迟几分钟）。"
    )

    if not form.is_valid():
        return render(
            request,
            "forgot_password.html",
            {"form": form, "captcha_nonce": secrets.token_hex(6)},
        )

    email = form.cleaned_data["email"]
    user = User.objects.filter(email__iexact=email).first()

    if user:
        if not _mail_configured():
            messages.error(
                request,
                "系统尚未配置发件邮箱。请联系管理员设置 QQ 邮箱 SMTP（环境变量 EMAIL_HOST_USER、EMAIL_HOST_PASSWORD 授权码）。",
            )
            return render(
                request,
                "forgot_password.html",
                {"form": form, "captcha_nonce": secrets.token_hex(6)},
            )

        signer = TimestampSigner(salt=FORGOT_PASSWORD_SIGNER_SALT)
        token = signer.sign(str(user.pk))
        reset_url = request.build_absolute_uri(reverse("password_reset_confirm"))
        reset_url = f"{reset_url}?token={quote(token)}"

        from_addr = (
            getattr(settings, "DEFAULT_FROM_EMAIL", None)
            or getattr(settings, "EMAIL_HOST_USER", "")
            or ""
        )
        subject = "【ASIN Listing】重置登录密码"
        body_plain = (
            "您好，\n\n"
            f"请点击以下链接，在 1 小时内重置密码（请勿转发给他人）：\n{reset_url}\n\n"
            "若您未申请重置，请忽略本邮件。\n"
        )
        body_html = (
            "<p>您好，</p>"
            "<p>请点击下方按钮或链接，在 <strong>1 小时内</strong>重置密码（请勿转发给他人）：</p>"
            f'<p><a href="{reset_url}" style="display:inline-block;padding:10px 22px;background:#0071e3;'
            'color:#fff;text-decoration:none;border-radius:999px;font-weight:600;">重置密码</a></p>'
            f'<p style="word-break:break-all;font-size:0.85rem;color:#667085;">{reset_url}</p>'
            "<p>若您未申请重置，请忽略本邮件。</p>"
        )
        try:
            send_mail(
                subject,
                body_plain,
                from_addr,
                [user.email],
                fail_silently=False,
                html_message=body_html,
            )
        except Exception:
            messages.error(request, "邮件发送失败，请稍后重试或联系管理员检查邮箱配置。")
            return render(
                request,
                "forgot_password.html",
                {"form": form, "captcha_nonce": secrets.token_hex(6)},
            )
        messages.success(request, msg_sent)
    else:
        messages.error(request, "该邮箱未注册。请确认后重试，或直接注册新账号。")

    return redirect(reverse("forgot_password"))


def password_reset_confirm_view(request: HttpRequest) -> HttpResponse:
    """通过邮件链接设置新密码（无需登录）。"""
    token = (request.GET.get("token") or request.POST.get("token") or "").strip()
    if not token:
        messages.error(request, "无效的重置链接。")
        return redirect(reverse("login"))

    signer = TimestampSigner(salt=FORGOT_PASSWORD_SIGNER_SALT)
    try:
        uid = signer.unsign(token, max_age=FORGOT_PASSWORD_MAX_AGE)
        user = User.objects.get(pk=int(uid))
    except SignatureExpired:
        messages.error(request, "链接已过期，请重新申请忘记密码。")
        return redirect(reverse("forgot_password"))
    except (BadSignature, ValueError, User.DoesNotExist):
        messages.error(request, "链接无效。")
        return redirect(reverse("login"))

    if request.method == "POST":
        form = SetPasswordForm(user, request.POST)
        _style_set_password_form(form)
        if form.is_valid():
            form.save()
            messages.success(request, "密码已重置，请使用新密码登录。")
            return redirect(reverse("login"))
    else:
        form = SetPasswordForm(user)
        _style_set_password_form(form)

    return render(
        request,
        "password_reset_confirm.html",
        {"form": form, "token": token},
    )


@login_required
def account_settings(request: HttpRequest) -> HttpResponse:
    """账户设置：邮箱、登录密码；超级管理员可配置 Helium10 凭证。"""
    from .h10_config import h10_credentials_status, set_h10_credentials

    def _render(email_form, password_form, h10_form=None):
        ctx = {"email_form": email_form, "password_form": password_form}
        if request.user.is_superuser:
            if h10_form is None:
                h10_form = H10CredentialsForm()
            _style_h10_credentials_form(h10_form)
            ctx["h10_form"] = h10_form
            ctx["h10_status"] = h10_credentials_status()
        return render(request, "account_settings.html", ctx)

    if request.method == "POST":
        action = (request.POST.get("form_action") or "").strip()

        if action == "h10":
            if not request.user.is_superuser:
                messages.error(request, "无权修改 Helium10 凭证。")
                return redirect(reverse("account_settings"))
            email_form = EmailUpdateForm(request.user)
            password_form = PasswordChangeForm(request.user)
            h10_form = H10CredentialsForm(request.POST)
            _style_email_update_form(email_form)
            _style_password_change_form(password_form)
            _style_h10_credentials_form(h10_form)
            if h10_form.is_valid():
                set_h10_credentials(
                    h10_form.cleaned_data["h10_auth_token"],
                    h10_form.cleaned_data["h10_x_token"],
                    user=request.user,
                )
                messages.success(request, "Helium10 凭证已保存。")
                return redirect(reverse("account_settings"))
            return _render(email_form, password_form, h10_form)

        if action == "email":
            email_form = EmailUpdateForm(request.user, request.POST)
            password_form = PasswordChangeForm(request.user)
            _style_email_update_form(email_form)
            _style_password_change_form(password_form)
            if email_form.is_valid():
                request.user.email = email_form.cleaned_data["new_email"]
                request.user.save(update_fields=["email"])
                messages.success(request, "邮箱已更新。")
                return redirect(reverse("account_settings"))
            return _render(email_form, password_form)

        # 默认：修改密码（兼容未传 form_action 的旧提交）
        password_form = PasswordChangeForm(request.user, request.POST)
        email_form = EmailUpdateForm(request.user)
        _style_password_change_form(password_form)
        _style_email_update_form(email_form)
        if password_form.is_valid():
            user = password_form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "密码已修改。")
            return redirect(reverse("account_settings"))
        return _render(email_form, password_form)

    password_form = PasswordChangeForm(request.user)
    email_form = EmailUpdateForm(request.user)
    _style_password_change_form(password_form)
    _style_email_update_form(email_form)
    return _render(email_form, password_form)


@login_required
@user_passes_test(lambda u: u.is_staff)
def user_manage(request: HttpRequest) -> HttpResponse:
    base_qs = User.objects.all().order_by("-date_joined")
    active_count = User.objects.filter(is_active=True).count()
    inactive_count = User.objects.filter(is_active=False).count()
    page_obj = paginate(request, base_qs)
    rows = []
    for u in page_obj.object_list:
        profile, _ = UserProfile.objects.get_or_create(
            user=u,
            defaults={"approval_status": UserProfile.APPROVAL_APPROVED if (u.is_staff or u.is_superuser) else UserProfile.APPROVAL_PENDING},
        )
        rows.append((u, profile))
    return render(
        request,
        "user_manage.html",
        {
            "rows": rows,
            "active_count": active_count,
            "inactive_count": inactive_count,
            "page_obj": page_obj,
            "pagination_qs": pagination_querystring(request),
        },
    )


@login_required
@user_passes_test(lambda u: u.is_staff)
def toggle_user_active(request: HttpRequest, user_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect(reverse("user_manage"))
    if request.user.id == user_id:
        messages.error(request, "不能禁用当前登录账号。")
        return _redirect_with_q("user_manage", request)

    target = User.objects.filter(id=user_id).first()
    if not target:
        messages.error(request, "用户不存在。")
        return _redirect_with_q("user_manage", request)

    target.is_active = not target.is_active
    target.save(update_fields=["is_active"])
    messages.success(request, f"{target.username} 已{'启用' if target.is_active else '禁用'}。")
    return _redirect_with_q("user_manage", request)


@login_required
@user_passes_test(lambda u: u.is_staff)
def delete_user(request: HttpRequest, user_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect(reverse("user_manage"))
    if request.user.id == user_id:
        messages.error(request, "不能删除当前登录账号。")
        return _redirect_with_q("user_manage", request)
    target = User.objects.filter(id=user_id).first()
    if not target:
        messages.error(request, "用户不存在。")
        return _redirect_with_q("user_manage", request)
    if target.is_superuser and not request.user.is_superuser:
        messages.error(request, "仅超级管理员可以删除超级管理员账号。")
        return _redirect_with_q("user_manage", request)
    username = target.username
    target.delete()
    messages.success(request, f"已删除用户：{username}")
    return _redirect_with_q("user_manage", request)


@login_required
@user_passes_test(lambda u: u.is_superuser)
def approval_list(request: HttpRequest) -> HttpResponse:
    pending_qs = UserProfile.objects.select_related("user").filter(approval_status=UserProfile.APPROVAL_PENDING).order_by("created_at")
    pending_page_obj = paginate(request, pending_qs, page_param="pending_page")
    records_qs = (
        UserProfile.objects.select_related("user", "approved_by")
        .exclude(approval_status=UserProfile.APPROVAL_PENDING)
        .order_by("-approved_at", "-created_at")
    )
    records_page_obj = paginate(request, records_qs, page_param="records_page")
    pending_pagination_qs = pagination_querystring(request, "pending_page")
    records_pagination_qs = pagination_querystring(request, "records_page")
    return render(
        request,
        "approval_list.html",
        {
            "pending_profiles": pending_page_obj.object_list,
            "pending_page_obj": pending_page_obj,
            "pending_pagination_qs": pending_pagination_qs,
            "approval_records": records_page_obj.object_list,
            "records_page_obj": records_page_obj,
            "records_pagination_qs": records_pagination_qs,
        },
    )


@login_required
@user_passes_test(lambda u: u.is_superuser)
def approval_action(request: HttpRequest, user_id: int, action: str) -> HttpResponse:
    if request.method != "POST":
        return redirect(reverse("approval_list"))
    user = User.objects.filter(id=user_id).first()
    if not user:
        messages.error(request, "用户不存在。")
        return _redirect_with_q("approval_list", request)

    profile, _ = UserProfile.objects.get_or_create(user=user)
    if action == "approve":
        profile.approval_status = UserProfile.APPROVAL_APPROVED
        profile.approved_by = request.user
        profile.approved_at = timezone.now()
        profile.save(update_fields=["approval_status", "approved_by", "approved_at"])
        user.is_active = True
        user.save(update_fields=["is_active"])
        messages.success(request, f"已审批通过：{user.username}")
    elif action == "reject":
        profile.approval_status = UserProfile.APPROVAL_REJECTED
        profile.approved_by = request.user
        profile.approved_at = timezone.now()
        profile.save(update_fields=["approval_status", "approved_by", "approved_at"])
        user.is_active = False
        user.save(update_fields=["is_active"])
        messages.success(request, f"已拒绝：{user.username}")
    else:
        messages.error(request, "无效操作。")
    return _redirect_with_q("approval_list", request)


@login_required
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
    analyzed_set = set(
        asin_analysis_qs_for_user(request.user).values_list("asin", flat=True)
    )
    processing_set = set(AsinAnalysisLock.objects.values_list("asin", flat=True))
    qs = original_asin_qs_for_user(request.user).order_by("-updated_at", "-created_at")
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
        },
    )