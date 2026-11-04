from django.contrib import admin
from .models import Device, DeviceCommand, DeviceEvent, DeviceHeartbeat, FirmwareImage, ParserSchema, UploadedFile, Reading
from .mqtt import send_command
from .diagnostics import rssi_label, aggregate
class RssiFilter(admin.SimpleListFilter):
    title = "Wi-Fi signal"; parameter_name = "rssi"
    BUCKETS = {"good": (-67, 0), "ok": (-75, -68), "weak": (-82, -76), "poor": (-200, -83)}
    def lookups(self, request, model_admin): return [("good", "good (>= -67 dBm)"), ("ok", "ok (-68..-75)"), ("weak", "weak (-76..-82)"), ("poor", "poor (< -82)"), ("none", "no data")]
    def queryset(self, request, qs):
        v = self.value()
        if v == "none": return qs.filter(rssi__isnull=True)
        if v in self.BUCKETS: lo, hi = self.BUCKETS[v]; return qs.filter(rssi__gte=lo, rssi__lte=hi)

@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("device_id", "name", "location", "parser", "enabled", "online", "rssi_dbm", "uptime", "last_reset", "fw_version", "last_seen", "config_version", "applied_config_version")
    list_filter = ("online", "location", "last_reset", RssiFilter, "enabled", "device_type")
    change_list_template = "admin/devices/device_changelist.html"
    search_fields = ("device_id", "name", "location")
    readonly_fields = ("command_token", "fw_version")
    @admin.display(description="RSSI", ordering="rssi")
    def rssi_dbm(self, d): return f"{d.rssi} dBm ({rssi_label(d.rssi)})" if d.rssi is not None else "-"
    @admin.display(description="Uptime", ordering="uptime_s")
    def uptime(self, d):
        if d.uptime_s is None: return "-"
        m, _ = divmod(d.uptime_s, 60); h, m = divmod(m, 60); dd, h = divmod(h, 24)
        return f"{dd}d {h}h" if dd else f"{h}h {m}m" if h else f"{m}m"
    def get_urls(self):
        from django.urls import path
        v = self.admin_site.admin_view
        return [path("diagnostics/", v(self.diagnostics), name="devices_diagnostics"), path("diagnostics/<slug:device_id>/", v(self.diagnostics_detail), name="devices_diagnostics_detail")] + super().get_urls()
    def diagnostics(self, request):
        from django.template.response import TemplateResponse
        days = int(request.GET["days"]) if request.GET.get("days", "").isdigit() and 0 < int(request.GET["days"]) <= 90 else 1
        rows = [{"d": d, "label": rssi_label(d.rssi)} for d in Device.objects.all()]
        sort = request.GET.get("sort", "name"); keys = {"rssi": lambda r: (r["d"].rssi is None, -(r["d"].rssi or 0)), "uptime": lambda r: r["d"].uptime_s or 0, "location": lambda r: (r["d"].location or "~", r["d"].device_id), "name": lambda r: r["d"].device_id}
        rows.sort(key=keys.get(sort, keys["name"]), reverse=request.GET.get("dir") == "desc")
        if request.GET.get("online") in ("1", "0"): rows = [r for r in rows if r["d"].online == (request.GET["online"] == "1")]
        if request.GET.get("location"): rows = [r for r in rows if r["d"].location == request.GET["location"]]
        ctx = dict(self.admin_site.each_context(request), title="Device diagnostics", rows=rows, sort=sort, days=days, agg=aggregate(DeviceEvent.objects.all(), days), opts=self.model._meta,
                   locations=sorted({d.location for d in Device.objects.all() if d.location}), n_online=sum(r["d"].online for r in rows), n_total=len(rows), q=request.GET)
        return TemplateResponse(request, "admin/devices/diagnostics.html", ctx)
    def diagnostics_detail(self, request, device_id):
        from django.template.response import TemplateResponse
        from django.shortcuts import get_object_or_404
        d = get_object_or_404(Device, device_id=device_id)
        hb = list(d.heartbeats.all()[:240])[::-1]; pts = [h.rssi for h in hb if h.rssi is not None]
        spark = ""
        if len(pts) > 1:
            lo, hi = min(pts), max(pts); span = max(hi - lo, 1)
            spark = " ".join(f"{i * 600 / (len(pts) - 1):.1f},{58 - (v - lo) * 52 / span:.1f}" for i, v in enumerate(pts))
        ctx = dict(self.admin_site.each_context(request), title=f"Diagnostics: {d.device_id}", d=d, events=d.events.all()[:60], spark=spark, rssi_min=min(pts, default=None), rssi_max=max(pts, default=None),
                   counters=sorted((d.diag_counters or {}).items()), opts=self.model._meta, label=rssi_label(d.rssi))
        return TemplateResponse(request, "admin/devices/diagnostics_detail.html", ctx)
    actions = ["ping", "poll_now", "request_diag", "reboot", "ota_newest", "ota_choose"]
    def _send(self, request, qs, action):
        for d in qs: send_command(d, action, by=request.user.get_username())
        self.message_user(request, f"{action} sent to {qs.count()} device(s); see Device commands for the ack.")
    @admin.action(description="Ping MCU")
    def ping(self, request, qs): self._send(request, qs, "ping")
    @admin.action(description="Poll instrument now")
    def poll_now(self, request, qs): self._send(request, qs, "poll_now")
    @admin.action(description="OTA: flash a chosen firmware version...")
    def ota_choose(self, request, qs):
        from django.template.response import TemplateResponse
        from django.http import HttpResponseRedirect
        if "apply" in request.POST:
            img = FirmwareImage.find(request.POST.get("image", ""))
            if not img: self.message_user(request, "pick a firmware version", level="error"); return None
            for d in qs: send_command(d, "ota", str(img.pk), by=request.user.get_username())
            self.message_user(request, f"OTA to v{img.version} sent to {qs.count()} device(s); watch Device commands for the result.")
            return HttpResponseRedirect(request.get_full_path())
        ctx = dict(self.admin_site.each_context(request), title="Choose firmware version", devices=qs, images=FirmwareImage.objects.all(), action="ota_choose", opts=self.model._meta)
        return TemplateResponse(request, "admin/devices/ota_choose.html", ctx)
    @admin.action(description="OTA: flash newest firmware image")
    def ota_newest(self, request, qs): self._send(request, qs, "ota")
    @admin.action(description="Request full diagnostics")
    def request_diag(self, request, qs): self._send(request, qs, "diag")
    @admin.action(description="Reboot MCU")
    def reboot(self, request, qs): self._send(request, qs, "reboot")
@admin.register(ParserSchema)
class ParserSchemaAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "description", "created_at")
    readonly_fields = ("created_at",)
@admin.register(Reading)
class ReadingAdmin(admin.ModelAdmin):
    list_display = ("device", "received_at", "value", "unit", "ok", "stable", "parser")
    list_filter = ("device", "ok", "stable")
    readonly_fields = tuple(f.name for f in Reading._meta.fields)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

@admin.register(DeviceCommand)
class DeviceCommandAdmin(admin.ModelAdmin):
    list_display = ("id", "device", "action", "arg", "status", "result", "took", "created_by", "created_at", "acked_at")
    list_filter = ("device", "action", "status")
    fields = ("device", "action", "arg")
    @admin.display(description="took (s)")
    def took(self, obj): return obj.took
    def get_readonly_fields(self, request, obj=None): return tuple(f.name for f in DeviceCommand._meta.fields) if obj else ()
    def get_fields(self, request, obj=None): return None if obj else self.fields
    def save_model(self, request, obj, form, change):
        if change: return
        cmd = send_command(obj.device, obj.action, obj.arg, request.user.get_username())
        obj.pk, obj.status, obj.result = cmd.pk, cmd.status, cmd.result
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    list_display = ("id", "device", "name", "size", "sha256", "received_at")
    list_filter = ("device",)
    readonly_fields = tuple(f.name for f in UploadedFile._meta.fields)
    def has_add_permission(self, request): return False

@admin.register(FirmwareImage)
class FirmwareImageAdmin(admin.ModelAdmin):
    list_display = ("version", "size", "sha256", "uploaded_at", "notes")
    readonly_fields = ("size", "sha256")
    def get_readonly_fields(self, request, obj=None): return ("size", "sha256", "version", "file") if obj else ("size", "sha256")

@admin.register(DeviceEvent)
class DeviceEventAdmin(admin.ModelAdmin):
    list_display = ("received_at", "device", "kind", "detail", "boot_no", "uptime_s")
    list_filter = ("kind", "device__location", "device", "received_at")
    date_hierarchy = "received_at"
    search_fields = ("detail",)
    readonly_fields = tuple(f.name for f in DeviceEvent._meta.fields)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
