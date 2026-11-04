#!/usr/bin/env python3
"""End-to-end checks against a running stack (server + listener + broker) and a running esp_emulator. stdlib only."""
import json, sys, time, urllib.request, urllib.error
BASE, DEV, TOK = "http://127.0.0.1:8011/api", "bal-01", sys.argv[1] if len(sys.argv) > 1 else "e2etoken123"
def call(path, body=None, tok=TOK):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(r, timeout=10) as x: return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e: return e.code, {}
def run(action, arg="", wait=8):
    code, c = call(f"/devices/{DEV}/actions/", {"action": action, "arg": arg}); assert code == 202, (action, code)
    for _ in range(int(wait * 4)):
        time.sleep(.25); st = next(x for x in call(f"/devices/{DEV}/actions/")[1]["commands"] if x["id"] == c["id"])
        if st["status"] != "pending": return st
    return st
ok = True
def check(name, cond, info=""):
    global ok; ok &= bool(cond); print(("PASS" if cond else "FAIL"), name, info, flush=True)
check("api rejects bad token", call(f"/devices/{DEV}/actions/", {"action": "ping"}, "nope")[0] == 401)
r = run("ping"); check("ping acked by MCU", r["status"] == "ok", r["result"])
r = run("poll_now"); check("poll_now", r["status"] == "ok", r["result"])
r = run("set_wifi", json.dumps({"ssid": "lab2", "pass": "lab2pass"})); check("set_wifi to unreachable net acked", r["status"] == "ok", f"history arg={r['arg']!r}")
check("wifi password not stored", "lab2pass" not in json.dumps(call(f"/devices/{DEV}/actions/")[1]))
time.sleep(10); r = run("ping"); check("MCU rolled back to old Wi-Fi and answers again", r["status"] == "ok", r["result"])
r = run("set_wifi", json.dumps({"ssid": "lab", "pass": "labpass"})); time.sleep(4); r = run("ping"); check("set_wifi to reachable net commits", r["status"] == "ok", r["result"])
r = run("reset_config"); time.sleep(4); r = run("ping"); check("reset_config then MCU back online", r["status"] == "ok", r["result"])
r = run("factory_reset"); time.sleep(4); r = run("ping"); check("factory_reset then MCU back online", r["status"] == "ok", r["result"])
# OTA: upload an image through Django (as the admin would), flash it, and tamper-check
import subprocess, os
def dj(code): return (subprocess.run(["docker", "compose", "exec", "-T", "web", "python", "manage.py", "shell", "-c", code], capture_output=True, text=True, cwd=os.path.join(os.path.dirname(__file__), "..")).stdout.strip().splitlines()[-1:] or [""])[0]
img = dj("from django.core.files.base import ContentFile as C; from devices.models import FirmwareImage as F; import os; F.objects.filter(version='9.9.9').delete(); i=F(version='9.9.9'); i.file.save('fw.bin', C(os.urandom(70000)), save=False); i.save(); print(i.pk)")
r = run("ota", img, wait=30); check("ota flashed (17 chunks, sha256 verified)", r["status"] == "ok", r["result"])
time.sleep(3); r = run("ping"); check("MCU back after ota reboot", r["status"] == "ok")
check("django shows new fw_version", dj("from devices.models import Device; print(Device.objects.get(device_id='bal-01').fw_version)") == "9.9.9")
dj(f"from devices.models import FirmwareImage as F; F.objects.filter(pk={img}).update(sha256='0'*64)")  # corrupt hash => MCU must refuse
r = run("ota", img, wait=30); check("ota with wrong sha256 refused", r["status"] == "failed", r["result"])
# OTA by version string; version auto-read from the binary's LABOPS_FW marker (blank version on upload)
dj("from django.core.files.base import ContentFile as C; from devices.models import FirmwareImage as F; F.objects.filter(version='2.3.4').delete(); import os; i=F(); i.file.save('fw.bin', C(os.urandom(9000)+b'LABOPS_FW=2.3.4\\x00'+os.urandom(9000)), save=False); i.save()")
r = run("ota", "v2.3.4", wait=30); check("ota by version 'v2.3.4' (auto-versioned from binary)", r["status"] == "ok", r["result"])
time.sleep(3); check("device now reports 2.3.4", dj("from devices.models import Device; print(Device.objects.get(device_id='bal-01').fw_version)") == "2.3.4")
r = run("ota", "v0.0.1", wait=5); check("unknown version refused", r["status"] == "failed", r["result"])
fw = call("/firmware/")[1]["firmware"]; check("firmware browse API lists versions", {"2.3.4", "9.9.9"} <= {f["version"] for f in fw}, [f["version"] for f in fw])
time.sleep(1)
files = call(f"/devices/{DEV}/files/list/")[1]["files"]; names = {f["name"] for f in files}
check("csv (UTF-16, NULs) stored", "readings_utf16.csv" in names)
check("pdf stored", "report.pdf" in names)
check("excel stored", {"book.xlsx", "old.xls"} <= names, sorted(names))
check("fake xlsx, fake pdf and .exe not stored", not ({"evil.pdf", "ignore.exe", "fake.xlsx"} & names), sorted(names))
# ---- diagnostics: inject faults into the emulated MCU and check what Django learns (NVS persistence across reboots included)
def chaos(what, n=3):
    dj(f"import json, paho.mqtt.publish as P; P.single('lab/devices/{DEV}/emu', json.dumps({{'do': '{what}', 'n': {n}}}), hostname='mosquitto')")
def diag():
    return next(x for x in call("/diagnostics/")[1]["devices"] if x["device_id"] == DEV)
def wait(cond, secs=20):
    for _ in range(int(secs * 4)):
        time.sleep(.25); d = diag()
        if cond(d): return d
    return diag()
b0 = diag()["counters"]["boots"]
chaos("brownout"); d = wait(lambda d: d["counters"]["brownout"] >= 1); check("brownout reboot recorded (counter + last_reset)", d["last_reset"] == "brownout" and d["counters"]["brownout"] == 1, d["counters"])
check("counters persisted across reboot (boots incremented, earlier power_on kept)", d["counters"]["boots"] == b0 + 1 and d["counters"]["poweron"] >= 1, d["counters"])
chaos("panic"); d = wait(lambda d: d["counters"]["panic"] >= 1); check("crash (panic) recorded", d["last_reset"] == "panic")
chaos("power_off"); d = wait(lambda d: d["counters"]["poweron"] >= 2); check("power loss recorded (power_on again)", d["counters"]["poweron"] >= 2 and any("Lost power" in f["finding"] for f in d["findings"]), [f["finding"] for f in d["findings"]])
r = run("reboot"); d = wait(lambda d: d["last_reset"] == "cmd_reboot"); check("commanded reboot is told apart from faults", d["last_reset"] == "cmd_reboot", d["last_reset"])
chaos("wifi_drop", 3); d = wait(lambda d: d["counters"]["wifi_drops"] >= 1, 30); check("wifi drop counted with duration event", d["counters"]["wifi_drops"] == 1)
chaos("mqtt_fail", 4); d = wait(lambda d: d["counters"]["mqtt_fails"] >= 1, 30); check("mqtt failure counted", d["counters"]["mqtt_fails"] >= 1)
chaos("rssi", -88); d = wait(lambda d: d["rssi"] == -88); check("rssi poor flagged with advice", d["rssi_label"] == "poor" and any("Very weak Wi-Fi" in f["finding"] for f in d["findings"]))
ev = dj(f"from devices.models import DeviceEvent as E; print(','.join(sorted(set(E.objects.filter(device__device_id='{DEV}').values_list('kind', flat=True)))))")
check("event history has all kinds", {"boot", "wifi_drop", "mqtt_fail"} <= set(ev.split(",")), ev)
check("score drops and verdict set", d["score"] < 100 and d["verdict"] in ("needs attention", "act now"), (d["score"], d["verdict"]))
r = run("diag"); check("diag command acked", r["status"] == "ok", r["result"])
order = [x["device_id"] for x in call("/diagnostics/?sort=rssi")[1]["devices"]]; check("sort by rssi API works", order[0] == DEV, order)
sys.exit(0 if ok else 1)
