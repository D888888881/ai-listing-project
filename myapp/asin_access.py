"""ASIN 数据可见性：超级管理员可见全部；普通用户仅可见自己上传或分配给自己的 ASIN。"""

from __future__ import annotations

from typing import Optional

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Exists, OuterRef, Q, QuerySet

from .models import AsinAnalysis, OriginalAsinData

User = get_user_model()


def is_asin_admin(user: AbstractBaseUser) -> bool:
    return bool(user and user.is_authenticated and user.is_superuser)


def _owner_q(user: AbstractBaseUser) -> Q:
    return Q(created_by=user) | Q(assigned_to=user)


def original_asin_qs_for_user(user: AbstractBaseUser) -> QuerySet[OriginalAsinData]:
    qs = OriginalAsinData.objects.all()
    if is_asin_admin(user):
        return qs
    return qs.filter(_owner_q(user))


def asin_analysis_qs_for_user(user: AbstractBaseUser) -> QuerySet[AsinAnalysis]:
    qs = AsinAnalysis.objects.all()
    if is_asin_admin(user):
        return qs
    return qs.filter(
        Exists(
            OriginalAsinData.objects.filter(
                _owner_q(user),
                asin__iexact=OuterRef("asin"),
            )
        )
    )


def user_can_access_asin(user: AbstractBaseUser, asin: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    if is_asin_admin(user):
        return True
    key = (asin or "").strip()
    if not key:
        return False
    return OriginalAsinData.objects.filter(_owner_q(user), asin__iexact=key).exists()


def filter_original_by_user_id(
    qs: QuerySet[OriginalAsinData], user_id: Optional[int]
) -> QuerySet[OriginalAsinData]:
    if not user_id:
        return qs
    return qs.filter(Q(created_by_id=user_id) | Q(assigned_to_id=user_id))


def stamp_created_by_if_empty(obj: OriginalAsinData, user: AbstractBaseUser) -> bool:
    """若尚无上传者则记为当前用户；返回是否写入。"""
    if not user or not user.is_authenticated or obj.created_by_id:
        return False
    obj.created_by = user
    obj.save(update_fields=["created_by", "updated_at"])
    return True


def stamp_created_by_on_new_rows(asins: list[str], user: AbstractBaseUser) -> None:
    """bulk_create / update_or_create 之后，为尚无 created_by 的 ASIN 打上上传者。"""
    if not user or not user.is_authenticated or not asins:
        return
    cleaned = [(a or "").strip() for a in asins if (a or "").strip()]
    if not cleaned:
        return
    q_expr = Q()
    for a in cleaned:
        q_expr |= Q(asin__iexact=a)
    OriginalAsinData.objects.filter(q_expr, created_by__isnull=True).update(created_by=user)


def get_active_users_for_assign():
    return User.objects.filter(is_active=True).order_by("username")
