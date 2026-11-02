from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="ParserSchema",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.SlugField(max_length=80)),
                ("version", models.PositiveIntegerField()),
                ("description", models.TextField(blank=True)),
                ("pattern", models.TextField(help_text="Regex with named groups value and unit; optional sign and stability groups")),
                ("stable_group", models.CharField(blank=True, max_length=80)),
                ("stable_rule", models.CharField(choices=[("equals", "equals"), ("present", "present"), ("absent", "absent")], default="present", max_length=10)),
                ("stable_values", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["name", "version"]},
        ),
        migrations.CreateModel(
            name="Device",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("device_id", models.SlugField(max_length=80, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("device_type", models.CharField(default="scale", max_length=60)),
                ("manufacturer", models.CharField(blank=True, max_length=120)),
                ("model", models.CharField(blank=True, max_length=120)),
                ("location", models.CharField(blank=True, max_length=120)),
                ("baud", models.PositiveIntegerField(default=9600)),
                ("data_bits", models.PositiveSmallIntegerField(default=8)),
                ("parity", models.CharField(default="N", max_length=1)),
                ("stop_bits", models.PositiveSmallIntegerField(default=1)),
                ("terminator", models.CharField(default="\r\n", max_length=12)),
                ("poll_command", models.CharField(blank=True, max_length=120)),
                ("poll_interval_ms", models.PositiveIntegerField(default=1000)),
                ("enabled", models.BooleanField(default=True)),
                ("config_version", models.PositiveIntegerField(default=1)),
                ("applied_config_version", models.PositiveIntegerField(blank=True, null=True)),
                ("online", models.BooleanField(default=False)),
                ("last_seen", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("parser", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="devices", to="devices.parserschema")),
            ],
        ),
        migrations.CreateModel(
            name="Reading",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("seq", models.IntegerField(blank=True, null=True)),
                ("raw_line", models.TextField()),
                ("ok", models.BooleanField(default=False)),
                ("error", models.TextField(blank=True)),
                ("value", models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True)),
                ("unit", models.CharField(blank=True, max_length=32)),
                ("stable", models.BooleanField(blank=True, null=True)),
                ("fields", models.JSONField(blank=True, default=dict)),
                ("device", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="readings", to="devices.device")),
                ("parser", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="readings", to="devices.parserschema")),
            ],
            options={"ordering": ["-received_at"]},
        ),
        migrations.AddConstraint(model_name="parserschema", constraint=models.UniqueConstraint(fields=("name", "version"), name="unique_parser_name_version")),
        migrations.AddIndex(model_name="reading", index=models.Index(fields=["device", "received_at"], name="reading_dev_received_idx")),
    ]
