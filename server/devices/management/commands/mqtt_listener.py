import json, logging, re, time
import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from devices.models import Device, DeviceCommand, DeviceEvent, DeviceHeartbeat, ParserSchema, Reading
from devices.parsing import parse
from devices.mqtt import publish_config, publish_ota_chunk
from devices.files import store_upload
from devices.diag_ingest import ingest_diag
from devices.transfer import Reassembler
log = logging.getLogger(__name__)
class Command(BaseCommand):
    help = "Consume device raw readings and status from MQTT"
    def handle(self, *args, **kwargs):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        def on_connect(client, userdata, flags, reason_code, properties):
            client.subscribe([(f"{settings.MQTT_PREFIX}/+/raw", 0), (f"{settings.MQTT_PREFIX}/+/status", 0), (f"{settings.MQTT_PREFIX}/+/ack", 1), (f"{settings.MQTT_PREFIX}/+/file/#", 0), (f"{settings.MQTT_PREFIX}/+/ota_req", 0), (f"{settings.MQTT_PREFIX}/+/diag", 0)])
            for d in Device.objects.all():
                try: publish_config(d)
                except Exception as e: log.warning("republish failed %s: %s", d.device_id, e)
        reasm = Reassembler(lambda ident: (Device.objects.filter(device_id=ident).values_list("command_token", flat=True).first()))
        def on_file(client, ident, xfer, part, payload):
            res = reasm.add(ident, xfer, part, payload)
            if not res: return
            status, xid, a, body = res
            if status == "ok":
                dev = Device.objects.filter(device_id=ident).first()
                f, err = store_upload(dev, a, body)
                ok, info = (True, f.name) if f else (False, err)
            else: ok, info = False, a
            log.info("file %s from %s: %s", "stored" if ok else "rejected", ident, info)
            client.publish(f"{settings.MQTT_PREFIX}/{ident}/file_ack", json.dumps({"xfer": int(xid) if str(xid).isdigit() else xid, "ok": ok, "info": info}), qos=1)
        def on_message(client, userdata, msg):
            fm = re.match(rf"^{re.escape(settings.MQTT_PREFIX)}/([^/]+)/file/(\d+)/(meta|\d+)$", msg.topic)
            if fm: on_file(client, fm.group(1), fm.group(2), fm.group(3), msg.payload); return
            match = re.match(rf"^{re.escape(settings.MQTT_PREFIX)}/([^/]+)/(raw|status|ack|ota_req|diag)$", msg.topic)
            if not match: return
            ident, kind = match.groups()
            try: payload = json.loads(msg.payload.decode())
            except Exception: log.warning("bad JSON on %s", msg.topic); return
            device = Device.objects.select_related("parser").filter(device_id=ident).first()
            if kind == "diag":
                if device: ingest_diag(device, payload)
                return
            if kind == "ota_req":
                try: publish_ota_chunk(client, ident, int(payload["image"]), int(payload["index"]))
                except (KeyError, ValueError, TypeError): log.warning("bad ota_req from %s", ident)
                return
            if kind == "ack":
                ok = bool(payload.get("ok"))
                DeviceCommand.objects.filter(pk=payload.get("id"), device__device_id=ident, status="pending").update(status="ok" if ok else "failed", result=str(payload.get("result", ""))[:1000], acked_at=timezone.now())
                return
            if kind == "status":
                if not device:
                    parser = ParserSchema.objects.first()
                    if not parser: return
                    device = Device.objects.create(device_id=ident, name=f"Pending {ident}", parser=parser, enabled=False)
                state = payload.get("state") == "online"
                Device.objects.filter(pk=device.pk).update(online=state, last_seen=timezone.now() if state else device.last_seen, applied_config_version=payload.get("config_version"), fw_version=str(payload.get("fw", ""))[:40] or device.fw_version)
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
