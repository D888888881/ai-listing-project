# Generated manually for ASIN ownership / assignment

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("myapp", "0011_aigenerationhistory"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="originalasindata",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="uploaded_original_asins",
                to=settings.AUTH_USER_MODEL,
                verbose_name="上传用户",
            ),
        ),
        migrations.AddField(
            model_name="originalasindata",
            name="assigned_to",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assigned_original_asins",
                to=settings.AUTH_USER_MODEL,
                verbose_name="分配给",
            ),
        ),
    ]
