# Generated for Phase 15 multi-currency support.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("supplier_invoices", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="supplierinvoice",
            name="currency",
            field=models.CharField(choices=[("USD", "US Dollar"), ("EGP", "Egyptian Pound")], max_length=3),
        ),
        migrations.AddField(
            model_name="supplierinvoiceline",
            name="currency",
            field=models.CharField(choices=[("USD", "US Dollar"), ("EGP", "Egyptian Pound")], default="USD", max_length=3),
            preserve_default=False,
        ),
    ]
