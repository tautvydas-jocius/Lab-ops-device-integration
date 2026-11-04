import logging, secrets
from django.core.exceptions import ValidationError
from django.db import models
log = logging.getLogger(__name__)

def new_token(): return secrets.token_hex(24)

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
    fw_version = models.CharField(max_length=40, blank=True, help_text="Reported by the MCU in its status message")
    # latest diagnostics snapshot (from the MCU heartbeat on <prefix>/<id>/diag); history lives in DeviceHeartbeat / DeviceEvent
    rssi = models.SmallIntegerField(null=True, blank=True, editable=False)
    uptime_s = models.PositiveIntegerField(null=True, blank=True, editable=False)
    free_heap = models.PositiveIntegerField(null=True, blank=True, editable=False)
    min_free_heap = models.PositiveIntegerField(null=True, blank=True, editable=False)
    boot_no = models.PositiveIntegerField(null=True, blank=True, editable=False)
    last_reset = models.CharField(max_length=60, blank=True, editable=False)
    diag_counters = models.JSONField(default=dict, blank=True, editable=False)
    diag_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_seen = models.DateTimeField(null=True, blank=True)
    command_token = models.CharField(max_length=64, default=new_token, editable=False, help_text="Shared secret; put in firmware secrets.h as COMMAND_TOKEN. Never sent over MQTT, only used to HMAC-sign commands.")
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


class DeviceCommand(models.Model):
    ACTIONS = [("ping", "Ping"), ("poll_now", "Poll now"), ("send_serial", "Send serial text"), ("reboot", "Reboot MCU"), ("set_wifi", "Set Wi-Fi credentials"), ("reset_config", "Reset device config"), ("factory_reset", "Factory reset (config + Wi-Fi)"), ("ota", "OTA firmware update"), ("diag", "Request full diagnostics")]
    STATUS = [("pending", "pending"), ("ok", "ok"), ("failed", "failed")]
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="commands")
    action = models.CharField(max_length=20, choices=ACTIONS)
    arg = models.CharField(max_length=200, blank=True, help_text="send_serial: text written to the instrument (terminator is NOT added). ota: firmware image id (blank = newest); set_wifi: JSON {\"ssid\":\"..\",\"pass\":\"..\"}; password is not stored here")
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    result = models.TextField(blank=True)
    created_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    acked_at = models.DateTimeField(null=True, blank=True)
    class Meta: ordering = ["-id"]
    def __str__(self): return f"{self.device.device_id} {self.action} #{self.pk}"
    @property
    def took(self):
        """Seconds from the command being created to the MCU's ack (for OTA this is the whole transfer + flash)."""
        return round((self.acked_at - self.created_at).total_seconds(), 2) if self.acked_at else None


class UploadedFile(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="files")
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to="device_files/%Y/%m/")
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    received_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-id"]
    def __str__(self): return f"{self.device.device_id}/{self.name}"


class FirmwareImage(models.Model):
    version = models.CharField(max_length=40, unique=True, blank=True, help_text="Leave blank: read from the binary's LABOPS_FW marker (set by FW_VERSION at build time), else auto-numbered")
    file = models.FileField(upload_to="firmware/", help_text="PlatformIO build output: .pio/build/<env>/firmware.bin")
    size = models.PositiveIntegerField(editable=False, default=0)
    sha256 = models.CharField(max_length=64, editable=False, blank=True)
    notes = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-id"]
    def __str__(self): return f"firmware {self.version}"
    @classmethod
    def find(cls, ref):
        """Resolve an id, a version (\"1.2.0\" or \"v1.2.0\"), or blank (= newest)."""
        ref = (ref or "").strip()
        if not ref: return cls.objects.first()
        return cls.objects.filter(version__in=[ref, ref.lstrip("vV"), "v" + ref.lstrip("vV")]).first() or (cls.objects.filter(pk=ref).first() if ref.isdigit() else None)
    def save(self, *args, **kwargs):
        if not self.sha256:  # computed once; images are immutable once stored
            import hashlib, re
            h = hashlib.sha256(); self.file.open("rb"); found = None
            for chunk in self.file.chunks():
                h.update(chunk)
                if not found: found = re.search(rb"LABOPS_FW=([A-Za-z0-9._+-]{1,40})", chunk)  # marker embedded by firmware/src/main.cpp (ponytail: a marker split across a 64 KB chunk edge is missed; falls back to auto-number)
            self.file.seek(0); self.sha256, self.size = h.hexdigest(), self.file.size
            if not self.version:
                from django.utils import timezone
                self.version = found.group(1).decode() if found else f"auto-{timezone.now():%Y%m%d}.{FirmwareImage.objects.count() + 1}"
        super().save(*args, **kwargs)


class DeviceHeartbeat(models.Model):
    """One row per MCU heartbeat (default every 30 s). Pruned after HEARTBEAT_KEEP_DAYS by the listener."""
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="heartbeats")
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    rssi = models.SmallIntegerField(null=True)
    uptime_s = models.PositiveIntegerField(null=True)
    free_heap = models.PositiveIntegerField(null=True)
    class Meta:
        ordering = ["-at"]
        indexes = [models.Index(fields=["device", "-at"], name="hb_dev_at_idx")]

class DeviceEvent(models.Model):
    """Persistent MCU event (boot reason, Wi-Fi drop, MQTT drop...). The MCU keeps the last 16 in NVS and replays them, so
    (device, bid, n) de-duplicates: bid = random id of the boot the event happened in, n = event counter."""
    KINDS = ["boot", "wifi_drop", "mqtt_drop", "mqtt_fail", "other"]
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="events")
    n = models.PositiveIntegerField()
    bid = models.CharField(max_length=16)
    boot_no = models.PositiveIntegerField(default=0)
    uptime_s = models.PositiveIntegerField(default=0)
    kind = models.CharField(max_length=30)
    detail = models.CharField(max_length=200, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-id"]
        constraints = [models.UniqueConstraint(fields=["device", "bid", "n"], name="unique_device_event")]
