from django.contrib import admin
from .models import Device, DeviceCommand, ParserSchema, UploadedFile, Reading
from .mqtt import send_command
@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("device_id", "name", "location", "parser", "enabled", "online", "last_seen", "config_version", "applied_config_version")
    list_filter = ("enabled", "online", "device_type")
    search_fields = ("device_id", "name", "location")
    readonly_fields = ("command_token",)
    actions = ["ping", "poll_now", "reboot"]
    def _send(self, request, qs, action):
        for d in qs: send_command(d, action, by=request.user.get_username())
        self.message_user(request, f"{action} sent to {qs.count()} device(s); see Device commands for the ack.")
    @admin.action(description="Ping MCU")
    def ping(self, request, qs): self._send(request, qs, "ping")
    @admin.action(description="Poll instrument now")
    def poll_now(self, request, qs): self._send(request, qs, "poll_now")
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
    list_display = ("id", "device", "action", "arg", "status", "result", "created_by", "created_at", "acked_at")
    list_filter = ("device", "action", "status")
    fields = ("device", "action", "arg")
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
