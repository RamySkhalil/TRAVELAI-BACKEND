# Generated for Phase 15 multi-currency support.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ticket_versions", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ticketversion",
            name="currency",
            field=models.CharField(choices=[("USD", "US Dollar"), ("EGP", "Egyptian Pound")], max_length=3),
        ),
    ]
