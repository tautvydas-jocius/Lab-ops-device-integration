"""Plain diagnostics data: signal bands and event aggregates. No scoring or ranking; the numbers link through to the raw event log."""
from datetime import timedelta
from django.db.models import Count
from django.utils import timezone

def rssi_label(r):
    if r is None: return "unknown"
    return "good" if r >= -67 else "ok" if r >= -75 else "weak" if r >= -82 else "poor"

def boot_reason(detail):
    """'brownout prev_uptime=61s' -> 'brownout'"""
    return (detail or "").split(" ", 1)[0] or "unknown"

def aggregate(events_qs, days=1, now=None):
    """Counts over the last `days` days: by event kind, boot reason, device and location. events_qs: DeviceEvent queryset."""
    since = (now or timezone.now()) - timedelta(days=days)
    qs = events_qs.filter(received_at__gte=since)
    kinds = {r["kind"]: r["n"] for r in qs.values("kind").annotate(n=Count("id"))}
    reasons = {}
    for detail in qs.filter(kind="boot").values_list("detail", flat=True):
        r = boot_reason(detail); reasons[r] = reasons.get(r, 0) + 1
    per_device, per_location = {}, {}
    for did, loc, kind in qs.values_list("device__device_id", "device__location", "kind"):
        per_device.setdefault(did, {}).setdefault(kind, 0); per_device[did][kind] += 1
        l = loc or "(no location)"; per_location.setdefault(l, {}).setdefault(kind, 0); per_location[l][kind] += 1
    per_day = {}
    for ts, kind in qs.values_list("received_at", "kind"):
        per_day.setdefault(ts.date().isoformat(), {}).setdefault(kind, 0); per_day[ts.date().isoformat()][kind] += 1
    return {"days": days, "since": since, "total": sum(kinds.values()), "kinds": kinds, "boot_reasons": reasons,
            "per_device": per_device, "per_location": per_location, "per_day": dict(sorted(per_day.items(), reverse=True))}
