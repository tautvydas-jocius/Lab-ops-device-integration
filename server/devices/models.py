import logging
from django.core.exceptions import ValidationError
from django.db import models
log = logging.getLogger(__name__)

class ParserSchema(models.Model):
    RULES = [("equals", "equals"), ("present", "present"), ("absent", "absent")]
    name = models.SlugField(max_length=80)
    version = models.PositiveIntegerField()
    description = models.TextField(blank=True)
    pattern = models.TextField(help_text="Regex with named groups value and unit; optional sign and stability groups")
    stable_group = models.CharField(max_length=80, blank=True)
    stable_rule = models.CharField(max_length=10, choices=RULES, default="present")
    stable_values = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["name", "version"], name="unique_parser_name_version")]
        ordering = ["name", "version"]
    def __str__(self): return f"{self.name} v{self.version}"
    def save(self, *args, **kwargs):
        if self.pk:
            old = ParserSchema.objects.filter(pk=self.pk).first()
            if old and any(getattr(old, f) != getattr(self, f) for f in ("pattern", "stable_group", "stable_rule", "stable_values")) and self.readings.exists():
                raise ValidationError("create a new version")
        super().save(*args, **kwargs)

class Device(models.Model):
    device_id = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    device_type = models.CharField(max_length=60, default="scale")
    manufacturer = models.CharField(max_length=120, blank=True)
    model = models.CharField(max_length=120, blank=True)
    location = models.CharField(max_length=120, blank=True)
    baud = models.PositiveIntegerField(default=9600)
    data_bits = models.PositiveSmallIntegerField(default=8)
    parity = models.CharField(max_length=1, default="N")
    stop_bits = models.PositiveSmallIntegerField(default=1)
    terminator = models.CharField(max_length=12, default="\r\n")
    poll_command = models.CharField(max_length=120, blank=True)
    poll_interval_ms = models.PositiveIntegerField(default=1000)
    enabled = models.BooleanField(default=True)
    parser = models.ForeignKey(ParserSchema, on_delete=models.PROTECT, related_name="devices")
    config_version = models.PositiveIntegerField(default=1)
    applied_config_version = models.PositiveIntegerField(null=True, blank=True)
    online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    CONFIG_FIELDS = ("baud", "data_bits", "parity", "stop_bits", "terminator", "poll_command", "poll_interval_ms", "enabled", "parser")
    def __str__(self): return f"{self.device_id} — {self.name}"
    def save(self, *args, **kwargs):
        if self.pk:
            old = Device.objects.filter(pk=self.pk).first()
            if old and any(getattr(old, f + "_id" if f == "parser" else f) != getattr(self, f + "_id" if f == "parser" else f) for f in self.CONFIG_FIELDS):
                self.config_version = old.config_version + 1
        super().save(*args, **kwargs)
        try:
            from .mqtt import publish_config
            publish_config(self)
        except Exception as exc:
            log.warning("Could not publish config for %s: %s", self.device_id, exc)

class Reading(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="readings")
    received_at = models.DateTimeField(auto_now_add=True)
    seq = models.IntegerField(null=True, blank=True)
    raw_line = models.TextField()
    parser = models.ForeignKey(ParserSchema, null=True, blank=True, on_delete=models.PROTECT, related_name="readings")
    ok = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    unit = models.CharField(max_length=32, blank=True)
    stable = models.BooleanField(null=True, blank=True)
    fields = models.JSONField(default=dict, blank=True)
    class Meta:
        indexes = [models.Index(fields=["device", "received_at"], name="reading_dev_received_idx")]
        ordering = ["-received_at"]
    def __str__(self): return f"{self.device.device_id} {self.received_at}"
