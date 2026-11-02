#!/usr/bin/env python3
import argparse, json, random, time
import paho.mqtt.client as mqtt

FORMATS = {
    "mt-sics": lambda x, stable, junk: "garbage noise" if junk else f"S {'S' if stable else 'D'}      {x:0.4f} g",
    "sartorius-sbi": lambda x, stable, junk: "overload" if junk else f"N     {'+' if x >= 0 else '-'}   {abs(x):0.3f} {'g' if stable else ''}  ",
    "ohaus": lambda x, stable, junk: "ERR" if junk else f"    {x:0.3f} g    {'?' if not stable else ''}",
}
class Scale:
    def __init__(self, ident, kind, host, port, prefix):
        self.ident, self.kind, self.host, self.port, self.prefix = ident, kind, host, port, prefix
        self.config = {"enabled": False, "poll_interval_ms": 1000, "config_version": 0}
        self.seq = 0; self.start = time.monotonic(); self.last_emit = 0
        self.raw = f"{prefix}/{ident}/raw"; self.status = f"{prefix}/{ident}/status"; self.config_topic = f"{prefix}/{ident}/config"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{ident}")
        will = json.dumps({"state": "offline", "config_version": 0, "fw": "1.0.0"})
        self.client.will_set(self.status, will, qos=1, retain=True)
        self.client.on_connect = self.connected; self.client.on_message = self.message
    def connected(self, client, userdata, flags, reason_code, properties):
        client.subscribe(self.config_topic)
        self.status_update()
    def status_update(self):
        self.client.publish(self.status, json.dumps({"state": "online", "config_version": self.config["config_version"], "fw": "1.0.0"}), qos=1, retain=True)
    def message(self, client, userdata, msg):
        try: self.config = json.loads(msg.payload)
        except Exception: return
        print(f"{self.ident}: applied config_version={self.config.get('config_version')} enabled={self.config.get('enabled')} poll_interval_ms={self.config.get('poll_interval_ms')}", flush=True)
        self.status_update()
    def run(self):
        self.client.connect(self.host, self.port, 60); self.client.loop_start()
        self.status_update()
        try:
            while True:
                now = time.monotonic()
                if self.config.get("enabled") and now - self.last_emit >= max(0.1, self.config.get("poll_interval_ms", 1000) / 1000):
                    self.last_emit = now; self.seq += 1
                    weight = 12.3456 + 0.0008 * (now - self.start) + random.uniform(-0.001, 0.001)
                    line = FORMATS[self.kind](weight, random.random() > 0.18, random.random() < 0.025)
                    self.client.publish(self.raw, json.dumps({"line": line, "seq": self.seq, "uptime_ms": int((now - self.start) * 1000)}))
                time.sleep(0.05)
        except KeyboardInterrupt: pass
        finally:
            self.client.publish(self.status, json.dumps({"state": "offline", "config_version": self.config.get("config_version", 0), "fw": "1.0.0"}), qos=1, retain=True); self.client.loop_stop(); self.client.disconnect()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--devices", default="bal-01:mt-sics,bal-02:sartorius-sbi,bal-03:ohaus"); ap.add_argument("--host", default="localhost"); ap.add_argument("--port", type=int, default=1883); ap.add_argument("--prefix", default="lab/devices")
    args = ap.parse_args()
    scales = [Scale(*item.split(":"), args.host, args.port, args.prefix) for item in args.devices.split(",")]
    # Each MQTT client needs its own loop; the shared process runs independent scale workers.
    import threading
    for scale in scales: threading.Thread(target=scale.run, daemon=True).start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: pass
if __name__ == "__main__": main()
