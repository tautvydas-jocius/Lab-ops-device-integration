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

    def test_file_upload_token_and_validation(self):
        import tempfile
        d = Device.objects.create(device_id="f1", name="F", parser=self.mt)
        url = "/api/devices/f1/files/"; ok = {"Authorization": f"Bearer {d.command_token}"}
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            self.assertEqual(self.client.post(url + "?name=a.csv", b"a,b\n1,2\n", content_type="application/octet-stream").status_code, 401)
            self.assertEqual(self.client.post(url + "?name=a.exe", b"x", content_type="application/octet-stream", headers=ok).status_code, 415)
            self.assertEqual(self.client.post(url + "?name=a.pdf", b"not pdf", content_type="application/octet-stream", headers=ok).status_code, 415)
            r = self.client.post(url + "?name=../a%20b.csv", b"a,b\n1,2\n", content_type="application/octet-stream", headers=ok)
            self.assertEqual(r.status_code, 201); self.assertEqual(r.json()["name"], "a_b.csv")
            self.assertEqual(self.client.post(url + "?name=r.pdf", b"%PDF-1.4 x", content_type="application/octet-stream", headers=ok).status_code, 201)
        self.assertEqual(d.files.count(), 2)
