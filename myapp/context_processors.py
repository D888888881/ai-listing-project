def pending_approval_badge(request):
    """超级管理员导航栏「审批页面」旁显示待审批注册数量。"""
    count = 0
    if request.user.is_authenticated and request.user.is_superuser:
        from .models import UserProfile

        count = UserProfile.objects.filter(
            approval_status=UserProfile.APPROVAL_PENDING,
        ).count()
    display = ""
    if count > 99:
        display = "99+"
    elif count > 0:
        display = str(count)
    return {
        "pending_approval_count": count,
        "pending_approval_badge_display": display,
    }
