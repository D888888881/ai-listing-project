"""认证、用户管理、审批、账户设置、生图运维。"""
from __future__ import annotations

import logging
import secrets
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    SetPasswordForm,
)
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..captcha_img import CAPTCHA_SESSION_KEY, verify_captcha_post
from ..image_gen_jobs import retry_job_from_db
from ..models import ImageGenJob, UserProfile
from ..pagination import paginate, pagination_querystring
from .common import (
    EmailUpdateForm,
    ForgotPasswordRequestForm,
    H10CredentialsForm,
    NanoApiLimitsForm,
    RegisterForm,
    _mail_configured,
    _redirect_with_q,
    _style_email_update_form,
    _style_forgot_password_request_form,
    _style_h10_credentials_form,
    _style_nano_limits_form,
    _style_password_change_form,
    _style_set_password_form,
)

logger = logging.getLogger(__name__)

FORGOT_PASSWORD_SIGNER_SALT = "asin-listing-forgot-password"
FORGOT_PASSWORD_MAX_AGE = 600

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
        from ..captcha_img import generate_captcha_png

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
    """账户设置：邮箱、登录密码；超级管理员可配置 Helium10 凭证与生图并发限额。"""
    from ..h10_config import h10_credentials_status, set_h10_credentials
    from ..image_gen_config import api_limits_status, set_api_limits

    def _render(email_form, password_form, h10_form=None, nano_form=None):
        ctx = {"email_form": email_form, "password_form": password_form}
        if request.user.is_superuser:
            if h10_form is None:
                h10_form = H10CredentialsForm()
            _style_h10_credentials_form(h10_form)
            ctx["h10_form"] = h10_form
            ctx["h10_status"] = h10_credentials_status()
            if nano_form is None:
                lim = api_limits_status()
                nano_form = NanoApiLimitsForm(
                    initial={
                        "global_limit": lim["global"],
                        "per_user_limit": lim["per_user"],
                        "per_user_superuser_limit": lim["per_user_superuser"],
                    }
                )
            _style_nano_limits_form(nano_form)
            ctx["nano_form"] = nano_form
            ctx["nano_limits_status"] = api_limits_status()
        return render(request, "account_settings.html", ctx)

    if request.method == "POST":
        action = (request.POST.get("form_action") or "").strip()

        if action == "nano_limits":
            if not request.user.is_superuser:
                messages.error(request, "无权修改生图并发限额。")
                return redirect(reverse("account_settings"))
            email_form = EmailUpdateForm(request.user)
            password_form = PasswordChangeForm(request.user)
            h10_form = H10CredentialsForm()
            nano_form = NanoApiLimitsForm(request.POST)
            _style_email_update_form(email_form)
            _style_password_change_form(password_form)
            _style_h10_credentials_form(h10_form)
            _style_nano_limits_form(nano_form)
            if nano_form.is_valid():
                set_api_limits(
                    global_limit=nano_form.cleaned_data["global_limit"],
                    per_user=nano_form.cleaned_data["per_user_limit"],
                    per_user_superuser=nano_form.cleaned_data["per_user_superuser_limit"],
                    user=request.user,
                )
                messages.success(request, "生图并发限额已保存（立即生效，无需重启）。")
                return redirect(reverse("account_settings"))
            return _render(email_form, password_form, h10_form, nano_form)

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


@login_required
@user_passes_test(lambda u: u.is_staff)
def image_gen_ops(request: HttpRequest) -> HttpResponse:
    """生图运维：任务列表、指标、DLQ 重试。"""
    from ..image_gen_config import api_limits_status
    from ..image_gen_metrics import metrics_snapshot

    status_filter = (request.GET.get("status") or "").strip()
    qs = ImageGenJob.objects.select_related("user").order_by("-created_at")
    if status_filter in dict(ImageGenJob.STATUS_CHOICES):
        qs = qs.filter(status=status_filter)
    page_obj = paginate(request, qs)
    return render(
        request,
        "image_gen_ops.html",
        {
            "page_obj": page_obj,
            "jobs": page_obj.object_list,
            "status_filter": status_filter,
            "status_choices": ImageGenJob.STATUS_CHOICES,
            "metrics": metrics_snapshot(),
            "limits": api_limits_status(),
            "pagination_qs": pagination_querystring(request),
        },
    )


@login_required
@user_passes_test(lambda u: u.is_staff)
@require_GET
def image_gen_ops_metrics_json(request: HttpRequest) -> JsonResponse:
    from ..image_gen_metrics import metrics_snapshot

    return JsonResponse({"ok": True, "metrics": metrics_snapshot()})


@login_required
@user_passes_test(lambda u: u.is_staff)
@require_POST
def image_gen_job_retry(request: HttpRequest) -> HttpResponse:
    job_id = (request.POST.get("job_id") or "").strip()
    if not job_id:
        messages.error(request, "缺少任务 ID。")
        return redirect(reverse("image_gen_ops"))
    try:
        new_id = retry_job_from_db(job_id, operator=request.user)
        messages.success(request, f"已重新入队，新任务 ID：{new_id[:12]}…")
    except ValueError as e:
        messages.error(request, str(e))
    return redirect(reverse("image_gen_ops"))