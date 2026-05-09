"""Schema swap: drop the old UDP-report shape, recreate around CSV ingest.

DB is empty when this runs; no data migration needed.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("charging", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(name="ChargingSession"),
        migrations.CreateModel(
            name="ChargingSession",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("serial", models.CharField(max_length=32)),
                ("started_at", models.DateTimeField()),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("energy_kwh", models.DecimalField(decimal_places=3, max_digits=10)),
                ("raw_row", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-started_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="chargingsession",
            constraint=models.UniqueConstraint(
                fields=("serial", "started_at"),
                name="unique_session_per_wallbox",
            ),
        ),
    ]
