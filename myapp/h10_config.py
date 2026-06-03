"""Helium10 API 凭证：优先读数据库，其次环境变量。"""
from __future__ import annotations

import os
from typing import Optional, Tuple

from django.contrib.auth.models import User

from .models import SystemSetting


class H10CredentialMissingError(RuntimeError):
    """未配置 Helium10 凭证。"""


def _mask_token(value: str, tail: int = 8) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= tail:
        return "*" * len(text)
    return f"{'*' * 12}…{text[-tail:]}"


def _normalize_token(value: str) -> str:
    """去掉首尾空白及用户误粘贴的引号。"""
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    return text


def get_h10_credentials(*, allow_empty: bool = False) -> Tuple[str, str]:
    auth = _normalize_token(SystemSetting.get_value(SystemSetting.KEY_H10_AUTH_TOKEN))
    x_token = _normalize_token(SystemSetting.get_value(SystemSetting.KEY_H10_X_TOKEN))
    if not auth:
        auth = os.environ.get("H10_AUTH_TOKEN", "").strip()
    if not x_token:
        x_token = os.environ.get("H10_X_TOKEN", "").strip()
    if not allow_empty and (not auth or not x_token):
        raise H10CredentialMissingError(
            "Helium10 凭证未配置。请由超级管理员在「设置 → Helium10 凭证」中填写，"
            "或设置环境变量 H10_AUTH_TOKEN / H10_X_TOKEN。"
        )
    return auth, x_token


def set_h10_credentials(auth_token: str, x_token: str, user: Optional[User] = None) -> None:
    SystemSetting.set_value(SystemSetting.KEY_H10_AUTH_TOKEN, _normalize_token(auth_token), user=user)
    SystemSetting.set_value(SystemSetting.KEY_H10_X_TOKEN, _normalize_token(x_token), user=user)


def h10_credentials_status() -> dict:
    auth, x_token = get_h10_credentials(allow_empty=True)
    row_auth = SystemSetting.objects.filter(key=SystemSetting.KEY_H10_AUTH_TOKEN).first()
    row_x = SystemSetting.objects.filter(key=SystemSetting.KEY_H10_X_TOKEN).first()
    updated_at = None
    updated_by = None
    for row in (row_auth, row_x):
        if row and row.updated_at:
            if updated_at is None or row.updated_at > updated_at:
                updated_at = row.updated_at
                updated_by = row.updated_by
    return {
        "configured": bool(auth and x_token),
        "auth_masked": _mask_token(auth),
        "x_token_masked": _mask_token(x_token),
        "from_env_only": bool(auth and x_token and not (row_auth and row_x)),
        "updated_at": updated_at,
        "updated_by": updated_by,
    }
