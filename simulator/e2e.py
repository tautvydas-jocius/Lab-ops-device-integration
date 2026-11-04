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
time.sleep(1)
files = call(f"/devices/{DEV}/files/list/")[1]["files"]; names = {f["name"] for f in files}
check("csv (UTF-16, NULs) stored", "readings_utf16.csv" in names)
check("pdf stored", "report.pdf" in names)
check("fake pdf and .exe not stored", not ({"evil.pdf", "ignore.exe"} & names), sorted(names))
sys.exit(0 if ok else 1)
