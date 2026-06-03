# Generated manually for VOC-only three-part analysis overview

from django.db import migrations, models


def delete_legacy_analysis_details(apps, schema_editor):
    AnalysisDetail = apps.get_model("myapp", "AnalysisDetail")
    AnalysisDetail.objects.filter(
        category__in=["rufus", "negative", "voc", "suggestions", "refus"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("myapp", "0008_originalasindata_voc_cluster"),
    ]

    operations = [
        migrations.RunPython(delete_legacy_analysis_details, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="analysisdetail",
            name="category",
            field=models.CharField(
                choices=[
                    ("benchmark", "对标 ASIN 分析"),
                    ("cluster", "ASIN 集群分析"),
                    ("differentiation", "差异化分析"),
                ],
                max_length=20,
                verbose_name="维度类别",
            ),
        ),
    ]
