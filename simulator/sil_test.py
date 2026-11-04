#!/usr/bin/env python3
"""Software-in-the-loop test: the REAL firmware C++ (src/main.cpp, usb_drop.h, real PubSubClient + ArduinoJson) runs on Linux in the `sil`
container against the real broker, listener and Django. Only the hardware is modelled (firmware/test/sil/shim). The USB "PC" is mtools,
an independent FAT implementation; OTA flashes a second real build through Django and re-execs it.
Usage: CONTROL_API_TOKEN=e2etoken123 python3 simulator/sil_test.py [section ...]   sections: boot cmd usb ota faults conc wifi nopsram nofix"""
import hashlib, json, os, re, subprocess, sys, tempfile, time, urllib.error, urllib.request
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); TOK = os.environ.get("CONTROL_API_TOKEN", "e2etoken123"); DEV = "bal-01"; BASE = "http://127.0.0.1:8011/api"
def sh(*a, env=None, inp=None, timeout=120):
    return subprocess.run(a, cwd=ROOT, capture_output=True, text=inp is None or isinstance(inp, str), input=inp, timeout=timeout, env={**os.environ, **(env or {})})
DC = ["docker", "compose", "--profile", "sil"]
def dj(code): return (sh(*DC, "exec", "-T", "web", "python", "manage.py", "shell", "-c", code).stdout.strip().splitlines()[-1:] or [""])[0]
def sil_sh(cmd): return sh(*DC, "exec", "-T", "sil", "sh", "-c", cmd).stdout
def ctl(line): sil_sh(f"echo '{line}' > /state/ctl")
def logs(): return sh(*DC, "logs", "--no-log-prefix", "sil").stdout
def call(path, body=None, tok=TOK):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(r, timeout=10) as x: return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e: return e.code, {}
def run(action, arg="", wait=12):
    code, c = call(f"/devices/{DEV}/actions/", {"action": action, "arg": arg}); assert code == 202, (action, code)
    st = c
    for _ in range(int(wait * 4)):
        time.sleep(.25); st = next((x for x in call(f"/devices/{DEV}/actions/")[1]["commands"] if x["id"] == c["id"]), st)
        if st["status"] != "pending": break
    return st
def diag(): return next(x for x in call("/diagnostics/")[1]["devices"] if x["device_id"] == DEV)
def until(cond, secs=25, what=None):
    d = None
    for _ in range(int(secs * 2)):
        d = diag()
        if cond(d): return d
        time.sleep(.5)
    return d
fails = []; npass = 0
def check(name, cond, info=""):
    global npass
    print(("PASS" if cond else "FAIL"), name, str(info)[:170], flush=True)
    if cond: npass += 1
    else: fails.append(name)
def back_online(secs=30):
    for _ in range(int(secs)):
        r = call(f"/devices/{DEV}/actions/", {"action": "ping"})[1]; time.sleep(1)
        st = next((x for x in call(f"/devices/{DEV}/actions/")[1]["commands"] if x["id"] == r.get("id")), {})
        if st.get("status") == "ok": return st
    return {}
def start(psram="1", binary="1.0.0"):
    tok = dj("from devices.models import Device; print(Device.objects.get(device_id='bal-01').command_token)")
    sh(*DC, "rm", "-sf", "sil"); sh(*DC, "up", "-d", "--force-recreate", "sil", env={"COMMAND_TOKEN": tok, "SIL_WIPE": "1", "SIL_PSRAM": psram, "SIL_BIN": binary}); return tok
def mq(code):  # run paho inside the web container (it has the dependency and is on the compose network)
    return dj("import json, time, paho.mqtt.client as M\nfrom devices.models import Device\nfrom devices.mqtt import sign\nd = Device.objects.get(device_id='bal-01')\ngot = []\nc = M.Client(M.CallbackAPIVersion.VERSION2)\nc.on_message = lambda *a: got.append(a[2].payload.decode())\nc.connect('mosquitto', 1883); c.subscribe('lab/devices/bal-01/ack', 1); c.loop_start(); time.sleep(.5)\n" + code + "\ntime.sleep(2.5); print(json.dumps(got))")

def sec_boot():
    d = until(lambda d: d["online"] and d["counters"].get("boots") == 1 and d["last_reset"] == "power_on")
    check("boot: real firmware online, boot #1, reset=power_on", d["online"] and d["last_reset"] == "power_on" and d["counters"]["boots"] == 1, d["counters"])
    check("boot: firmware version reported", d["fw_version"] == "1.0.0", d["fw_version"])
    dj("from devices.models import Device; d=Device.objects.get(device_id='bal-01'); d.enabled=True; d.baud=19200; d.poll_command='SI\\r\\n'; d.poll_interval_ms=500; d.save()")
    time.sleep(3)
    check("config: retained config applied (serial reopened at 19200)", "Serial1.begin baud=19200" in logs())
    check("config: ack visible in Django", dj("from devices.models import Device as D; d=D.objects.get(device_id='bal-01'); print(d.applied_config_version==d.config_version)") == "True")
    check("config: balance really polled over UART", "SI" in sil_sh("cat /state/uart_tx.log"))
    n0 = int(dj("from devices.models import Reading as R; print(R.objects.filter(device__device_id='bal-01', ok=True).count())")); time.sleep(3)
    n1 = int(dj("from devices.models import Reading as R; print(R.objects.filter(device__device_id='bal-01', ok=True).count())"))
    check("readings: poll replies parsed and stored by Django", n1 > n0, f"{n0}->{n1}")

def sec_cmd():
    r = run("ping"); check("cmd: ping signed by Django, verified by C++ HMAC", r["status"] == "ok" and "pong" in r["result"], r["result"])
    r = run("send_serial", "TARE\\r"); check("cmd: send_serial", r["status"] == "ok" and "TARE" in sil_sh("cat /state/uart_tx.log"), r["result"])
    r = run("poll_now"); check("cmd: poll_now", r["status"] == "ok", r["result"])
    r = run("diag"); check("cmd: diag", r["status"] == "ok", r["result"])
    out = mq("c.publish('lab/devices/bal-01/cmd', json.dumps({'id': 99999999, 'action': 'reboot', 'arg': '', 'sig': '00' * 32}), qos=1)")
    check("cmd: bad signature silently dropped", out == "[]", out)
    out = mq("c.publish('lab/devices/bal-01/cmd', json.dumps({'id': 1, 'action': 'reboot', 'arg': '', 'sig': sign(d.command_token, 1, 'reboot', '')}), qos=1)")
    check("cmd: validly signed replay of an old id refused", "replayed" in out, out)

def sec_wifi():
    r = run("set_wifi", json.dumps({"ssid": "nosuch", "pass": "x"})); check("wifi: set_wifi acked before reboot", r["status"] == "ok", r["result"])
    d = until(lambda d: d["last_reset"] == "wifi_trial_revert" and d["online"], 70); check("wifi: unreachable net reverted after the real 45 s trial, reason recorded", d["last_reset"] == "wifi_trial_revert", d["last_reset"])
    check("wifi: back on the old network", re.findall(r"wifi up ssid=(\S+)", logs())[-1] == "lab", re.findall(r"wifi up ssid=(\S+)", logs())[-3:])
    r = run("set_wifi", json.dumps({"ssid": "lab2", "pass": "lab2pass"})); time.sleep(4); st = back_online()
    check("wifi: reachable net committed", st.get("status") == "ok" and re.findall(r"wifi up ssid=(\S+)", logs())[-1] == "lab2", re.findall(r"wifi up ssid=(\S+)", logs())[-2:])
    ctl("fault power"); time.sleep(3); st = back_online(); check("wifi: committed creds survive a power cycle (read from NVS)", re.findall(r"wifi up ssid=(\S+)", logs())[-1] == "lab2", re.findall(r"wifi up ssid=(\S+)", logs())[-2:])
    r = run("set_wifi", json.dumps({"ssid": "corp", "pass": "s3cret", "eap_user": "alice@corp", "eap_identity": "anon@corp"})); time.sleep(4); st = back_online()
    check("wifi: WPA2-Enterprise path: identity, user, password passed to the EAP client", "mode=EAP user=alice@corp identity=anon@corp" in logs() and re.findall(r"wifi up ssid=(\S+)", logs())[-1] == "corp", re.findall(r"WiFi.begin .*", logs())[-1])
    hist = json.dumps(call(f"/devices/{DEV}/actions/")[1]); check("wifi: passwords never stored in history", "s3cret" not in hist and "lab2pass" not in hist)
    r = run("reset_config"); time.sleep(4); st = back_online(); check("reset_config: back, Wi-Fi kept", st.get("status") == "ok" and re.findall(r"wifi up ssid=(\S+)", logs())[-1] == "corp")
    d = until(lambda d: d["last_reset"] == "reset_config", 10); check("reset_config: reason recorded", d["last_reset"] == "reset_config", d["last_reset"])
    r = run("factory_reset"); time.sleep(4); st = back_online(); check("factory_reset: returns to secrets.h Wi-Fi", st.get("status") == "ok" and re.findall(r"wifi up ssid=(\S+)", logs())[-1] == "lab", re.findall(r"wifi up ssid=(\S+)", logs())[-1:])
    r = run("ping"); old = dj("from devices.models import DeviceCommand as C; print(C.objects.filter(device__device_id='bal-01').count())")
    out = mq("c.publish('lab/devices/bal-01/cmd', json.dumps({'id': 1, 'action': 'ping', 'arg': '', 'sig': sign(d.command_token, 1, 'ping', '')}), qos=1)")
    check("factory_reset: replay counter kept", "replayed" in out, out)

def upload_ok(name, data, expect_name=None, wait=20):
    """Host copies the file with mtools onto the drive; returns the stored row (or None)."""
    tmp = "/tmp/up_" + hashlib.md5(name.encode()).hexdigest(); sh(*DC, "exec", "-T", "sil", "sh", "-c", f"cat > {tmp}", inp=data)
    ctl(f"usb put {tmp} {name}"); sha = hashlib.sha256(data).hexdigest(); expect = expect_name or name
    for _ in range(wait * 2):
        time.sleep(.5)
        n = dj(f"from devices.models import UploadedFile as U; print(U.objects.filter(device__device_id='bal-01', sha256='{sha}', name='{expect}').count())")
        if n and n != "0": return int(n)
    return 0
def sec_usb():
    u0 = time.time()
    dj("from devices.models import UploadedFile as U; U.objects.filter(device__device_id='bal-01').delete()")
    check("usb: 8.3 name, bytes identical end to end", upload_ok("NOTES.TXT", b"batch 42 weighed\r\n"), "")
    check("usb: firmware's own FAT12 format passes fsck.vfat", sil_sh("cat /state/usb_last.txt").split()[:1] == ["0"], sil_sh("cat /state/fsck.txt")[:120])
    check("usb: long file name reassembled", upload_ok("readings 2026 batch-7.csv", b"a;b\n1;2\n" * 400, "readings_2026_batch-7.csv"))
    check("usb: UTF-16 CSV (NUL bytes) intact", upload_ok("utf16.csv", ("﻿a;b\n" + "1;2\n" * 3000).encode("utf-16")))
    check("usb: binary PDF intact", upload_ok("report.pdf", b"%PDF-1.4\n" + bytes(range(256)) * 400))
    check("usb: .xlsx intact", upload_ok("book.xlsx", b"PK\x03\x04" + os.urandom(90000)))
    check("usb: .xls intact", upload_ok("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + os.urandom(40000)))
    check("usb: edited file with the same name is re-sent", upload_ok("NOTES.TXT", b"batch 43 weighed, edited\r\n"))
    n = int(dj("from devices.models import UploadedFile as U; print(U.objects.filter(device__device_id='bal-01').count())")); ctl("usb del old.xls"); time.sleep(5)
    check("usb: unchanged files not re-sent on the next scan", int(dj("from devices.models import UploadedFile as U; print(U.objects.filter(device__device_id='bal-01').count())")) == n)
    check("usb: fragmented file (freed clusters reused) intact", upload_ok("frag.csv", b"x;y\n" * 30000))
    check("usb: 3 MB file", upload_ok("big.csv", b"1234567;8901234\n" * 196608, wait=60))
    tmp = "/tmp/up_exe"; sh(*DC, "exec", "-T", "sil", "sh", "-c", f"cat > {tmp}", inp=b"MZ"); ctl(f"usb put {tmp} tool.exe"); sh(*DC, "exec", "-T", "sil", "sh", "-c", "cat > /tmp/up_fake", inp=b"not really a pdf"); ctl("usb put /tmp/up_fake fake.pdf"); time.sleep(8)
    names = dj("from devices.models import UploadedFile as U; print(sorted(U.objects.filter(device__device_id='bal-01').values_list('name', flat=True)))")
    check("usb: .exe ignored, fake .pdf rejected by the server", "tool" not in names and "fake" not in names, names)

def sec_ota():
    binp = os.path.join(tempfile.mkdtemp(), "fw2.bin"); sh(*DC, "cp", "sil:/app/sil-2.0.0", binp); sh(*DC, "cp", binp, "web:/tmp/fw2.bin")
    v = dj("from django.core.files import File; from devices.models import FirmwareImage as F; F.objects.filter(version='2.0.0').delete(); i=F(); i.file.save('firmware.bin', File(open('/tmp/fw2.bin','rb')), save=False); i.save(); print(i.version, i.size)")
    check("ota: Django read the version out of the real binary's LABOPS_FW marker", v.startswith("2.0.0 "), v)
    r = run("ota", "v2.0.0", wait=120); check("ota: transferred over MQTT, SHA-256 verified by C++", r["status"] == "ok" and "flashed v2.0.0" in r["result"], r["result"])
    d = until(lambda d: d["fw_version"] == "2.0.0" and d["last_reset"] == "ota", 25); check("ota: the flashed binary actually booted and reports 2.0.0", d["fw_version"] == "2.0.0" and d["last_reset"] == "ota", (d["fw_version"], d["last_reset"]))
    check("ota: slot bytes equal the uploaded image", sil_sh("sha256sum /state/ota_boot.bin").split()[:1] == [hashlib.sha256(open(binp, "rb").read()).hexdigest()])
    pk = dj("from devices.models import FirmwareImage as F; i=F.objects.get(version='2.0.0'); F.objects.filter(pk=i.pk).update(sha256='0'*64); print(i.pk)")
    r = run("ota", "v2.0.0", wait=120); check("ota: wrong hash refused, nothing switched", r["status"] == "failed" and "sha256" in r["result"] and "Update.abort" in logs(), r["result"])
    st = back_online(); check("ota: firmware keeps running after the refused image", st.get("status") == "ok", st.get("result"))
    r = run("ota", "v0.0.0"); check("ota: unknown version refused by the server", r["status"] == "failed", r["result"])

def sec_faults():
    b = diag()["counters"]
    for f, key, reason in (("brownout", "brownout", "brownout"), ("panic", "panic", "panic"), ("watchdog", "wdt", "watchdog"), ("power", "poweron", "power_on")):
        ctl(f"fault {f}"); d = until(lambda d: d["counters"].get(key, 0) > b.get(key, 0) and d["last_reset"] == reason, 15)
        check(f"faults: {f} classified by esp_reset_reason logic and counted", d["counters"].get(key, 0) == b.get(key, 0) + 1 and d["last_reset"] == reason, (d["last_reset"], d["counters"].get(key)))
    d = diag(); check("faults: counters persisted across all those reboots", d["counters"]["boots"] >= b["boots"] + 4, d["counters"])
    r = run("reboot"); d = until(lambda d: d["last_reset"] == "cmd_reboot", 15); check("faults: commanded reboot told apart from faults", d["last_reset"] == "cmd_reboot", d["last_reset"])
    b = diag()["counters"]; ctl("wifi down 4"); d = until(lambda d: d["counters"].get("wifi_drops", 0) > b.get("wifi_drops", 0), 35); check("wifi drop: counted after reconnect", d["counters"].get("wifi_drops", 0) == b.get("wifi_drops", 0) + 1, d["counters"])
    ev = dj("from devices.models import DeviceEvent as E; print(E.objects.filter(device__device_id='bal-01', kind='wifi_drop').first().detail)"); check("wifi drop: reason code and outage length logged", "reason=200" in ev and "down=" in ev, ev)
    b = diag()["counters"]; ctl("mqtt block 7"); d = until(lambda d: d["counters"].get("mqtt_fails", 0) > b.get("mqtt_fails", 0), 40); check("mqtt outage: failed attempts counted", d["counters"].get("mqtt_fails", 0) > b.get("mqtt_fails", 0), d["counters"])
    ev = dj("from devices.models import DeviceEvent as E; print(E.objects.filter(device__device_id='bal-01', kind='mqtt_fail').first().detail)"); check("mqtt outage: state code logged", "last_state=" in ev, ev)
    ctl("wifi rssi -88"); r = run("diag"); d = until(lambda d: d["rssi"] == -88, 10); check("rssi: reported", d["rssi"] == -88, d["rssi"])
    v = sil_sh("cat /state/violations.log 2>/dev/null"); check("NVS rules: no begin-while-open, long key, or write-to-closed in the whole run", not v.strip(), v[:300])

def seq_gaps(since):
    rows = json.loads(dj(f"import json; from devices.models import Reading as R; from django.utils.dateparse import parse_datetime as P; print(json.dumps(list(R.objects.filter(device__device_id='bal-01', received_at__gte=P('{since}')).order_by('id').values_list('seq', flat=True))))") or "[]")
    return len(rows), sum(1 for a, b in zip(rows, rows[1:]) if b != a + 1 and b != 1)
def conc_run():
    """Balance streams while a 2 MB file and commands share the one firmware loop and MQTT link. Returns the measurements."""
    dj("from devices.models import Device; d=Device.objects.get(device_id='bal-01'); d.enabled=True; d.poll_command=''; d.save()"); time.sleep(2); ctl("stats"); time.sleep(.5)
    s0 = dict(x.split("=") for x in sil_sh("cat /state/stats.txt").split()); since = dj("from django.utils import timezone; print(timezone.now().isoformat())")
    ctl("uart rate 20"); time.sleep(3)
    data = os.urandom(2 * 1024 * 1024); tmp = "/tmp/up_conc"; sh(*DC, "exec", "-T", "sil", "sh", "-c", f"cat > {tmp}", inp=b"a;b\n" + data); ctl(f"usb put {tmp} conc.csv")
    time.sleep(3.2); rs = [run("ping", wait=25), run("diag", wait=25), run("ping", wait=25)]
    sha = hashlib.sha256(b"a;b\n" + data).hexdigest(); stored = 0
    for _ in range(60):
        stored = int(dj(f"from devices.models import UploadedFile as U; print(U.objects.filter(sha256='{sha}').count())") or 0)
        if stored: break
        time.sleep(1)
    time.sleep(3); ctl("uart rate 0"); time.sleep(2); ctl("stats"); time.sleep(.5)
    s1 = dict(x.split("=") for x in sil_sh("cat /state/stats.txt").split()); fed = int(s1["fed"]) - int(s0["fed"]); dmg = int(s1["damaged"]) - int(s0["damaged"]); n, gaps = seq_gaps(since)
    return stored, rs, fed, dmg, n, gaps
def sec_conc():
    stored, rs, fed, dmg, n, gaps = conc_run()
    check("concurrency: 2 MB upload delivered intact while the balance streams", stored == 1)
    check("concurrency: commands answered during the upload", all(r["status"] == "ok" for r in rs), [(r["status"], r.get("took_s")) for r in rs])
    check("concurrency: no UART overflow in the real C++ loop", dmg == 0, f"fed={fed} damaged={dmg}")
    check("concurrency: Django stored every line, seq contiguous", gaps == 0 and n == fed, f"stored={n} fed={fed} seq_gaps={gaps}")
def sec_nofix():
    start(binary="nofix"); time.sleep(6); until(lambda d: d["online"] and d["counters"].get("boots") == 1, 20)
    stored, rs, fed, dmg, n, gaps = conc_run()
    check("negative control: with the fix removed the same test sees lost balance lines", dmg > 0 and n < fed, f"fed={fed} lost_in_uart={dmg} stored={n}")

def sec_nopsram():
    start("0"); time.sleep(6); d = until(lambda d: d["online"] and d["counters"].get("boots") == 1, 20)
    check("no PSRAM: 192 KB drive enumerates", "USB MSC drive 192 KB" in logs())
    check("no PSRAM: CSV through the small drive", upload_ok("small.csv", b"a;b\n" * 8000)); check("no PSRAM: its FAT12 passes fsck.vfat", sil_sh("cat /state/usb_last.txt").split()[:1] == ["0"], sil_sh("cat /state/fsck.txt")[:120])

ALL = ["boot", "cmd", "usb", "ota", "faults", "conc", "wifi", "nopsram", "nofix"]
secs = sys.argv[1:] or ALL
if "--no-restart" in secs: secs.remove("--no-restart")
else: start(); time.sleep(5)
for s in secs:
    print(f"--- {s}", flush=True); globals()["sec_" + s]()
print(f"\n{npass} passed, {len(fails)} failed"); [print(" FAIL:", f) for f in fails]
sys.exit(1 if fails else 0)
