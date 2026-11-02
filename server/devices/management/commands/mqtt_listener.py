import json, logging, re, time
import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from devices.models import Device, ParserSchema, Reading
from devices.parsing import parse
from devices.mqtt import publish_config
log = logging.getLogger(__name__)
class Command(BaseCommand):
    help = "Consume device raw readings and status from MQTT"
    def handle(self, *args, **kwargs):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        def on_connect(client, userdata, flags, reason_code, properties):
            client.subscribe([(f"{settings.MQTT_PREFIX}/+/raw", 0), (f"{settings.MQTT_PREFIX}/+/status", 0)])
            for d in Device.objects.all():
                try: publish_config(d)
                except Exception as e: log.warning("republish failed %s: %s", d.device_id, e)
        def on_message(client, userdata, msg):
            match = re.match(rf"^{re.escape(settings.MQTT_PREFIX)}/([^/]+)/(raw|status)$", msg.topic)
            if not match: return
            ident, kind = match.groups()
            try: payload = json.loads(msg.payload.decode())
            except Exception: log.warning("bad JSON on %s", msg.topic); return
            device = Device.objects.select_related("parser").filter(device_id=ident).first()
            if kind == "status":
                if not device:
                    parser = ParserSchema.objects.first()
                    if not parser: return
                    device = Device.objects.create(device_id=ident, name=f"Pending {ident}", parser=parser, enabled=False)
                state = payload.get("state") == "online"
                Device.objects.filter(pk=device.pk).update(online=state, last_seen=timezone.now() if state else device.last_seen, applied_config_version=payload.get("config_version"))
                return
            if not device:
                parser = ParserSchema.objects.first()
                if not parser: return
                device = Device.objects.create(device_id=ident, name=f"Pending {ident}", parser=parser, enabled=False)
            result = parse(str(payload.get("line", "")), device.parser)
            Reading.objects.create(device=device, parser=device.parser, seq=payload.get("seq"), raw_line=str(payload.get("line", "")), **result)
            Device.objects.filter(pk=device.pk).update(online=True, last_seen=timezone.now())
        client.on_connect = on_connect; client.on_message = on_message
        while True:
            try:
                client.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60); client.loop_forever()
            except Exception as exc:
                log.warning("MQTT connection failed: %s", exc); time.sleep(3)
