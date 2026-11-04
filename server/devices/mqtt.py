import hashlib, hmac, json
import paho.mqtt.publish as publish
from django.conf import settings

def config_payload(device):
    return {"config_version": device.config_version, "enabled": device.enabled, "baud": device.baud, "data_bits": device.data_bits, "parity": device.parity, "stop_bits": device.stop_bits, "terminator": device.terminator, "poll_command": device.poll_command, "poll_interval_ms": device.poll_interval_ms}

def publish_config(device):
    publish.single(f"{settings.MQTT_PREFIX}/{device.device_id}/config", json.dumps(config_payload(device)), hostname=settings.MQTT_HOST, port=settings.MQTT_PORT, retain=True)

def sign(token, cmd_id, action, arg):
    return hmac.new(token.encode(), f"{cmd_id}|{action}|{arg}".encode(), hashlib.sha256).hexdigest()

def publish_command(cmd, arg=None):
    d = cmd.device
    arg = cmd.arg if arg is None else arg
    payload = {"id": cmd.pk, "action": cmd.action, "arg": arg, "sig": sign(d.command_token, cmd.pk, cmd.action, arg)}
    publish.single(f"{settings.MQTT_PREFIX}/{d.device_id}/cmd", json.dumps(payload), hostname=settings.MQTT_HOST, port=settings.MQTT_PORT, qos=1)

def send_command(device, action, arg="", by=""):
    from .models import DeviceCommand
    stored = arg
    if action == "set_wifi":  # never persist the Wi-Fi password: keep only the SSID in history
        try: w = json.loads(arg); stored = "ssid=" + str(w["ssid"]) + (" eap=" + str(w["eap_user"]) if w.get("eap_user") else "")
        except (ValueError, KeyError, TypeError): stored = "(invalid)"
    cmd = DeviceCommand.objects.create(device=device, action=action, arg=stored, created_by=by)
    try: publish_command(cmd, arg)
    except Exception as exc:
        cmd.status, cmd.result = "failed", f"publish failed: {exc}"; cmd.save(update_fields=["status", "result"])
    return cmd

def sign_file(token, xfer, name, size, sha256):
    return hmac.new(token.encode(), f"{xfer}|{name}|{size}|{sha256}".encode(), hashlib.sha256).hexdigest()
