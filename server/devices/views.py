from django.http import JsonResponse, Http404
from .models import Device, Reading
from .mqtt import config_payload

def device_json(d):
    return {"device_id": d.device_id, "name": d.name, "device_type": d.device_type, "manufacturer": d.manufacturer, "model": d.model, "location": d.location, "enabled": d.enabled, "online": d.online, "last_seen": d.last_seen, "parser": {"name": d.parser.name, "version": d.parser.version}, "config_version": d.config_version, "applied_config_version": d.applied_config_version}
def devices(request): return JsonResponse({"devices": [device_json(d) for d in Device.objects.select_related("parser").all()]})
def device_detail(request, device_id):
    try: d = Device.objects.select_related("parser").get(device_id=device_id)
    except Device.DoesNotExist: raise Http404
    data = device_json(d); data["config"] = config_payload(d)
    return JsonResponse(data)
def readings(request, device_id):
    try: d = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist: raise Http404
    try: limit = min(max(int(request.GET.get("limit", "50")), 1), 500)
    except ValueError: limit = 50
    qs = d.readings.select_related("parser")
    if request.GET.get("stable") == "1": qs = qs.filter(stable=True)
    rows = [{"received_at": x.received_at, "seq": x.seq, "raw_line": x.raw_line, "parser": f"{x.parser.name} v{x.parser.version}" if x.parser else None, "ok": x.ok, "error": x.error, "value": str(x.value) if x.value is not None else None, "unit": x.unit, "stable": x.stable, "fields": x.fields} for x in qs[:limit]]
    return JsonResponse({"device_id": device_id, "readings": rows})
