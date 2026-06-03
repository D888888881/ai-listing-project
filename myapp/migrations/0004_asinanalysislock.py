from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("myapp", "0003_userprofile"),
    ]

    operations = [
        migrations.CreateModel(
            name="AsinAnalysisLock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("asin", models.CharField(max_length=20, unique=True, verbose_name="ASIN")),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                (
                    "started_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="gpt_analysis_locks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "ASIN GPT 分析锁",
                "verbose_name_plural": "ASIN GPT 分析锁",
            },
        ),
    ]

