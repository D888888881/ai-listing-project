from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("myapp", "0007_originalasindata_asin_cluster"),
    ]

    operations = [
        migrations.AddField(
            model_name="originalasindata",
            name="voc_cluster",
            field=models.JSONField(
                blank=True,
                default=dict,
                verbose_name="VOC 集群(仅对标行展示)",
            ),
        ),
    ]
