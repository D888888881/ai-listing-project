# Generated manually for AiListingGenerationHistory

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("myapp", "0010_listing_aux_editable_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="AiListingGenerationHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("asin", models.CharField(db_index=True, max_length=20, verbose_name="ASIN")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="生成时间")),
                (
                    "generated_by_username",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="生成时刻的用户名，用户删除后仍可显示",
                        max_length=150,
                        verbose_name="用户名快照",
                    ),
                ),
                ("keywords", models.JSONField(blank=True, default=list, verbose_name="关键词")),
                ("ask_rufus", models.JSONField(blank=True, default=dict, verbose_name="Ask Rufus")),
                ("voc_positioning", models.TextField(blank=True, default="", verbose_name="VOC定位(当时)")),
                ("negative_direction", models.TextField(blank=True, default="", verbose_name="差评改进方向(当时)")),
                ("cluster_suggestion", models.TextField(blank=True, default="", verbose_name="差异化建议(当时)")),
                ("material_supplement", models.TextField(blank=True, default="", verbose_name="材质与补充(当时)")),
                ("listing", models.TextField(blank=True, default="", verbose_name="Listing 输出")),
                (
                    "generated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ai_listing_generations",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="操作用户",
                    ),
                ),
            ],
            options={
                "verbose_name": "AI-Listing 生成历史",
                "verbose_name_plural": "AI-Listing 生成历史",
                "ordering": ["-created_at"],
            },
        ),
    ]
