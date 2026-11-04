import hashlib, hmac, json
import paho.mqtt.publish as publish
from django.conf import settings

def config_payload(device):
    return {"config_version": device.config_version, "enabled": device.enabled, "baud": device.baud, "data_bits": device.data_bits, "parity": device.parity, "stop_bits": device.stop_bits, "terminator": device.terminator, "poll_command": device.poll_command, "poll_interval_ms": device.poll_interval_ms}

def publish_config(device):
    publish.single(f"{settings.MQTT_PREFIX}/{device.device_id}/config", json.dumps(config_payload(device)), hostname=settings.MQTT_HOST, port=settings.MQTT_PORT, retain=True)

def sign(token, cmd_id, action, arg):
    return hmac.new(token.encode(), f"{cmd_id}|{action}|{arg}".encode(), hashlib.sha256).hexdigest()

def publish_command(cmd):
    d = cmd.device
    payload = {"id": cmd.pk, "action": cmd.action, "arg": cmd.arg, "sig": sign(d.command_token, cmd.pk, cmd.action, cmd.arg)}
    publish.single(f"{settings.MQTT_PREFIX}/{d.device_id}/cmd", json.dumps(payload), hostname=settings.MQTT_HOST, port=settings.MQTT_PORT, qos=1)

def send_command(device, action, arg="", by=""):
    from .models import DeviceCommand
    cmd = DeviceCommand.objects.create(device=device, action=action, arg=arg, created_by=by)
    try: publish_command(cmd)
    except Exception as exc:
        cmd.status, cmd.result = "failed", f"publish failed: {exc}"; cmd.save(update_fields=["status", "result"])
    return cmd
