from django.contrib import admin

from .models import ImageGenJob


@admin.register(ImageGenJob)
class ImageGenJobAdmin(admin.ModelAdmin):
    list_display = (
        "job_id",
        "asin",
        "user",
        "status",
        "queue_name",
        "batch_size",
        "added",
        "retry_count",
        "created_at",
    )
    list_filter = ("status", "queue_name")
    search_fields = ("job_id", "asin", "celery_task_id")
    readonly_fields = (
        "job_id",
        "celery_task_id",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
    )
    ordering = ("-created_at",)
