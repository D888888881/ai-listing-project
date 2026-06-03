from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("myapp", "0012_originalasindata_owner_assign"),
    ]

    operations = [
        migrations.AddField(
            model_name="originalasindata",
            name="main_image_requirements",
            field=models.TextField(blank=True, default="", verbose_name="主图图需"),
        ),
        migrations.AddField(
            model_name="originalasindata",
            name="aplus_image_requirements",
            field=models.TextField(blank=True, default="", verbose_name="APlus图需"),
        ),
        migrations.AddField(
            model_name="originalasindata",
            name="original_images",
            field=models.JSONField(blank=True, default=list, verbose_name="原图"),
        ),
        migrations.AddField(
            model_name="originalasindata",
            name="finished_images",
            field=models.JSONField(blank=True, default=list, verbose_name="成品图"),
        ),
    ]
