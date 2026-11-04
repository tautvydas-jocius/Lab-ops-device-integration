#!/usr/bin/env python3
"""Does the MCU keep up with the balance while a file upload, an OTA, commands and diagnostics all hit the same MQTT link?
Runs the emulator's UART model twice (Arduino-default 256 B RX buffer without pump, vs firmware's 2 KB buffer + pumpSerial())
and counts balance lines lost in the UART, plus what Django stored. Needs the stack up. Usage: concurrency_test.py [api token]"""
import json, os, re, subprocess, sys, time, urllib.request
TOK = sys.argv[1] if len(sys.argv) > 1 else "e2etoken123"; ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
def dj(code): return (subprocess.run(["docker", "compose", "exec", "-T", "web", "python", "manage.py", "shell", "-c", code], capture_output=True, text=True, cwd=ROOT).stdout.strip().splitlines()[-1:] or [""])[0]
def api(dev, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:8011/api/devices/{dev}/actions/", data=json.dumps(body).encode() if body else None, headers={"Authorization": f"Bearer {TOK}"})
    return json.loads(urllib.request.urlopen(r, timeout=10).read())
def run(dev, label, rx, pump):
    token = dj(f"from devices.models import Device; d=Device.objects.get(device_id='{dev}'); d.enabled=True; d.poll_interval_ms=1000; d.save(); print(d.command_token)")
    state = os.path.join(ROOT, f".emu-{dev}"); os.makedirs(state + "/drop", exist_ok=True)
    for f in os.listdir(state + "/drop"): os.remove(os.path.join(state, "drop", f))
    for f in os.listdir(state):
        if f.endswith(".json"): os.remove(os.path.join(state, f))
    img = dj("from django.core.files.base import ContentFile as C; from devices.models import FirmwareImage as F; import os; F.objects.filter(version='conc-1m').delete(); i=F(version='conc-1m'); i.file.save('fw.bin', C(os.urandom(1024*1024)), save=False); i.save(); print(i.pk)")
    t_start = dj("from django.utils import timezone; print(timezone.now().isoformat())")
    log = open(os.path.join(state, "emu.log"), "w")
    cmd = ["uv", "run", "--with", "paho-mqtt", "python", "simulator/esp_emulator.py", "--id", dev, "--token", token, "--state", state, "--drop", state + "/drop", "--idle", "0.5", "--balance-hz", "20", "--rx-buffer", str(rx)] + (["--pump"] if pump else [])
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    try:
        time.sleep(8)  # baseline: balance streaming alone
        open(state + "/drop/big.csv", "wb").write(b"a;b\n" + b"1234567;8901234\n" * 135000)  # ~2 MB
        time.sleep(1.0); ids = [api(dev, {"action": "ota", "arg": "conc-1m"})["id"]] + [api(dev, {"action": a})["id"] for a in ("ping", "diag", "ping")]  # all while the upload is in flight
        deadline = time.time() + 60; res = {}
        while time.time() < deadline and len(res) < 4:
            time.sleep(.5)
            for c in api(dev)["commands"]:
                if c["id"] in ids and c["status"] != "pending": res[c["id"]] = c["status"]
        time.sleep(6)
    finally:
        p.terminate(); p.wait(); log.close()
    stats = re.findall(r"uart: generated=(\d+) published=(\d+) lost=(\d+)", open(state + "/emu.log").read()); g, pub, lost = map(int, stats[-1]) if stats else (0, 0, 0)
    stored = int(dj(f"from devices.models import Reading as R; from django.utils.dateparse import parse_datetime as P; print(R.objects.filter(device__device_id='{dev}', received_at__gte=P('{t_start}')).count())"))
    big = dj(f"from devices.models import UploadedFile as U; print(U.objects.filter(device__device_id='{dev}', name='big.csv').count())")
    print(f"{label:<34} generated={g:>4} published={pub:>4} LOST={lost:>4} ({100*lost/max(g,1):.0f}%)  stored_by_django={stored:>4}  file_stored={big}  commands={sorted(res.values())}")
    return lost
print("balance 20 lines/s (480 B/s) during: 2 MB file upload + 1 MB OTA + 2 pings + diag, same MQTT link")
a = run("bal-02", "A: 256 B buffer, no pump (naive)", 256, False)
b = run("bal-03", "B: 2 KB buffer + pump (firmware)", 2048, True)
sys.exit(0 if b == 0 else 1)
