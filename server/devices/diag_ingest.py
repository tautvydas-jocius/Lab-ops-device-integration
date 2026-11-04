from datetime import timedelta
from django.utils import timezone
from .models import Device, DeviceEvent, DeviceHeartbeat

HEARTBEAT_KEEP_DAYS = 7

def parse_event(raw):
    """MCU event string: n|bid|boot|uptime_s|kind|detail"""
    p = str(raw).split("|", 5)
    if len(p) < 5 or not p[0].isdigit(): return None
    return {"n": int(p[0]), "bid": p[1][:16], "boot_no": int(p[2] or 0), "uptime_s": int(p[3] or 0), "kind": p[4][:30], "detail": (p[5] if len(p) > 5 else "")[:200]}

def ingest_diag(device, d):
    c = d.get("c") or {}
    Device.objects.filter(pk=device.pk).update(
        rssi=d.get("rssi"), uptime_s=d.get("up"), free_heap=d.get("heap"), min_free_heap=d.get("minheap"), boot_no=d.get("boot"),
        last_reset=str(d.get("reason", ""))[:60], diag_counters=c if isinstance(c, dict) else {}, diag_at=timezone.now())
    DeviceHeartbeat.objects.create(device=device, rssi=d.get("rssi"), uptime_s=d.get("up"), free_heap=d.get("heap"))
    new = 0
    for raw in d.get("ev") or []:
        e = parse_event(raw)
        if e and not DeviceEvent.objects.filter(device=device, bid=e["bid"], n=e["n"]).exists():
            DeviceEvent.objects.create(device=device, **e); new += 1
    if DeviceHeartbeat.objects.filter(device=device).count() % 200 == 0:  # prune now and then, not on every beat
        DeviceHeartbeat.objects.filter(at__lt=timezone.now() - timedelta(days=HEARTBEAT_KEEP_DAYS)).delete()
    return new
