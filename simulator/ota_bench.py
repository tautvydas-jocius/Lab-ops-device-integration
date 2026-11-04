#!/usr/bin/env python3
"""Times OTA end to end (command -> MCU ack) for several image sizes. Usage: ota_bench.py [token]. Needs the stack + an MCU/emulator running."""
import json, os, subprocess, sys, time, urllib.request
TOK = sys.argv[1] if len(sys.argv) > 1 else "e2etoken123"; B = "http://127.0.0.1:8011/api/devices/bal-01/actions/"
def dj(code): return (subprocess.run(["docker", "compose", "exec", "-T", "web", "python", "manage.py", "shell", "-c", code], capture_output=True, text=True, cwd=os.path.join(os.path.dirname(__file__), "..")).stdout.strip().splitlines()[-1:] or [""])[0]
def api(body=None):
    r = urllib.request.Request(B, data=json.dumps(body).encode() if body else None, headers={"Authorization": f"Bearer {TOK}"})
    return json.loads(urllib.request.urlopen(r, timeout=10).read())
print(f"{'size':>9} {'chunks':>6} {'took (s)':>9} {'KB/s':>7}  MCU says")
for kb in (64, 256, 1024, 1536):
    v = f"bench-{kb}k"; i = dj(f"from django.core.files.base import ContentFile as C; from devices.models import FirmwareImage as F; import os; F.objects.filter(version='{v}').delete(); i=F(version='{v}'); i.file.save('fw.bin', C(os.urandom({kb}*1024)), save=False); i.save(); print(i.pk)")
    c = api({"action": "ota", "arg": v}); t0 = time.time()
    while True:
        time.sleep(.2); st = next(x for x in api()["commands"] if x["id"] == c["id"])
        if st["status"] != "pending" or time.time() - t0 > 120: break
    print(f"{kb:>7}KB {(kb*1024+4095)//4096:>6} {st['took_s']!s:>9} {kb/st['took_s']:>7.0f}  {st['result']}" if st["took_s"] else st); time.sleep(3)
