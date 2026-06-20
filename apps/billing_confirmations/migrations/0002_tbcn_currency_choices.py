# Generated for Phase 15 multi-currency support.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing_confirmations", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="travelbillingconfirmationnote",
            name="currency",
            field=models.CharField(choices=[("USD", "US Dollar"), ("EGP", "Egyptian Pound")], max_length=3),
        ),
    ]
