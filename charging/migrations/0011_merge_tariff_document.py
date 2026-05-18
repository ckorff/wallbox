"""Fold TariffDocument into Tariff.

A tariff and its supplier-PDF evidence are a single concept; the
Phase 3.1 split into two tables only existed for one commit. This
migration:

1. Adds ``pdf``, ``provider_name``, ``notes`` to ``Tariff``.
2. Copies each ``TariffDocument`` into the ``Tariff`` row sharing its
   ``valid_from``. Orphans (no matching tariff) are dropped — those
   would have shipped reports without a price anyway, so there is
   nothing to preserve.
3. Drops the now-empty ``TariffDocument`` table.
"""
from django.db import migrations, models


def copy_documents_into_tariffs(apps, schema_editor):
    Tariff = apps.get_model("charging", "Tariff")
    TariffDocument = apps.get_model("charging", "TariffDocument")
    for doc in TariffDocument.objects.all():
        tariff = Tariff.objects.filter(valid_from=doc.valid_from).first()
        if tariff is None:
            continue
        tariff.pdf = doc.pdf
        tariff.provider_name = doc.provider_name
        tariff.notes = doc.notes
        tariff.save(update_fields=["pdf", "provider_name", "notes"])


class Migration(migrations.Migration):

    dependencies = [
        ("charging", "0010_tariffdocument"),
    ]

    operations = [
        migrations.AddField(
            model_name="tariff",
            name="pdf",
            field=models.FileField(blank=True, upload_to="tariff_documents/"),
        ),
        migrations.AddField(
            model_name="tariff",
            name="provider_name",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="tariff",
            name="notes",
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(
            copy_documents_into_tariffs,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.DeleteModel(
            name="TariffDocument",
        ),
    ]
