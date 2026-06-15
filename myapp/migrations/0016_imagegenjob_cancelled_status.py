from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("myapp", "0015_imagegenjob"),
    ]

    operations = [
        migrations.AlterField(
            model_name="imagegenjob",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "等待中"),
                    ("running", "执行中"),
                    ("completed", "已完成"),
                    ("failed", "失败"),
                    ("dead", "死信(DLQ)"),
                    ("cancelled", "已停止"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
                verbose_name="状态",
            ),
        ),
    ]
