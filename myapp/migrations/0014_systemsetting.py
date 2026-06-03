from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("myapp", "0013_originalasindata_ai_image_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="SystemSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=64, unique=True, verbose_name="配置键")),
                ("value", models.TextField(blank=True, default="", verbose_name="配置值")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="system_settings_updated",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="最后更新人",
                    ),
                ),
            ],
            options={
                "verbose_name": "系统配置",
                "verbose_name_plural": "系统配置",
            },
        ),
    ]
