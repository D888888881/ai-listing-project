from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("myapp", "0016_imagegenjob_cancelled_status"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="originalasindata",
            index=models.Index(fields=["created_by", "-updated_at"], name="myapp_origi_created_2c47cc_idx"),
        ),
        migrations.AddIndex(
            model_name="originalasindata",
            index=models.Index(fields=["assigned_to", "-updated_at"], name="myapp_origi_assigne_04df35_idx"),
        ),
        migrations.AddIndex(
            model_name="ailistinggenerationhistory",
            index=models.Index(fields=["asin", "-created_at"], name="myapp_ailis_asin_aa44cb_idx"),
        ),
        migrations.AddIndex(
            model_name="ailistinggenerationhistory",
            index=models.Index(fields=["generated_by", "-created_at"], name="myapp_ailis_generat_90c7eb_idx"),
        ),
        migrations.AddIndex(
            model_name="imagegenjob",
            index=models.Index(fields=["user", "status", "-created_at"], name="myapp_image_user_id_8ed1d1_idx"),
        ),
        migrations.AddIndex(
            model_name="imagegenjob",
            index=models.Index(fields=["orig_pk", "-created_at"], name="myapp_image_orig_pk_6d23c9_idx"),
        ),
        migrations.AddIndex(
            model_name="imagegenjob",
            index=models.Index(fields=["status", "-updated_at"], name="myapp_image_status_913e99_idx"),
        ),
    ]
