"""视图层公共表单与工具函数。"""
from __future__ import annotations

import logging
import operator
from datetime import timedelta
from functools import reduce
from typing import Any, Iterable, Optional
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm, UserCreationForm
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from ..asin_access import is_asin_admin
from ..models import AsinAnalysisLock, OriginalAsinData

logger = logging.getLogger(__name__)

ASIN_ANALYSIS_LOCK_STALE = timedelta(minutes=400)

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
        from ..h10_config import _normalize_token, get_h10_credentials

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


class NanoApiLimitsForm(forms.Form):
    """GrsAi 全站/每用户并发限额（超级管理员）。"""

    global_limit = forms.IntegerField(label="全站 API 并发上限", min_value=1, max_value=500)
    per_user_limit = forms.IntegerField(label="普通用户每用户并发", min_value=1, max_value=50)
    per_user_superuser_limit = forms.IntegerField(
        label="管理员每用户并发",
        min_value=1,
        max_value=50,
    )


def _style_nano_limits_form(form: forms.Form) -> None:
    for name in ("global_limit", "per_user_limit", "per_user_superuser_limit"):
        if name in form.fields:
            form.fields[name].widget.attrs.update(
                {"class": "settings-input", "style": "width:100%;max-width:280px;"}
            )


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
    qs = OriginalAsinData.objects.filter(q_expr).select_related("created_by")
    if user is not None and user.is_authenticated and not is_asin_admin(user):
        qs = qs.filter(Q(created_by=user) | Q(assigned_to=user))
    return list(qs)


def _deny_asin_access(request: HttpRequest, asin: str) -> HttpResponse:
    messages.error(request, f"无权访问 ASIN：{asin}")
    return _redirect_with_q("original_text_list", request)


