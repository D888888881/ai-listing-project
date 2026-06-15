from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("myapp", "0014_systemsetting"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ImageGenJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("job_id", models.CharField(db_index=True, max_length=64, unique=True, verbose_name="任务 ID")),
                ("celery_task_id", models.CharField(blank=True, default="", max_length=128, verbose_name="Celery 任务 ID")),
                ("asin", models.CharField(db_index=True, max_length=20, verbose_name="ASIN")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "等待中"),
                            ("running", "执行中"),
                            ("completed", "已完成"),
                            ("failed", "失败"),
                            ("dead", "死信(DLQ)"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("queue_name", models.CharField(blank=True, default="", max_length=64, verbose_name="队列")),
                ("priority", models.SmallIntegerField(default=0, verbose_name="优先级")),
                ("batch_size", models.PositiveIntegerField(default=0, verbose_name="批次张数")),
                ("added", models.PositiveIntegerField(default=0, verbose_name="成功张数")),
                ("orig_pk", models.PositiveIntegerField(default=0, verbose_name="OriginalAsinData PK")),
                ("user_notes", models.TextField(blank=True, default="", verbose_name="用户备注")),
                ("job_specs_json", models.JSONField(blank=True, default=list, verbose_name="任务规格")),
                ("errors_json", models.JSONField(blank=True, default=list, verbose_name="错误列表")),
                ("error_message", models.TextField(blank=True, default="", verbose_name="错误信息")),
                ("result_json", models.JSONField(blank=True, null=True, verbose_name="结果快照")),
                ("retry_count", models.PositiveSmallIntegerField(default=0, verbose_name="重试次数")),
                ("parent_job_id", models.CharField(blank=True, db_index=True, default="", max_length=64, verbose_name="来源任务")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="image_gen_jobs",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="用户",
                    ),
                ),
            ],
            options={
                "verbose_name": "生图任务",
                "verbose_name_plural": "生图任务",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["status", "-created_at"], name="myapp_image_status_created_idx"),
                    models.Index(fields=["user", "-created_at"], name="myapp_image_user_created_idx"),
                ],
            },
        ),
    ]
