import json
import paho.mqtt.publish as publish
from django.conf import settings

def config_payload(device):
    return {"config_version": device.config_version, "enabled": device.enabled, "baud": device.baud, "data_bits": device.data_bits, "parity": device.parity, "stop_bits": device.stop_bits, "terminator": device.terminator, "poll_command": device.poll_command, "poll_interval_ms": device.poll_interval_ms}

def publish_config(device):
    publish.single(f"{settings.MQTT_PREFIX}/{device.device_id}/config", json.dumps(config_payload(device)), hostname=settings.MQTT_HOST, port=settings.MQTT_PORT, retain=True)
