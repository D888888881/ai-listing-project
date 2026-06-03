"""列表页分页：统一每页条数与查询串（保留筛选参数、去掉页码键）。"""
from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import HttpRequest

LIST_PAGE_SIZE = 20


def pagination_querystring(request: HttpRequest, omit: str | tuple[str, ...] = "page") -> str:
    """当前 GET 参数序列化，并去掉指定键（用于生成分页链接）。"""
    qd = request.GET.copy()
    keys = (omit,) if isinstance(omit, str) else omit
    for k in keys:
        qd.pop(k, None)
    return qd.urlencode()


def paginate(
    request: HttpRequest,
    queryset: QuerySet,
    *,
    per_page: int = LIST_PAGE_SIZE,
    page_param: str = "page",
):
    """对 QuerySet 分页，页码来自 request.GET[page_param]。"""
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get(page_param) or 1)
