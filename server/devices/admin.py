from django.contrib import admin
from .models import Device, ParserSchema, Reading
@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("device_id", "name", "location", "parser", "enabled", "online", "last_seen", "config_version", "applied_config_version")
    list_filter = ("enabled", "online", "device_type")
    search_fields = ("device_id", "name", "location")
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
