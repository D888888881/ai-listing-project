from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

class AsinAnalysis(models.Model):
    asin = models.CharField(max_length=20, unique=True, verbose_name="ASIN")
    listing = models.TextField(verbose_name="Listing 信息")
    # 可以保留一些全局字段，比如整体 VOC 等，但不是必须
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.asin


class AnalysisDetail(models.Model):
    analysis = models.ForeignKey(
        AsinAnalysis,
        on_delete=models.CASCADE,
        related_name='details'
    )
    CATEGORY_CHOICES = [
        ("benchmark", "对标 ASIN 分析"),
        ("cluster", "ASIN 集群分析"),
        ("differentiation", "差异化分析"),
    ]
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, verbose_name="维度类别")
    gpt_summary = models.TextField(verbose_name="GPT 总结与优化")
    satisfy_condition = models.TextField(verbose_name="满足条件")

    class Meta:
        ordering = ["category"]  # benchmark -> cluster -> differentiation
        unique_together = ['analysis', 'category']

    def __str__(self):
        return f"{self.analysis.asin} - {self.get_category_display()}"


class OriginalAsinData(models.Model):
    """
    “原文本/最初数据”存储：ASIN、关键词、Ask Rufus、VOC（导入 JSON）、ASIN 集群、VOC 集群。
    voc_cluster：仅通过「导入 VOC 集群」写入，挂在「对标」行上，不在列表为集群 ASIN 单独建行的 VOC。
    """
    asin = models.CharField(max_length=20, unique=True, verbose_name="ASIN")
    keywords = models.JSONField(default=list, blank=True, verbose_name="关键词")
    ask_rufus = models.JSONField(default=dict, blank=True, verbose_name="Ask Rufus")
    asin_cluster = models.JSONField(default=list, blank=True, verbose_name="ASIN 集群")
    voc_cluster = models.JSONField(default=dict, blank=True, verbose_name="VOC 集群(仅对标行展示)")
    voc = models.JSONField(default=dict, blank=True, verbose_name="VOC 原始数据(JSON)")
    # AI-Listing 页可编辑缓存（覆盖展示与生成 Listing 时的输入；未填 VOC/差评/建议时仍回退为分析概览抽取）
    material_supplement = models.TextField(blank=True, default="待定", verbose_name="材质与补充")
    voc_positioning_edited = models.TextField(blank=True, default="", verbose_name="VOC定位(可编辑)")
    negative_direction_edited = models.TextField(blank=True, default="", verbose_name="差评改进方向(可编辑)")
    cluster_suggestion_edited = models.TextField(blank=True, default="", verbose_name="差异化建议(可编辑)")
    # AI 生图页可编辑字段
    main_image_requirements = models.TextField(blank=True, default="", verbose_name="主图图需")
    aplus_image_requirements = models.TextField(blank=True, default="", verbose_name="APlus图需")
    original_images = models.JSONField(default=list, blank=True, verbose_name="原图")
    finished_images = models.JSONField(default=list, blank=True, verbose_name="成品图")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_original_asins",
        verbose_name="上传用户",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_original_asins",
        verbose_name="分配给",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Original({self.asin})"


class AiListingGenerationHistory(models.Model):
    """
    AI-Listing 页每次点击「生成 Listing」成功后的输入与输出快照，供历史记录查看。
    """

    asin = models.CharField(max_length=20, db_index=True, verbose_name="ASIN")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="生成时间")
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_listing_generations",
        verbose_name="操作用户",
    )
    generated_by_username = models.CharField(
        max_length=150,
        blank=True,
        default="",
        verbose_name="用户名快照",
        help_text="生成时刻的用户名，用户删除后仍可显示",
    )
    keywords = models.JSONField(default=list, blank=True, verbose_name="关键词")
    ask_rufus = models.JSONField(default=dict, blank=True, verbose_name="Ask Rufus")
    voc_positioning = models.TextField(blank=True, default="", verbose_name="VOC定位(当时)")
    negative_direction = models.TextField(blank=True, default="", verbose_name="差评改进方向(当时)")
    cluster_suggestion = models.TextField(blank=True, default="", verbose_name="差异化建议(当时)")
    material_supplement = models.TextField(blank=True, default="", verbose_name="材质与补充(当时)")
    listing = models.TextField(blank=True, default="", verbose_name="Listing 输出")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "AI-Listing 生成历史"
        verbose_name_plural = "AI-Listing 生成历史"

    def __str__(self) -> str:
        return f"{self.asin} @ {self.created_at}"


class AsinAnalysisLock(models.Model):
    """
    GPT 分析并发锁：同一 ASIN 同时只允许一个请求调用模型。
    """
    asin = models.CharField(max_length=20, unique=True, verbose_name="ASIN")
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gpt_analysis_locks",
    )
    started_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ASIN GPT 分析锁"
        verbose_name_plural = "ASIN GPT 分析锁"

    def __str__(self):
        return f"Lock({self.asin})"


class UserProfile(models.Model):
    APPROVAL_PENDING = "pending"
    APPROVAL_APPROVED = "approved"
    APPROVAL_REJECTED = "rejected"
    APPROVAL_CHOICES = [
        (APPROVAL_PENDING, "待审批"),
        (APPROVAL_APPROVED, "已审批"),
        (APPROVAL_REJECTED, "已拒绝"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    approval_status = models.CharField(max_length=20, choices=APPROVAL_CHOICES, default=APPROVAL_PENDING)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_users",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_approval_status_display()})"


@receiver(post_save, sender=get_user_model())
def ensure_user_profile(sender, instance, created, **kwargs):
    if created:
        status = UserProfile.APPROVAL_APPROVED if (instance.is_superuser or instance.is_staff) else UserProfile.APPROVAL_PENDING
        UserProfile.objects.create(user=instance, approval_status=status)


class SystemSetting(models.Model):
    """系统级键值配置（如 Helium10 API 凭证），由超级管理员在设置页维护。"""

    KEY_H10_AUTH_TOKEN = "h10_auth_token"
    KEY_H10_X_TOKEN = "h10_x_token"

    key = models.CharField(max_length=64, unique=True, verbose_name="配置键")
    value = models.TextField(blank=True, default="", verbose_name="配置值")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="system_settings_updated",
        verbose_name="最后更新人",
    )

    class Meta:
        verbose_name = "系统配置"
        verbose_name_plural = "系统配置"

    def __str__(self):
        return self.key

    @classmethod
    def get_value(cls, key: str, default: str = "") -> str:
        row = cls.objects.filter(key=key).first()
        return (row.value if row else default).strip()

    @classmethod
    def set_value(cls, key: str, value: str, user=None) -> None:
        cls.objects.update_or_create(
            key=key,
            defaults={"value": (value or "").strip(), "updated_by": user},
        )


class ImageGenJob(models.Model):
    """批量生图异步任务（MySQL 持久化 + Redis 热状态）。"""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_DEAD = "dead"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "等待中"),
        (STATUS_RUNNING, "执行中"),
        (STATUS_COMPLETED, "已完成"),
        (STATUS_FAILED, "失败"),
        (STATUS_DEAD, "死信(DLQ)"),
        (STATUS_CANCELLED, "已停止"),
    ]

    job_id = models.CharField(max_length=64, unique=True, db_index=True, verbose_name="任务 ID")
    celery_task_id = models.CharField(max_length=128, blank=True, default="", verbose_name="Celery 任务 ID")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="image_gen_jobs",
        verbose_name="用户",
    )
    asin = models.CharField(max_length=20, db_index=True, verbose_name="ASIN")
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name="状态",
    )
    queue_name = models.CharField(max_length=64, blank=True, default="", verbose_name="队列")
    priority = models.SmallIntegerField(default=0, verbose_name="优先级")
    batch_size = models.PositiveIntegerField(default=0, verbose_name="批次张数")
    added = models.PositiveIntegerField(default=0, verbose_name="成功张数")
    orig_pk = models.PositiveIntegerField(default=0, verbose_name="OriginalAsinData PK")
    user_notes = models.TextField(blank=True, default="", verbose_name="用户备注")
    job_specs_json = models.JSONField(default=list, blank=True, verbose_name="任务规格")
    errors_json = models.JSONField(default=list, blank=True, verbose_name="错误列表")
    error_message = models.TextField(blank=True, default="", verbose_name="错误信息")
    result_json = models.JSONField(null=True, blank=True, verbose_name="结果快照")
    retry_count = models.PositiveSmallIntegerField(default=0, verbose_name="重试次数")
    parent_job_id = models.CharField(max_length=64, blank=True, default="", db_index=True, verbose_name="来源任务")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "生图任务"
        verbose_name_plural = "生图任务"
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.job_id[:8]}… {self.asin} [{self.status}]"