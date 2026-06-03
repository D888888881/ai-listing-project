from django.db import migrations


def forwards(apps, schema_editor):
    AnalysisDetail = apps.get_model("myapp", "AnalysisDetail")
    AnalysisDetail.objects.filter(category="refus").update(category="rufus")


def backwards(apps, schema_editor):
    AnalysisDetail = apps.get_model("myapp", "AnalysisDetail")
    AnalysisDetail.objects.filter(category="rufus").update(category="refus")


class Migration(migrations.Migration):

    dependencies = [
        ("myapp", "0004_asinanalysislock"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

