import hashlib, hmac, time
from .mqtt import sign_file
MAX_TRANSFERS = 8
TTL = 60  # seconds an incomplete transfer is kept

class Reassembler:
    """Collects binary file chunks published by an MCU over MQTT: <prefix>/<dev>/file/<xfer>/meta (JSON) and /<seq> (raw bytes).
    add() returns None until a transfer is complete, then ("ok", xfer, name, bytes) or ("error", xfer, reason, None)."""
    def __init__(self, token_for): self.token_for, self.t = token_for, {}
    def _gc(self):
        now = time.monotonic()
        for k in [k for k, v in self.t.items() if now - v["at"] > TTL]: del self.t[k]
        while len(self.t) > MAX_TRANSFERS: del self.t[min(self.t, key=lambda k: self.t[k]["at"])]
    def add(self, device_id, xfer, part, payload):
        self._gc()
        x = self.t.setdefault((device_id, xfer), {"at": time.monotonic(), "meta": None, "chunks": {}})
        if part == "meta":
            import json
            try: x["meta"] = json.loads(payload)
            except ValueError: del self.t[(device_id, xfer)]; return ("error", xfer, "bad meta", None)
        elif part.isdigit(): x["chunks"][int(part)] = bytes(payload)
        else: return None
        m = x["meta"]
        if not m or len(x["chunks"]) < int(m.get("chunks", 1 << 30)): return None
        del self.t[(device_id, xfer)]
        token = self.token_for(device_id)
        want = sign_file(token or "", m.get("xfer"), m.get("name", ""), m.get("size"), m.get("sha256", ""))
        if not token or not hmac.compare_digest(want, str(m.get("sig", ""))): return ("error", xfer, "bad signature", None)
        body = b"".join(x["chunks"].get(i, b"") for i in range(int(m["chunks"])))
        if len(body) != int(m.get("size", -1)) or hashlib.sha256(body).hexdigest() != m.get("sha256"): return ("error", xfer, "size/sha256 mismatch", None)
        return ("ok", xfer, m.get("name", ""), body)
