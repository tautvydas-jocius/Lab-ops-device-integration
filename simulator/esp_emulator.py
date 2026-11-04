#!/usr/bin/env python3
"""ESP32-S3 firmware emulator: speaks the same MQTT protocol as firmware/src/main.cpp (config, status+LWT, HMAC-signed cmd/ack,
NVS-backed Wi-Fi with trial/rollback, resets, binary file chunks) so the server can be tested end to end without hardware.
NVS is a JSON file (survives emulated reboots). Files placed in --drop are treated as the USB drop drive contents.
NOT emulated: the C++ FAT12 parser and real radio; those need the hardware or a PlatformIO build."""
import argparse, hashlib, hmac, json, os, random, sys, threading, time
import paho.mqtt.client as mqtt

FW0 = "1.0.0"; TRIAL_S = 6; DIAG_S = 3  # firmware heartbeat is 30 s; shortened for tests  # firmware uses 45 s; shortened so tests run fast
ap = argparse.ArgumentParser()
ap.add_argument("--id", default="bal-01"); ap.add_argument("--token", required=True, help="device command token from Django admin")
ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=1884); ap.add_argument("--prefix", default="lab/devices")
ap.add_argument("--state", default=".emu"); ap.add_argument("--drop", default=".emu/drop")
ap.add_argument("--wifi", default="lab:labpass", help="comma list ssid:pass of networks the emulated radio can join (secrets.h default is the first)")
ap.add_argument("--idle", type=float, default=2.0)
A = ap.parse_args()
os.makedirs(A.drop, exist_ok=True); os.makedirs(A.state, exist_ok=True)
NVS = os.path.join(A.state, f"{A.id}.nvs.json")
KNOWN = dict(x.split(":", 1) for x in A.wifi.split(","))
DEFAULT_WIFI = next(iter(KNOWN.items()))
T = lambda s: f"{A.prefix}/{A.id}/{s}"
def log(*a): print(time.strftime("%H:%M:%S"), f"[{A.id}]", *a, flush=True)
def nvs_load():
    try: return json.load(open(NVS))
    except Exception: return {}
def nvs_save(d): json.dump(d, open(NVS, "w"))
def hmac_hex(msg): return hmac.new(A.token.encode(), msg.encode(), hashlib.sha256).hexdigest()

class Mcu:
    def __init__(self):
        self.next_reason = None; self.lock = threading.Lock(); self.ota_evt = threading.Event(); self.ota_chunk = None; self.ack_evt = {}; self.sent = {}; self.stop = False
        self.boot()
    def boot(self):
        """Emulates power-up: load NVS, pick Wi-Fi (pending creds => trial), build MQTT client."""
        self.boot_t = time.monotonic(); self.rssi = -58; self.diag_full = True
        n = nvs_load(); self.nvs = n; self.fw = n.get("fw", FW0)
        d = n.setdefault("diag", {"c": {k: 0 for k in ("boots", "poweron", "brownout", "panic", "wdt", "sw", "wifi_drops", "mqtt_fails")}, "evn": 0, "ring": {}, "intent": "", "upck": 0})
        d["c"]["boots"] += 1; self.boot_no = d["c"]["boots"]; self.bid = "%08x" % random.getrandbits(32)
        why = self.next_reason or ("sw:" + d["intent"] if d["intent"] else "power_on" if self.boot_no == 1 else "sw_unknown")
        if why.startswith("sw:"): kind = why[3:]; d["c"]["sw"] += 1
        else:
            kind = why; key = {"power_on": "poweron", "brownout": "brownout", "panic": "panic", "watchdog": "wdt"}.get(kind)
            if key: d["c"][key] += 1
        prev_up = d["upck"]; d["intent"] = ""; d["upck"] = 0; self.next_reason = None; self.last_reset = kind; self.wifi_down_at = None
        self.event("boot", f"{kind} prev_uptime={prev_up}s", d); self.save()
        self.cfg = {"enabled": True, "poll_interval_ms": 1000, "config_version": n.get("version", 0)}
        self.cfg.update(n.get("cfg", {})); self.last_cmd = n.get("cmdid", 0)
        w = n.get("wifi", {}); p = w.get("pending")
        self.trial = bool(p); cred = p or w.get("active") or {"ssid": DEFAULT_WIFI[0], "pass": DEFAULT_WIFI[1]}
        self.cred = cred; self.boot_t = time.monotonic(); self.seq = 0; self.last_emit = 0
        self.wifi_ok = KNOWN.get(cred["ssid"]) == cred["pass"]
        log(f"boot: wifi ssid={cred['ssid']} {'(TRIAL) ' if self.trial else ''}radio={'up' if self.wifi_ok else 'cannot join'} cmdid={self.last_cmd} cfg_v={self.cfg['config_version']}")
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"emu-{A.id}-{random.randrange(1<<16)}")
        self.client.will_set(T("status"), json.dumps({"state": "offline", "config_version": self.cfg["config_version"], "fw": self.fw}), qos=1, retain=True)
        self.client.on_connect = self.on_connect; self.client.on_message = self.on_message
        self.mqtt_up = False
        if self.wifi_ok: self.client.connect(A.host, A.port, 60); self.client.loop_start()
    def event(self, kind, detail, d=None):
        d = d or self.nvs["diag"]; d["evn"] += 1; n = d["evn"]
        d["ring"][str(n % 16)] = f"{n}|{self.bid}|{self.boot_no}|{int(time.monotonic() - self.boot_t) if hasattr(self, 'boot_t') else 0}|{kind}|{detail}"[:160]
        self.diag_full = True; log(f"event {kind}: {detail}")
    def publish_diag(self):
        d = self.nvs["diag"]; up = int(time.monotonic() - self.boot_t)
        msg = {"up": up, "rssi": self.rssi, "heap": 210000 - random.randrange(0, 4000), "minheap": 180000, "boot": self.boot_no, "reason": self.last_reset, "fw": self.fw, "ch": 6, "c": d["c"]}
        if self.diag_full: msg["ev"] = list(d["ring"].values())
        self.client.publish(T("diag"), json.dumps(msg), retain=True); self.diag_full = False
    def save(self, **kw):
        self.nvs.update(kw); nvs_save(self.nvs)
    def status(self): self.client.publish(T("status"), json.dumps({"state": "online", "config_version": self.cfg["config_version"], "fw": self.fw}), qos=1, retain=True)
    def on_connect(self, c, u, f, rc, p):
        self.mqtt_up = True
        for t in ("config", "cmd", "file_ack"): c.subscribe(T(t), 1)
        c.subscribe(T("ota/#")); c.subscribe(T("emu"))
        if self.trial:
            w = self.nvs.get("wifi", {}); w["active"] = w.pop("pending"); self.save(wifi=w); self.trial = False; log("trial Wi-Fi + MQTT ok -> committed")
        self.status(); log("mqtt connected")
    def ack(self, id_, ok, result): self.client.publish(T("ack"), json.dumps({"id": id_, "ok": ok, "result": result})); log(f"ack id={id_} ok={ok} {result}")
    def reboot(self, why):
        log(f"REBOOT ({why})"); self.nvs["diag"]["intent"] = why; self.nvs["diag"]["upck"] = int(time.monotonic() - self.boot_t); self.save(); self.client.loop_stop(); self.client.disconnect(); time.sleep(1); self.boot()
    def on_message(self, c, u, msg):
        if msg.topic.startswith(T("ota/")):
            self.ota_chunk = (int(msg.topic.rsplit("/", 1)[1]), bytes(msg.payload)); self.ota_evt.set(); return
        try: d = json.loads(msg.payload)
        except Exception: return
        if msg.topic == T("file_ack"):
            e = self.ack_evt.get(d.get("xfer"))
            if e: e["ok"] = d.get("ok"); e["info"] = d.get("info"); e["evt"].set()
        elif msg.topic == T("config"):
            self.cfg.update({k: d[k] for k in ("enabled", "poll_interval_ms", "config_version") if k in d}); self.save(version=self.cfg["config_version"], cfg=self.cfg)
            log(f"config applied v{self.cfg['config_version']} enabled={self.cfg['enabled']} poll={self.cfg['poll_interval_ms']}"); self.status()
        elif msg.topic == T("emu"): threading.Thread(target=self.chaos, args=(d,), daemon=True).start()
        elif msg.topic == T("cmd"): threading.Thread(target=self.command, args=(d,), daemon=True).start()
    def command(self, d):
        id_, action, arg, sig = d.get("id", 0), d.get("action", ""), d.get("arg", ""), d.get("sig", "")
        if not hmac.compare_digest(hmac_hex(f"{id_}|{action}|{arg}"), str(sig)): log(f"cmd {id_} {action}: BAD SIGNATURE, dropped silently"); return
        if id_ <= self.last_cmd: return self.ack(id_, False, "replayed or stale id")
        self.last_cmd = id_; self.save(cmdid=id_)
        if action == "ping": self.ack(id_, True, f"pong fw={self.fw} up={int(time.monotonic()-self.boot_t)}s rssi=-52")
        elif action == "poll_now": self.ack(id_, True, "polled")
        elif action == "send_serial": self.ack(id_, True, f"sent {len(arg)} bytes")
        elif action == "reboot": self.ack(id_, True, "rebooting"); time.sleep(.2); self.reboot("cmd_reboot")
        elif action == "diag": self.diag_full = True; self.publish_diag(); self.ack(id_, True, "diagnostics published")
        elif action == "ota": self.ota(id_, arg)
        elif action == "set_wifi":
            try: w = json.loads(arg); assert w["ssid"] and len(w["ssid"]) <= 32
            except Exception: return self.ack(id_, False, 'arg must be {"ssid":..,"pass":..[,"eap_user":..]}')
            nv = self.nvs.get("wifi", {}); nv["pending"] = {"ssid": w["ssid"], "pass": w.get("pass", ""), "eap_user": w.get("eap_user", "")}; self.save(wifi=nv)
            self.ack(id_, True, f"stored pending creds, rebooting; reverts in {TRIAL_S}s if MQTT does not connect"); time.sleep(.2); self.reboot("set_wifi")
        elif action in ("reset_config", "factory_reset"):
            keep = {"cmdid": self.last_cmd, "diag": self.nvs["diag"], "fw": self.fw}
            if action == "reset_config": keep["wifi"] = self.nvs.get("wifi", {})
            self.nvs = keep; nvs_save(keep); self.ack(id_, True, "cleared, rebooting"); time.sleep(.2); self.reboot(action)
        else: self.ack(id_, False, "unknown action")
    def chaos(self, d):
        """Test-only fault injection (topic <id>/emu): brownout|panic|watchdog|power_off|wifi_drop(secs)|mqtt_fail(secs)|rssi(value)."""
        what, n = d.get("do"), d.get("n", 3); log(f"CHAOS {what} {n}")
        if what in ("brownout", "panic", "watchdog", "power_off"): self.next_reason = "power_on" if what == "power_off" else what; self.reboot(f"chaos_{what}")
        elif what == "rssi": self.rssi = int(n)
        elif what in ("wifi_drop", "mqtt_fail"):
            dc = self.nvs["diag"]; self.client.loop_stop(); self.client.disconnect(); self.mqtt_up = False; t0 = time.monotonic(); time.sleep(n)
            self.client.connect(A.host, A.port, 60); self.client.loop_start(); dt = time.monotonic() - t0
            if what == "wifi_drop": dc["c"]["wifi_drops"] += 1; self.event("wifi_drop", f"reason=200 down={dt:.1f}s")
            else: dc["c"]["mqtt_fails"] += max(1, int(n // 2)); self.event("mqtt_fail", f"fails={max(1, int(n // 2))} last_state=-2 down={dt:.1f}s")
            self.save()
    def ota(self, id_, arg):
        """Mirrors firmware otaRun(): pull 4 KB chunks over MQTT, hash while 'writing', switch version only if SHA-256 matches the signed arg."""
        try: o = json.loads(arg); img, size, ver, sha = int(o["image"]), int(o["size"]), o["version"], o["sha256"]; assert size and len(sha) == 64
        except Exception: return self.ack(id_, False, "ota arg needs image, size, sha256")
        t0 = time.monotonic(); h = hashlib.sha256(); chunks = (size + 4095) // 4096; log(f"OTA start image={img} v{ver} {size} B in {chunks} chunks")
        for i in range(chunks):
            for attempt in range(4):
                self.ota_evt.clear(); self.client.publish(T("ota_req"), json.dumps({"image": img, "index": i}))
                if self.ota_evt.wait(4) and self.ota_chunk and self.ota_chunk[0] == i: break
            else: return self.ack(id_, False, f"ota timeout at chunk {i}")
            data = self.ota_chunk[1]
            if len(data) != min(4096, size - i * 4096): return self.ack(id_, False, f"ota chunk {i} has wrong length")
            h.update(data)
        if h.hexdigest() != sha: return self.ack(id_, False, "sha256 mismatch, image discarded")
        self.save(fw=ver); dt = time.monotonic() - t0; self.ack(id_, True, f"flashed v{ver} in {dt:.1f}s ({size/1024/dt:.1f} KB/s), rebooting"); time.sleep(.3); self.reboot("ota")
    def send_file(self, path):
        name = os.path.basename(path); data = open(path, "rb").read(); sha = hashlib.sha256(data).hexdigest()
        CH = 4096; chunks = (len(data) + CH - 1) // CH; xfer = random.randrange(1, 1 << 31)
        meta = {"xfer": xfer, "name": name, "size": len(data), "chunks": chunks, "sha256": sha, "sig": hmac_hex(f"{xfer}|{name}|{len(data)}|{sha}")}
        e = self.ack_evt[xfer] = {"evt": threading.Event()}; base = T(f"file/{xfer}")
        self.client.publish(base + "/meta", json.dumps(meta))
        for i in range(chunks): self.client.publish(f"{base}/{i}", data[i*CH:(i+1)*CH]); time.sleep(.003)
        if e["evt"].wait(15): return 1 if e["ok"] else 2
        return 0
    def drop_scan(self):
        for f in sorted(os.listdir(A.drop)):
            p = os.path.join(A.drop, f)
            if not os.path.isfile(p) or not f.lower().endswith((".csv", ".pdf", ".txt", ".xlsx", ".xls")) or not os.path.getsize(p): continue
            crc = hashlib.md5(open(p, "rb").read()).hexdigest()
            if self.sent.get(f) == crc: continue
            r = self.send_file(p); log(f"drop {f} ({os.path.getsize(p)} B) -> {['no ack, will retry', 'stored', 'rejected by server'][r]}")
            if r: self.sent[f] = crc
    def run(self):
        last_scan = 0
        while not self.stop:
            now = time.monotonic()
            if self.trial and now - self.boot_t > TRIAL_S:  # firmware: abandonTrial()
                w = self.nvs.get("wifi", {}); w.pop("pending", None); self.save(wifi=w); log("trial FAILED -> reverting to previous Wi-Fi"); self.client.loop_stop(); self.nvs["diag"]["intent"] = "wifi_trial_revert"; self.save(); self.boot(); continue
            if self.mqtt_up and now - getattr(self, 'last_diag', 0) > DIAG_S: self.last_diag = now; self.publish_diag()
            if self.mqtt_up and self.cfg.get("enabled") and now - self.last_emit >= max(.1, self.cfg["poll_interval_ms"] / 1000):
                self.last_emit = now; self.seq += 1
                self.client.publish(T("raw"), json.dumps({"line": f"S S      {12.3456 + random.uniform(-.001, .001):0.4f} g", "seq": self.seq, "uptime_ms": int((now - self.boot_t) * 1000)}))
            if self.mqtt_up and now - last_scan > A.idle:
                last_scan = now
                try:
                    if time.time() - max([os.path.getmtime(os.path.join(A.drop, f)) for f in os.listdir(A.drop)] or [0]) > A.idle: self.drop_scan()
                except Exception as ex: log("drop scan error", ex)
            time.sleep(.05)
try: Mcu().run()
except KeyboardInterrupt: pass
