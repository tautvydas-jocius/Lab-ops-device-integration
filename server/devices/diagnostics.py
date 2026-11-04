"""Turns a device's diagnostics snapshot + recent events/heartbeats into findings and a 0-100 health score. Pure functions (testable)."""
from datetime import timedelta
from statistics import pstdev
from django.utils import timezone

def rssi_label(r):
    if r is None: return "unknown"
    return "good" if r >= -67 else "ok" if r >= -75 else "weak" if r >= -82 else "poor"

def assess(d, events=(), heartbeats=(), now=None):
    """events: DeviceEvent-like (kind, detail, received_at); heartbeats: rssi values (newest first). Returns {score, verdict, findings[]}."""
    now = now or timezone.now(); f = []; score = 100
    def add(sev, msg, advice, pen):
        nonlocal score; f.append({"severity": sev, "finding": msg, "advice": advice}); score -= pen
    c = d.diag_counters or {}; day = now - timedelta(hours=24)
    recent = [e for e in events if e.received_at >= day]
    if not d.diag_at: add("info", "No diagnostics received yet", "Flash firmware with diagnostics, or run the 'Request full diagnostics' action.", 10)
    if not d.online: add("critical", f"Offline (last seen {d.last_seen:%Y-%m-%d %H:%M} UTC)" if d.last_seen else "Offline, never seen", "Check power, Wi-Fi and the broker; see recent events for the last reboot or drop reason.", 40)
    lbl = rssi_label(d.rssi)
    if lbl == "poor": add("critical", f"Very weak Wi-Fi ({d.rssi} dBm)", "Move the device or add an access point; below -82 dBm drops and retransmits are expected.", 25)
    elif lbl == "weak": add("warn", f"Weak Wi-Fi ({d.rssi} dBm)", "Consider an access point closer to this location or an external antenna.", 12)
    vals = [h for h in heartbeats if h is not None][:120]
    if len(vals) >= 10 and pstdev(vals) > 8: add("warn", f"Unstable signal (sigma {pstdev(vals):.0f} dB over {len(vals)} heartbeats)", "Interference or a moving obstacle; try another channel or position.", 8)
    if c.get("brownout", 0): add("critical", f"{c['brownout']} brownout reset(s)", "Supply voltage dips: use a better PSU/USB cable, add a bulk capacitor, avoid sharing the supply with motors or relays.", 25)
    if c.get("panic", 0): add("critical", f"{c['panic']} firmware crash(es)", "Capture the serial backtrace over USB; update firmware via OTA once fixed.", 25)
    if c.get("wdt", 0): add("warn", f"{c['wdt']} watchdog reset(s)", "Code blocked too long (slow Wi-Fi/MQTT reconnect or a hung serial read).", 15)
    if c.get("poweron", 0) > 1: add("warn", f"Lost power {c['poweron'] - 1} time(s) since first boot", "Mains/USB power interruptions or a loose connector; consider a UPS or a locking connector.", 10)
    drops = [e for e in recent if e.kind == "wifi_drop"]
    if len(drops) > 3: add("warn", f"{len(drops)} Wi-Fi drops in 24 h", "Weak signal if RSSI is poor; otherwise suspect the AP (roaming, DHCP leases, band steering).", 12)
    mq = [e for e in recent if e.kind in ("mqtt_drop", "mqtt_fail")]
    if len(mq) > 3: add("warn", f"{len(mq)} MQTT failures/drops in 24 h while Wi-Fi was up" if len(drops) <= 3 else f"{len(mq)} MQTT failures/drops in 24 h", "Broker unreachable, restarting, or rejecting this client (ACL/limits); check Mosquitto logs.", 10)
    boots = [e for e in recent if e.kind == "boot"]
    if len(boots) > 3 or (d.uptime_s is not None and d.uptime_s < 600 and c.get("boots", 0) > 5): add("critical", "Reboot loop or frequent reboots", "See boot reasons in the event list; fix the dominant reason first.", 20)
    if d.min_free_heap is not None and d.min_free_heap < 20000: add("warn", f"Low memory (minimum free heap {d.min_free_heap // 1024} KB)", "Possible leak or oversized buffers; watch free heap in the heartbeat trend.", 10)
    score = max(0, score)
    worst = max((x["severity"] for x in f), key=lambda s: {"info": 0, "warn": 1, "critical": 2}[s], default=None)
    verdict = "healthy" if worst in (None, "info") else "needs attention" if worst == "warn" else "act now"
    return {"score": score, "verdict": verdict, "findings": f}

def snapshot(d, now=None):
    """Assessment from the DB for one device."""
    now = now or timezone.now()
    ev = list(d.events.filter(received_at__gte=now - timedelta(hours=24))[:200])
    hb = list(d.heartbeats.values_list("rssi", flat=True)[:120])
    return assess(d, ev, hb, now)
