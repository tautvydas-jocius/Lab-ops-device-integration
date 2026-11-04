from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from unittest.mock import patch
from .models import Device, ParserSchema, Reading
from .parsing import parse
from .mqtt import config_payload

class DeviceIntegrationTests(TestCase):
    def setUp(self):
        self.mt = ParserSchema.objects.create(name="mt", version=1, pattern=r"^S\s+(?P<status>S|D)\s+(?P<value>[+-]?\d+(?:\.\d+)?)\s+(?P<unit>\S+)$", stable_group="status", stable_rule="equals", stable_values="S")
        self.sart = ParserSchema.objects.create(name="sart", version=1, pattern=r"^(?P<status>[A-Z])\s*(?P<sign>[+-])\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>\S*)\s*$", stable_group="unit", stable_rule="present")
        self.ohaus = ParserSchema.objects.create(name="ohaus", version=1, pattern=r"^\s*(?P<value>[+-]?\d+(?:\.\d+)?)\s+(?P<unit>\S+)\s*(?P<unstable>\?)?\s*$", stable_group="unstable", stable_rule="absent")
    def test_mt_stable_dynamic_and_garbage(self):
        self.assertTrue(parse("S S      12.3456 g\r\n", self.mt)["stable"])
        self.assertFalse(parse("S D      12.3456 g", self.mt)["stable"])
        self.assertFalse(parse("garbage", self.mt)["ok"])
    def test_sartorius_sign_stable_unstable(self):
        good = parse("N     +   12.345 g  ", self.sart)
        self.assertEqual(str(good["value"]), "12.345"); self.assertTrue(good["stable"])
        bad = parse("N     -   12.345   ", self.sart)
        self.assertEqual(str(bad["value"]), "-12.345"); self.assertFalse(bad["stable"])
    def test_ohaus_stable_unstable_and_sign(self):
        self.assertTrue(parse("    12.345 g    ", self.ohaus)["stable"])
        self.assertFalse(parse("    -12.345 g    ?", self.ohaus)["stable"])
    @patch("devices.mqtt.publish_config")
    def test_config_version_and_payload(self, publish):
        d = Device.objects.create(device_id="bal-test", name="Test", parser=self.mt)
        self.assertEqual(config_payload(d)["config_version"], 1)
        d.poll_interval_ms = 2500; d.save()
        self.assertEqual(d.config_version, 2)
        self.assertEqual(config_payload(d)["poll_interval_ms"], 2500)
    def test_referenced_schema_immutable(self):
        d = Device.objects.create(device_id="bal-test", name="Test", parser=self.mt)
        Reading.objects.create(device=d, parser=self.mt, raw_line="S S 1 g", ok=True)
        self.mt.pattern = r"different"
        with self.assertRaisesMessage(ValidationError, "create a new version"):
            self.mt.save()
    @patch("devices.mqtt.publish_config")
    def test_dashboard_and_overview_view(self, publish):
        d = Device.objects.create(device_id="bal-test", name="Test", parser=self.mt)
        Reading.objects.create(device=d, parser=self.mt, raw_line="S S 12.3456 g", ok=True, value=12.3456, unit="g", stable=True)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lab-Ops Device Gateway")
        self.assertContains(response, "bal-test")

        overview_resp = self.client.get("/api/overview/")
        self.assertEqual(overview_resp.status_code, 200)
        data = overview_resp.json()
        self.assertEqual(data["stats"]["total_devices"], 1)
        self.assertEqual(data["devices"][0]["device_id"], "bal-test")
        self.assertEqual(data["devices"][0]["latest_reading"]["value"], "12.345600")

    def test_bad_pattern_does_not_raise(self):
        bad = ParserSchema.objects.create(name="bad", version=1, pattern="(")
        self.assertFalse(parse("x", bad)["ok"])
    def test_stalled_flag(self):
        from datetime import timedelta
        from django.utils import timezone
        from .views import is_stalled
        d = Device.objects.create(device_id="s1", name="S", parser=self.mt, online=True, last_seen=timezone.now())
        self.assertFalse(is_stalled(d))
        d.last_seen = timezone.now() - timedelta(minutes=5)
        self.assertTrue(is_stalled(d))

    @patch("devices.mqtt.publish.single")
    def test_command_is_signed(self, single):
        import json
        from .mqtt import send_command, sign
        d = Device.objects.create(device_id="c1", name="C", parser=self.mt)
        cmd = send_command(d, "ping", "", "t")
        body = json.loads(single.call_args.args[1])
        self.assertEqual(body["sig"], sign(d.command_token, cmd.pk, "ping", ""))
        self.assertNotIn(d.command_token, single.call_args.args[1])
    @patch("devices.mqtt.publish.single")
    def test_actions_api_token(self, single):
        d = Device.objects.create(device_id="c2", name="C", parser=self.mt)
        url = "/api/devices/c2/actions/"
        with override_settings(CONTROL_API_TOKEN=""):
            self.assertEqual(self.client.post(url, "{}", content_type="application/json").status_code, 403)
        with override_settings(CONTROL_API_TOKEN="s3cret"):
            self.assertEqual(self.client.post(url, "{}", content_type="application/json", headers={"Authorization": "Bearer nope"}).status_code, 401)
            ok = {"Authorization": "Bearer s3cret"}
            self.assertEqual(self.client.post(url, '{"action":"bogus"}', content_type="application/json", headers=ok).status_code, 400)
            self.assertEqual(self.client.post(url, '{"action":"ping"}', content_type="application/json", headers=ok).status_code, 202)
        self.assertEqual(d.commands.count(), 1)

    @patch("devices.mqtt.publish.single")
    def test_set_wifi_password_not_stored(self, single):
        import json
        from .mqtt import send_command
        d = Device.objects.create(device_id="w1", name="W", parser=self.mt)
        cmd = send_command(d, "set_wifi", json.dumps({"ssid": "lab", "pass": "hunter2"}), "t")
        cmd.refresh_from_db()
        self.assertEqual(cmd.arg, "ssid=lab")
        self.assertIn("hunter2", single.call_args.args[1])

    def test_mqtt_file_transfer(self):
        import hashlib, json, tempfile
        from .files import store_upload
        from .mqtt import sign_file
        from .transfer import Reassembler
        d = Device.objects.create(device_id="f1", name="F", parser=self.mt)
        body = b"a,b\n" + b"1,2\n" * 2000
        sha = hashlib.sha256(body).hexdigest(); chunks = [body[i:i + 4096] for i in range(0, len(body), 4096)]
        def meta(token):
            return json.dumps({"xfer": 7, "name": "my data.csv", "size": len(body), "chunks": len(chunks), "sha256": sha, "sig": sign_file(token, 7, "my data.csv", len(body), sha)}).encode()
        def run(token):
            r = Reassembler(lambda ident: d.command_token); res = r.add("f1", "7", "meta", meta(token))
            for i in reversed(range(len(chunks))): res = r.add("f1", "7", str(i), chunks[i]) or res  # out-of-order chunks
            return res
        self.assertEqual(run("wrong-token")[0], "error")
        res = run(d.command_token); self.assertEqual(res[0], "ok")
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            f, err = store_upload(d, res[2], res[3]); self.assertIsNone(err); self.assertEqual(f.name, "my_data.csv")
            self.assertIsNotNone(store_upload(d, "x.exe", b"x")[1]); self.assertIsNone(store_upload(d, "t.xlsx", b"PK\x03\x04zz")[1]); self.assertIsNone(store_upload(d, "o.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1zz")[1]); self.assertIsNone(store_upload(d, "n.txt", b"hello")[1]); self.assertIsNotNone(store_upload(d, "fake.xlsx", b"text")[1]); self.assertIsNotNone(store_upload(d, "x.pdf", b"nope")[1])
        self.assertEqual(d.files.count(), 4)

    @patch("devices.mqtt.publish.single")
    def test_ota_command_signs_image_hash(self, single):
        import json, tempfile
        from django.core.files.base import ContentFile
        from .models import FirmwareImage
        from .mqtt import send_command, sign
        d = Device.objects.create(device_id="o1", name="O", parser=self.mt)
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            img = FirmwareImage(version="1.1.0"); img.file.save("fw.bin", ContentFile(b"\xe9" * 10000), save=False); img.save()
            self.assertEqual((img.size, len(img.sha256)), (10000, 64))
            cmd = send_command(d, "ota", "", "t"); cmd.refresh_from_db()
            body = json.loads(single.call_args.args[1]); arg = json.loads(body["arg"])
            self.assertEqual((arg["sha256"], arg["size"], cmd.arg), (img.sha256, 10000, f"image {img.pk} v1.1.0"))
            self.assertEqual(body["sig"], sign(d.command_token, cmd.pk, "ota", body["arg"]))
            from .mqtt import publish_ota_chunk
            sent = []
            class C:  # records publishes
                def publish(self, t, data, qos=0): sent.append((t, len(data)))
            publish_ota_chunk(C(), "o1", img.pk, 2); publish_ota_chunk(C(), "o1", img.pk, 3)
            self.assertEqual(sent, [(f"lab/devices/o1/ota/{img.pk}/2", 1808), (f"lab/devices/o1/ota/{img.pk}/3", 0)])
        self.assertEqual(send_command(d, "ota", "999", "t").status, "failed")

    def test_firmware_autoversion_and_lookup(self):
        import tempfile
        from django.core.files.base import ContentFile
        from .models import FirmwareImage
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            def up(data, version=""):
                i = FirmwareImage(version=version); i.file.save("f.bin", ContentFile(data), save=False); i.save(); return i
            a = up(b"\xe9junk\x00LABOPS_FW=1.2.0\x00more" * 3)
            b = up(b"no marker here")
            c = up(b"x", version="2.0.0-rc1")
            self.assertEqual(a.version, "1.2.0"); self.assertTrue(b.version.startswith("auto-")); self.assertEqual(c.version, "2.0.0-rc1")
            self.assertEqual(FirmwareImage.find("v1.2.0"), a); self.assertEqual(FirmwareImage.find("1.2.0"), a)
            self.assertEqual(FirmwareImage.find(str(b.pk)), b); self.assertEqual(FirmwareImage.find(""), c); self.assertIsNone(FirmwareImage.find("9.9"))

    def test_diagnostics_ingest_and_aggregate(self):
        from .diag_ingest import ingest_diag
        from .diagnostics import aggregate, rssi_label
        from .models import DeviceEvent
        d = Device.objects.create(device_id="g1", name="G", location="Lab A", parser=self.mt, online=True)
        msg = {"up": 120, "rssi": -85, "heap": 150000, "minheap": 15000, "boot": 7, "reason": "brownout",
               "c": {"boots": 7, "poweron": 3, "brownout": 2, "panic": 0, "wdt": 0, "sw": 2, "wifi_drops": 5, "mqtt_fails": 0},
               "ev": ["1|aa|1|0|boot|power_on prev_uptime=0s", "2|aa|1|60|wifi_drop|reason=200 down=3.0s", "3|bb|2|0|boot|brownout prev_uptime=61s", "4|bb|2|9|boot|brownout prev_uptime=9s"]}
        self.assertEqual(ingest_diag(d, msg), 4); self.assertEqual(ingest_diag(d, msg), 0)  # replayed ring is de-duplicated
        d.refresh_from_db(); self.assertEqual((d.rssi, d.last_reset, d.boot_no), (-85, "brownout", 7)); self.assertEqual(rssi_label(-85), "poor")
        a = aggregate(DeviceEvent.objects.all(), 1)
        self.assertEqual((a["total"], a["kinds"], a["boot_reasons"]), (4, {"boot": 3, "wifi_drop": 1}, {"power_on": 1, "brownout": 2}))
        self.assertEqual(a["per_location"]["Lab A"]["boot"], 3); self.assertEqual(a["per_device"]["g1"]["wifi_drop"], 1)
