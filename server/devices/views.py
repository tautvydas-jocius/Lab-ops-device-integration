import hmac, json
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, Http404
from django.shortcuts import render
from django.core.files.base import ContentFile
from django.http import FileResponse
from django.utils.text import get_valid_filename
from .models import FirmwareImage, UploadedFile, Device, DeviceCommand, Reading, ParserSchema
from .mqtt import config_payload, send_command

def is_stalled(d):
    # enabled device marked online but silent for > max(5 poll intervals, 30 s)
    if not (d.enabled and d.online and d.last_seen): return False
    return timezone.now() - d.last_seen > timedelta(seconds=max(5 * d.poll_interval_ms / 1000, 30))

def device_json(d):
    return {
        "stalled": is_stalled(d),
        "device_id": d.device_id,
        "name": d.name,
        "device_type": d.device_type,
        "manufacturer": d.manufacturer,
        "model": d.model,
        "location": d.location,
        "enabled": d.enabled,
        "online": d.online,
        "last_seen": d.last_seen.isoformat() if d.last_seen else None,
        "parser": {
            "name": d.parser.name,
            "version": d.parser.version,
            "pattern": d.parser.pattern,
            "stable_group": d.parser.stable_group,
            "stable_rule": d.parser.stable_rule,
            "stable_values": d.parser.stable_values,
            "description": d.parser.description,
        },
        "config_version": d.config_version,
        "applied_config_version": d.applied_config_version,
        "baud": d.baud,
        "data_bits": d.data_bits,
        "parity": d.parity,
        "stop_bits": d.stop_bits,
        "terminator": repr(d.terminator)[1:-1],
        "poll_command": repr(d.poll_command)[1:-1] if d.poll_command else "",
        "poll_interval_ms": d.poll_interval_ms,
    }

def devices(request):
    return JsonResponse({"devices": [device_json(d) for d in Device.objects.select_related("parser").all()]})

def device_detail(request, device_id):
    try:
        d = Device.objects.select_related("parser").get(device_id=device_id)
    except Device.DoesNotExist:
        raise Http404
    data = device_json(d)
    data["config"] = config_payload(d)
    return JsonResponse(data)

def readings(request, device_id):
    try:
        d = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        raise Http404
    try:
        limit = min(max(int(request.GET.get("limit", "50")), 1), 500)
    except ValueError:
        limit = 50
    qs = d.readings.select_related("parser")
    if request.GET.get("stable") == "1":
        qs = qs.filter(stable=True)
    rows = [
        {
            "received_at": x.received_at.isoformat() if x.received_at else None,
            "seq": x.seq,
            "raw_line": x.raw_line,
            "parser": f"{x.parser.name} v{x.parser.version}" if x.parser else None,
            "ok": x.ok,
            "error": x.error,
            "value": str(x.value) if x.value is not None else None,
            "unit": x.unit,
            "stable": x.stable,
            "fields": x.fields,
        }
        for x in qs[:limit]
    ]
    return JsonResponse({"device_id": device_id, "readings": rows})

def api_overview(request):
    devices_qs = Device.objects.select_related("parser").all()
    devices_list = []
    for d in devices_qs:
        dj = device_json(d)
        device_readings = list(d.readings.select_related("parser").order_by("-received_at")[:10])
        if device_readings:
            latest = device_readings[0]
            dj["latest_reading"] = {
                "received_at": latest.received_at.isoformat() if latest.received_at else None,
                "value": str(latest.value) if latest.value is not None else None,
                "unit": latest.unit,
                "stable": latest.stable,
                "raw_line": latest.raw_line,
                "ok": latest.ok,
                "seq": latest.seq,
                "parser": f"{latest.parser.name} v{latest.parser.version}" if latest.parser else None,
            }
            dj["recent_values"] = [
                float(r.value) for r in reversed(device_readings) if r.value is not None
            ]
        else:
            dj["latest_reading"] = None
            dj["recent_values"] = []
        devices_list.append(dj)

    recent_readings_qs = Reading.objects.select_related("device", "parser").order_by("-received_at")[:50]
    recent_readings = [
        {
            "id": r.id,
            "device_id": r.device.device_id,
            "device_name": r.device.name,
            "received_at": r.received_at.isoformat() if r.received_at else None,
            "seq": r.seq,
            "raw_line": r.raw_line,
            "parser": f"{r.parser.name} v{r.parser.version}" if r.parser else None,
            "ok": r.ok,
            "error": r.error,
            "value": str(r.value) if r.value is not None else None,
            "unit": r.unit,
            "stable": r.stable,
            "fields": r.fields,
        }
        for r in recent_readings_qs
    ]

    total_devices = len(devices_list)
    online_devices = sum(1 for d in devices_list if d["online"])
    total_readings = Reading.objects.count()
    parsed_ok = Reading.objects.filter(ok=True).count()
    schemas_qs = ParserSchema.objects.all()
    schemas_list = [
        {
            "name": s.name,
            "version": s.version,
            "description": s.description,
            "pattern": s.pattern,
            "stable_group": s.stable_group,
            "stable_rule": s.stable_rule,
            "stable_values": s.stable_values,
        }
        for s in schemas_qs
    ]

    return JsonResponse({
        "stats": {
            "total_devices": total_devices,
            "online_devices": online_devices,
            "total_readings": total_readings,
            "parsed_ok": parsed_ok,
            "parse_ok_percentage": round((parsed_ok / total_readings * 100), 1) if total_readings > 0 else 0,
            "schemas_count": len(schemas_list),
            "mqtt_host": getattr(settings, "MQTT_HOST", "mosquitto"),
            "mqtt_port": getattr(settings, "MQTT_PORT", 1883),
            "mqtt_prefix": getattr(settings, "MQTT_PREFIX", "lab/devices"),
        },
        "devices": devices_list,
        "schemas": schemas_list,
        "recent_readings": recent_readings,
    })

def dashboard(request):
    overview_response = api_overview(request)
    overview_data = json.loads(overview_response.content.decode("utf-8"))
    return render(request, "devices/dashboard.html", {
        "overview": overview_data,
        "overview_json": json.dumps(overview_data),
    })

def cmd_json(c): return {"id": c.pk, "action": c.action, "arg": c.arg, "status": c.status, "result": c.result, "took_s": c.took, "created_at": c.created_at, "acked_at": c.acked_at}

@csrf_exempt
def actions(request, device_id):
    # Bearer-token auth; disabled (403) when CONTROL_API_TOKEN is unset
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not settings.CONTROL_API_TOKEN or not hmac.compare_digest(supplied, settings.CONTROL_API_TOKEN):
        return JsonResponse({"error": "unauthorized"}, status=401 if settings.CONTROL_API_TOKEN else 403)
    try: d = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist: raise Http404
    if request.method == "GET": return JsonResponse({"commands": [cmd_json(c) for c in d.commands.all()[:50]]})
    if request.method != "POST": return JsonResponse({"error": "method not allowed"}, status=405)
    try: body = json.loads(request.body or b"{}")
    except ValueError: return JsonResponse({"error": "bad JSON"}, status=400)
    action = body.get("action")
    if action not in dict(DeviceCommand.ACTIONS): return JsonResponse({"error": "unknown action", "allowed": [a for a, _ in DeviceCommand.ACTIONS]}, status=400)
    return JsonResponse(cmd_json(send_command(d, action, str(body.get("arg", ""))[:200], by="api")), status=202)

def bearer(request): return request.headers.get("Authorization", "").removeprefix("Bearer ").strip()

def file_list(request, device_id):
    if not settings.CONTROL_API_TOKEN or not hmac.compare_digest(bearer(request), settings.CONTROL_API_TOKEN): return JsonResponse({"error": "unauthorized"}, status=401)
    qs = UploadedFile.objects.filter(device__device_id=device_id)[:100]
    return JsonResponse({"files": [{"id": f.pk, "name": f.name, "size": f.size, "sha256": f.sha256, "received_at": f.received_at, "download": f"/api/files/{f.pk}/"} for f in qs]})

def file_download(request, pk):
    if not settings.CONTROL_API_TOKEN or not hmac.compare_digest(bearer(request), settings.CONTROL_API_TOKEN): return JsonResponse({"error": "unauthorized"}, status=401)
    try: f = UploadedFile.objects.get(pk=pk)
    except UploadedFile.DoesNotExist: raise Http404
    return FileResponse(f.file.open("rb"), as_attachment=True, filename=f.name)

def firmware_list(request):
    if not settings.CONTROL_API_TOKEN or not hmac.compare_digest(bearer(request), settings.CONTROL_API_TOKEN): return JsonResponse({"error": "unauthorized"}, status=401)
    return JsonResponse({"firmware": [{"id": f.pk, "version": f.version, "size": f.size, "sha256": f.sha256, "uploaded_at": f.uploaded_at, "notes": f.notes, "download": f"/api/firmware/{f.pk}/"} for f in FirmwareImage.objects.all()]})

def firmware_download(request, pk):
    if not settings.CONTROL_API_TOKEN or not hmac.compare_digest(bearer(request), settings.CONTROL_API_TOKEN): return JsonResponse({"error": "unauthorized"}, status=401)
    try: f = FirmwareImage.objects.get(pk=pk)
    except FirmwareImage.DoesNotExist: raise Http404
    return FileResponse(f.file.open("rb"), as_attachment=True, filename=f"firmware-{f.version}.bin")

def diagnostics_api(request):
    """Data only (no scoring). ?sort=name|rssi|uptime|location  ?days=N adds event aggregates for the last N days."""
    if not settings.CONTROL_API_TOKEN or not hmac.compare_digest(bearer(request), settings.CONTROL_API_TOKEN): return JsonResponse({"error": "unauthorized"}, status=401)
    from .diagnostics import rssi_label, aggregate
    from .models import DeviceEvent
    out = [{"device_id": d.device_id, "location": d.location, "online": d.online, "rssi": d.rssi, "rssi_label": rssi_label(d.rssi), "uptime_s": d.uptime_s, "free_heap": d.free_heap,
            "min_free_heap": d.min_free_heap, "boot_no": d.boot_no, "last_reset": d.last_reset, "counters": d.diag_counters, "last_diag": d.diag_at, "fw_version": d.fw_version} for d in Device.objects.all()]
    sort = request.GET.get("sort", "name")
    out.sort(key={"rssi": lambda x: (x["rssi"] is None, -(x["rssi"] or 0)), "uptime": lambda x: x["uptime_s"] or 0, "location": lambda x: x["location"] or "~"}.get(sort, lambda x: x["device_id"]))
    body = {"devices": out}
    if request.GET.get("days", "").isdigit(): body["aggregate"] = aggregate(DeviceEvent.objects.all(), int(request.GET["days"]))
    return JsonResponse(body)
